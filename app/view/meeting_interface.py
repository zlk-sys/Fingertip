# coding: utf-8
"""Meeting (PPT) mode interface.

In meeting mode, the ring button controls slide navigation:
  - Single-click  -> next slide (Right arrow)
  - Double-click  -> previous slide (Left arrow)
"""
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (ScrollArea, FluentIcon, TitleLabel, BodyLabel,
                            StrongBodyLabel, CaptionLabel, SubtitleLabel,
                            SimpleCardWidget, TogglePushButton, IconWidget,
                            InfoBar, InfoBarPosition)
from qfluentwidgets import FluentIcon as FIF

from ..common.style_sheet import StyleSheet
from ..common.signal_bus import signalBus
from ..common.keyboard_simulator import next_slide, previous_slide
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


class MeetingInterface(ScrollArea):
    """Meeting / PPT mode interface."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        # State
        self._active = False
        self._handlers_registered = False

        # Title
        self.titleLabel = TitleLabel('演讲模式', self.view)
        self.subtitleLabel = BodyLabel('使用戒指按钮控制 PPT 翻页', self.view)

        # Status card
        self.statusCard = SimpleCardWidget(self.view)
        self.statusCard.setBorderRadius(12)
        self.statusCard.setFixedHeight(100)

        # self.statusIcon = IconWidget(FIF.MUTE, self.statusCard)
        # self.statusIcon.setFixedSize(40, 40)
        self.statusLabel = StrongBodyLabel('演讲模式未开启', self.statusCard)
        self.statusLabel.setObjectName('meetingStatusLabel')
        self.statusLabel.setProperty('active', False)
        self.connectionHint = CaptionLabel('请先连接戒指设备', self.statusCard)
        self.connectionHint.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.toggleBtn = TogglePushButton('开启演讲模式', self.statusCard)
        self.toggleBtn.setFixedWidth(160)
        self.toggleBtn.setEnabled(False)

        self._buildStatusCard()

        # Instruction cards section
        self.instructionSection = SubtitleLabel('操作说明', self.view)

        self.nextCard = InstructionCard(
            FIF.CARE_RIGHT_SOLID,
            '单击戒指按钮',
            '下一页 (模拟 → 方向键)',
            self.view
        )
        self.prevCard = InstructionCard(
            FIF.CARE_LEFT_SOLID,
            '双击戒指按钮',
            '上一页 (模拟 ← 方向键)',
            self.view
        )

        self.__initWidget()

    def _buildStatusCard(self):
        """Layout the status card contents."""
        cardLayout = QHBoxLayout(self.statusCard)
        cardLayout.setContentsMargins(20, 16, 20, 16)
        cardLayout.setSpacing(16)

        # cardLayout.addWidget(self.statusIcon)

        textLayout = QVBoxLayout()
        textLayout.setSpacing(4)
        textLayout.addWidget(self.statusLabel)
        textLayout.addWidget(self.connectionHint)
        textLayout.addStretch(1)
        cardLayout.addLayout(textLayout)
        cardLayout.addStretch(1)
        cardLayout.addWidget(self.toggleBtn, 0, Qt.AlignVCenter)

    def __initWidget(self):
        self.setObjectName('meetingInterface')
        self.view.setObjectName('view')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        StyleSheet.MEETING_INTERFACE.apply(self)

        self.vBoxLayout.setContentsMargins(36, 24, 36, 36)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.statusCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.instructionSection)
        self.vBoxLayout.addWidget(self.nextCard)
        self.vBoxLayout.addWidget(self.prevCard)
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
        """Start or stop meeting mode."""
        if checked:
            self.__startMeetingMode()
        else:
            self.__stopMeetingMode()

    def __startMeetingMode(self):
        client = _get_shared_client()
        if client is None:
            self.toggleBtn.setChecked(False)
            InfoBar.warning('未连接设备', '请先在「连接戒指」页面连接戒指',
                            parent=self.window(), duration=3000,
                            position=InfoBarPosition.TOP_RIGHT)
            return

        self._active = True
        self._register_handlers(client)
        signalBus.modeStarted.emit('meeting')
        self.statusLabel.setText('演讲模式已开启 - 监听中')
        self.statusLabel.setProperty('active', True)
        self.toggleBtn.setText('关闭演讲模式')
        # self.statusIcon.setIcon(FIF.MUTE)

        # Refresh stylesheet for property change
        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)

        InfoBar.success('演讲模式已开启',
                        '单击=下一页 | 双击=上一页',
                        parent=self.window(), duration=2000,
                        position=InfoBarPosition.TOP_RIGHT)

    def __stopMeetingMode(self):
        client = _get_shared_client()
        if client is not None and self._handlers_registered:
            self._unregister_handlers(client)

        self._active = False
        self.statusLabel.setText('演讲模式未开启')
        self.statusLabel.setProperty('active', False)
        self.toggleBtn.setText('开启演讲模式')

        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)
        signalBus.modeStopped.emit('meeting')

    def __onOtherModeStarted(self, mode: str):
        """Auto-stop meeting mode when another mode starts."""
        if mode == 'meeting' or not self._active:
            return
        self.toggleBtn.setChecked(False)  # triggers __stopMeetingMode
        InfoBar.info('演讲模式已自动关闭', '已开启其他模式，演讲模式自动退出',
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
        """Handle single press: next slide."""
        next_slide()

    async def _on_double_press(self, packet):
        """Handle double press: previous slide."""
        previous_slide()

    # ── Connection callbacks ─────────────────────────────────────

    def __onDeviceConnected(self, name: str, address: str):
        self.connectionHint.setText(f'已连接设备，可以开启演讲模式')
        self.toggleBtn.setEnabled(True)

    def __onDeviceDisconnected(self):
        # Auto-stop meeting mode on disconnect
        if self._active:
            self.__stopMeetingMode()
        self.connectionHint.setText('请先连接戒指设备')
        self.toggleBtn.setEnabled(False)
        self.toggleBtn.setChecked(False)
