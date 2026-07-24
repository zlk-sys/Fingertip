# coding: utf-8
"""Coding mode interface.

In coding mode, the ring button controls a programming assistant:
  - Single-click  -> send "继续" + Enter (continue the conversation)
  - Double-click  -> launch the pre-configured assistant (claude/qoder)
"""
import subprocess
import sys

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (ScrollArea, FluentIcon, TitleLabel, BodyLabel,
                            StrongBodyLabel, CaptionLabel, SubtitleLabel,
                            SimpleCardWidget, TogglePushButton, IconWidget,
                            ComboBox, InfoBar, InfoBarPosition)
from qfluentwidgets import FluentIcon as FIF

from ..common.style_sheet import StyleSheet
from ..common.signal_bus import signalBus
from ..common.keyboard_simulator import send_text, press_enter
from ..common.config import cfg
from ..sdk.ring_sound import SensorCommand


def _get_shared_client():
    """Return the current shared BLE client, or None if not connected."""
    from . import connect_interface
    return connect_interface.shared_client


class InstructionCard(SimpleCardWidget):
    """Card showing a single control instruction."""

    def __init__(self, icon, action_text, key_text, parent=None):
        super().__init__(parent)
        self.setBorderRadius(10)
        self.setFixedHeight(76)

        self.iconWidget = IconWidget(icon, self)
        self.iconWidget.setFixedSize(18, 18)

        self.actionLabel = StrongBodyLabel(action_text, self)
        self.keyLabel = CaptionLabel(key_text, self)
        self.keyLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(20, 16, 20, 16)
        self.hBoxLayout.setSpacing(16)
        self.hBoxLayout.addWidget(self.iconWidget)
        self.hBoxLayout.addSpacing(4)

        self.textLayout = QVBoxLayout()
        self.textLayout.setSpacing(4)
        self.textLayout.addWidget(self.actionLabel)
        self.textLayout.addWidget(self.keyLabel)
        self.textLayout.addStretch(1)
        self.hBoxLayout.addLayout(self.textLayout)
        self.hBoxLayout.addStretch(1)


class CodingInterface(ScrollArea):
    """Coding mode interface."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        # State
        self._active = False
        self._handlers_registered = False

        # Title
        self.titleLabel = TitleLabel('Coding 模式', self.view)
        self.subtitleLabel = BodyLabel('使用戒指按钮控制编程助手', self.view)

        # Status card
        self.statusCard = SimpleCardWidget(self.view)
        self.statusCard.setBorderRadius(12)
        self.statusCard.setFixedHeight(80)

        self.statusLabel = StrongBodyLabel('Coding 模式未开启', self.statusCard)
        self.statusLabel.setObjectName('codingStatusLabel')
        self.statusLabel.setProperty('active', False)
        self.connectionHint = CaptionLabel('请先连接戒指设备', self.statusCard)
        self.connectionHint.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.toggleBtn = TogglePushButton('开启 Coding 模式', self.statusCard)
        self.toggleBtn.setFixedWidth(180)
        self.toggleBtn.setEnabled(False)

        self._buildStatusCard()

        # Settings card
        self.settingsSection = SubtitleLabel('编程助手设置', self.view)
        self.settingsCard = SimpleCardWidget(self.view)
        self.settingsCard.setBorderRadius(12)
        self.settingsCard.setFixedHeight(72)

        self.assistantLabel = BodyLabel('选择编程助手', self.settingsCard)
        self.assistantCombo = ComboBox(self.settingsCard)
        self.assistantCombo.addItems(['claude', 'qoder'])
        self.assistantCombo.setCurrentText(cfg.get(cfg.codingAssistant))
        self.assistantCombo.currentTextChanged.connect(self.__onAssistantChanged)

        self.settingsLayout = QHBoxLayout(self.settingsCard)
        self.settingsLayout.setContentsMargins(20, 16, 20, 16)
        self.settingsLayout.addWidget(self.assistantLabel)
        self.settingsLayout.addStretch(1)
        self.settingsLayout.addWidget(self.assistantCombo)

        # Instruction cards section
        self.instructionSection = SubtitleLabel('操作说明', self.view)

        self.continueCard = InstructionCard(
            FIF.PLAY_SOLID,
            '单击戒指按钮',
            '发送「继续」并回车（继续对话）',
            self.view
        )
        self.launchCard = InstructionCard(
            FIF.CODE,
            '双击戒指按钮',
            '打开编程助手（claude/qoder）',
            self.view
        )

        self.__initWidget()

    def _buildStatusCard(self):
        """Layout the status card contents."""
        cardLayout = QHBoxLayout(self.statusCard)
        cardLayout.setContentsMargins(20, 16, 20, 16)
        cardLayout.setSpacing(16)

        textLayout = QVBoxLayout()
        textLayout.setSpacing(4)
        textLayout.addWidget(self.statusLabel)
        textLayout.addWidget(self.connectionHint)
        textLayout.addStretch(1)
        cardLayout.addLayout(textLayout)
        cardLayout.addStretch(1)
        cardLayout.addWidget(self.toggleBtn, 0, Qt.AlignVCenter)

    def __initWidget(self):
        self.setObjectName('codingInterface')
        self.view.setObjectName('view')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        StyleSheet.CODING_INTERFACE.apply(self)

        self.vBoxLayout.setContentsMargins(36, 24, 36, 36)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.statusCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.settingsSection)
        self.vBoxLayout.addWidget(self.settingsCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.instructionSection)
        self.vBoxLayout.addWidget(self.continueCard)
        self.vBoxLayout.addWidget(self.launchCard)
        self.vBoxLayout.addStretch(1)

        # Signals
        self.toggleBtn.toggled.connect(self.__onToggleMode)
        signalBus.deviceConnected.connect(self.__onDeviceConnected)
        signalBus.deviceDisconnected.connect(self.__onDeviceDisconnected)
        signalBus.modeStarted.connect(self.__onOtherModeStarted)

        # Initial state check
        if _get_shared_client() is not None:
            self.__onDeviceConnected('', '')

    # ── Mode toggle ───────────────────────────────────────────────

    def __onToggleMode(self, checked: bool):
        """Start or stop coding mode."""
        if checked:
            self.__startCodingMode()
        else:
            self.__stopCodingMode()

    def __startCodingMode(self):
        client = _get_shared_client()
        if client is None:
            self.toggleBtn.setChecked(False)
            InfoBar.warning('未连接设备', '请先在「连接戒指」页面连接戒指',
                            parent=self.window(), duration=3000,
                            position=InfoBarPosition.TOP_RIGHT)
            return

        self._active = True
        self._register_handlers(client)
        signalBus.modeStarted.emit('coding')
        self.statusLabel.setText('Coding 模式已开启 - 监听中')
        self.statusLabel.setProperty('active', True)
        self.toggleBtn.setText('关闭 Coding 模式')

        # Refresh stylesheet for property change
        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)

        InfoBar.success('Coding 模式已开启',
                        '单击=继续 | 双击=打开助手',
                        parent=self.window(), duration=2000,
                        position=InfoBarPosition.TOP_RIGHT)

    def __stopCodingMode(self):
        client = _get_shared_client()
        if client is not None and self._handlers_registered:
            self._unregister_handlers(client)

        self._active = False
        self.statusLabel.setText('Coding 模式未开启')
        self.statusLabel.setProperty('active', False)
        self.toggleBtn.setText('开启 Coding 模式')

        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)
        signalBus.modeStopped.emit('coding')

    def __onOtherModeStarted(self, mode: str):
        """Auto-stop coding mode when another mode starts."""
        if mode == 'coding' or not self._active:
            return
        self.toggleBtn.setChecked(False)  # triggers __stopCodingMode
        InfoBar.info('Coding 模式已自动关闭', '已开启其他模式，Coding 模式自动退出',
                     parent=self.window(), duration=2000,
                     position=InfoBarPosition.TOP_RIGHT)

    # ── Packet handlers ───────────────────────────────────────────

    def _register_handlers(self, client):
        """Register ring button event handlers on the BLE client."""
        if self._handlers_registered:
            return
        client.add_packet_handler(SensorCommand.KEY_SINGLE_PRESS, self._on_single_press)
        client.add_packet_handler(SensorCommand.KEY_DOUBLE_PRESS, self._on_double_press)
        self._handlers_registered = True

    def _unregister_handlers(self, client):
        """Remove ring button event handlers from the BLE client."""
        if not self._handlers_registered:
            return
        try:
            client.remove_packet_handler(SensorCommand.KEY_SINGLE_PRESS, self._on_single_press)
        except (ValueError, KeyError):
            pass
        try:
            client.remove_packet_handler(SensorCommand.KEY_DOUBLE_PRESS, self._on_double_press)
        except (ValueError, KeyError):
            pass
        self._handlers_registered = False

    async def _on_single_press(self, packet):
        """Handle single press: send "继续" + Enter."""
        send_text('继续')
        press_enter()

    async def _on_double_press(self, packet):
        """Handle double press: launch the configured assistant."""
        self._launchAssistant()

    def _launchAssistant(self):
        """Launch the pre-configured programming assistant in a new terminal."""
        assistant = cfg.get(cfg.codingAssistant)
        try:
            if sys.platform == 'win32':
                # Windows: open in a new cmd window
                subprocess.Popen(
                    ['cmd', '/c', 'start', 'cmd', '/k', assistant],
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
            elif sys.platform == 'darwin':
                # macOS: open in Terminal.app
                subprocess.Popen(['open', '-a', 'Terminal', assistant])
            else:
                # Linux: try common terminal emulators
                subprocess.Popen(['x-terminal-emulator', '-e', assistant])

            InfoBar.success('已启动编程助手',
                            f'正在打开 {assistant}...',
                            parent=self.window(), duration=2000,
                            position=InfoBarPosition.TOP_RIGHT)
        except Exception as exc:
            InfoBar.error('启动失败',
                          f'无法启动 {assistant}：{exc}',
                          parent=self.window(), duration=3000,
                          position=InfoBarPosition.TOP_RIGHT)

    def __onAssistantChanged(self, text: str):
        """Save the assistant choice to config."""
        cfg.set(cfg.codingAssistant, text)

    # ── Connection callbacks ─────────────────────────────────────

    def __onDeviceConnected(self, name: str, address: str):
        self.connectionHint.setText(f'已连接设备，可以开启 Coding 模式')
        self.toggleBtn.setEnabled(True)

    def __onDeviceDisconnected(self):
        # Auto-stop coding mode on disconnect
        if self._active:
            self.__stopCodingMode()
        self.connectionHint.setText('请先连接戒指设备')
        self.toggleBtn.setEnabled(False)
        self.toggleBtn.setChecked(False)
