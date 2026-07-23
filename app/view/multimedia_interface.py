# coding: utf-8
"""Multimedia mode interface.

In multimedia mode, the ring button toggles play/pause of the active media player:
  - Single-click  -> play/pause (VK_MEDIA_PLAY_PAUSE)
A manual button is also provided for direct control.
"""
import datetime

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (ScrollArea, FluentIcon, TitleLabel, BodyLabel,
                            StrongBodyLabel, CaptionLabel, SubtitleLabel,
                            SimpleCardWidget, TogglePushButton, IconWidget,
                            PrimaryPushButton, SwitchButton, InfoBar, InfoBarPosition)
from qfluentwidgets import FluentIcon as FIF

from ..common.style_sheet import StyleSheet
from ..common.signal_bus import signalBus
from ..common.keyboard_simulator import toggle_play_pause, next_track, previous_track
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
        self.setFixedHeight(96)

        self.iconWidget = IconWidget(icon, self)
        self.iconWidget.setFixedSize(36, 36)

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


class EventItem(QWidget):
    """Single event entry in the event log."""

    def __init__(self, time_str, action, parent=None):
        super().__init__(parent)
        self.setObjectName('eventItem')

        self.timeLabel = CaptionLabel(time_str, self)
        self.timeLabel.setObjectName('eventTimeLabel')
        self.actionLabel = BodyLabel(action, self)
        self.actionLabel.setObjectName('eventActionLabel')

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(0, 2, 0, 2)
        self.hBoxLayout.setSpacing(12)
        self.hBoxLayout.addWidget(self.timeLabel)
        self.hBoxLayout.addWidget(self.actionLabel)
        self.hBoxLayout.addStretch(1)


class MultimediaInterface(ScrollArea):
    """Multimedia / video mode interface."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        # State
        self._active = False
        self._handlers_registered = False
        self._doubleTapEnabled = True

        # Title
        self.titleLabel = TitleLabel('追剧模式', self.view)
        self.subtitleLabel = BodyLabel('使用戒指按钮控制视频播放与暂停', self.view)

        # Status card
        self.statusCard = SimpleCardWidget(self.view)
        self.statusCard.setBorderRadius(12)
        self.statusCard.setFixedHeight(100)

        self.statusIcon = IconWidget(FIF.VIDEO, self.statusCard)
        self.statusIcon.setFixedSize(40, 40)
        self.statusLabel = StrongBodyLabel('追剧模式未开启', self.statusCard)
        self.statusLabel.setObjectName('mediaStatusLabel')
        self.statusLabel.setProperty('active', False)
        self.connectionHint = CaptionLabel('请先连接戒指设备', self.statusCard)
        self.connectionHint.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.toggleBtn = TogglePushButton('开启追剧模式', self.statusCard)
        self.toggleBtn.setFixedWidth(160)
        self.toggleBtn.setEnabled(False)

        self._buildStatusCard()

        # Manual control section
        self.manualSection = SubtitleLabel('手动控制', self.view)
        self.manualCard = SimpleCardWidget(self.view)
        self.manualCard.setBorderRadius(12)
        self.manualCard.setMinimumHeight(120)

        self.playPauseBtn = PrimaryPushButton('播放 / 暂停', self.manualCard)
        self.playPauseBtn.setFixedSize(160, 48)
        self.playPauseBtn.setEnabled(False)

        self.manualLayout = QHBoxLayout(self.manualCard)
        self.manualLayout.setContentsMargins(20, 16, 20, 16)
        self.manualLayout.addStretch(1)
        self.manualLayout.addWidget(self.playPauseBtn)
        self.manualLayout.addStretch(1)

        # Gesture settings section
        self.gestureSection = SubtitleLabel('手势设置', self.view)
        self.gestureCard = SimpleCardWidget(self.view)
        self.gestureCard.setBorderRadius(12)
        self.gestureCard.setFixedHeight(72)

        self.doubleTapLabel = BodyLabel('双击戒指本体上一首', self.gestureCard)
        self.doubleTapSwitch = SwitchButton(self.gestureCard)
        self.doubleTapSwitch.setChecked(True)

        self.gestureLayout = QHBoxLayout(self.gestureCard)
        self.gestureLayout.setContentsMargins(20, 16, 20, 16)
        self.gestureLayout.addWidget(self.doubleTapLabel)
        self.gestureLayout.addStretch(1)
        self.gestureLayout.addWidget(self.doubleTapSwitch)

        # Instruction cards
        self.instructionSection = SubtitleLabel('操作说明', self.view)
        self.playCard = InstructionCard(
            FIF.PLAY_SOLID,
            '单击戒指按钮',
            '播放 / 暂停',
            self.view
        )
        self.nextCard = InstructionCard(
            FIF.CARE_RIGHT_SOLID,
            '双击戒指按钮',
            '下一首',
            self.view
        )
        self.prevCard = InstructionCard(
            FIF.CARE_LEFT_SOLID,
            '双击戒指本体',
            '上一首（双击戒指本体）—— 已开启',
            self.view
        )

        # Event log section
        self.eventLogSection = SubtitleLabel('操作记录', self.view)
        self.eventLogCard = SimpleCardWidget(self.view)
        self.eventLogCard.setObjectName('eventLogCard')
        self.eventLogCard.setBorderRadius(10)
        self.eventLogCard.setMinimumHeight(60)

        self.eventLogLayout = QVBoxLayout(self.eventLogCard)
        self.eventLogLayout.setContentsMargins(20, 16, 20, 16)
        self.eventLogLayout.setSpacing(4)

        self.emptyLogLabel = CaptionLabel('暂无操作记录', self.eventLogCard)
        self.emptyLogLabel.setAlignment(Qt.AlignCenter)
        self.emptyLogLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))
        self.eventLogLayout.addWidget(self.emptyLogLabel)

        self.__initWidget()

    def _buildStatusCard(self):
        """Layout the status card contents."""
        cardLayout = QHBoxLayout(self.statusCard)
        cardLayout.setContentsMargins(20, 16, 20, 16)
        cardLayout.setSpacing(16)

        cardLayout.addWidget(self.statusIcon)

        textLayout = QVBoxLayout()
        textLayout.setSpacing(4)
        textLayout.addWidget(self.statusLabel)
        textLayout.addWidget(self.connectionHint)
        textLayout.addStretch(1)
        cardLayout.addLayout(textLayout)
        cardLayout.addStretch(1)
        cardLayout.addWidget(self.toggleBtn, 0, Qt.AlignVCenter)

    def __initWidget(self):
        self.setObjectName('multimediaInterface')
        self.view.setObjectName('view')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        StyleSheet.MULTIMEDIA_INTERFACE.apply(self)

        self.vBoxLayout.setContentsMargins(36, 24, 36, 36)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.statusCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.manualSection)
        self.vBoxLayout.addWidget(self.manualCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.gestureSection)
        self.vBoxLayout.addWidget(self.gestureCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.instructionSection)
        self.vBoxLayout.addWidget(self.playCard)
        self.vBoxLayout.addWidget(self.nextCard)
        self.vBoxLayout.addWidget(self.prevCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.eventLogSection)
        self.vBoxLayout.addWidget(self.eventLogCard)
        self.vBoxLayout.addStretch(1)

        # Signals
        self.toggleBtn.toggled.connect(self.__onToggleMode)
        self.playPauseBtn.clicked.connect(self.__onManualToggle)
        self.doubleTapSwitch.checkedChanged.connect(self.__onDoubleTapSwitchChanged)
        signalBus.deviceConnected.connect(self.__onDeviceConnected)
        signalBus.deviceDisconnected.connect(self.__onDeviceDisconnected)

        # Initial state check
        if _get_shared_client() is not None:
            self.__onDeviceConnected('', '')

    # ── Mode toggle ───────────────────────────────────────────────

    def __onToggleMode(self, checked: bool):
        """Start or stop multimedia mode."""
        if checked:
            self.__startMultimediaMode()
        else:
            self.__stopMultimediaMode()

    def __startMultimediaMode(self):
        client = _get_shared_client()
        if client is None:
            self.toggleBtn.setChecked(False)
            InfoBar.warning('未连接设备', '请先在「连接戒指」页面连接戒指',
                            parent=self.window(), duration=3000,
                            position=InfoBarPosition.TOP_RIGHT)
            return

        self._active = True
        self._register_handlers(client)
        self.statusLabel.setText('追剧模式已开启 - 监听中')
        self.statusLabel.setProperty('active', True)
        self.toggleBtn.setText('关闭追剧模式')
        self.statusIcon.setIcon(FIF.PLAY_SOLID)
        self.playPauseBtn.setEnabled(True)

        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)

        InfoBar.success('追剧模式已开启',
                        '单击戒指按钮即可播放/暂停',
                        parent=self.window(), duration=2000,
                        position=InfoBarPosition.TOP_RIGHT)

    def __stopMultimediaMode(self):
        client = _get_shared_client()
        if client is not None and self._handlers_registered:
            self._unregister_handlers(client)

        self._active = False
        self.statusLabel.setText('追剧模式未开启')
        self.statusLabel.setProperty('active', False)
        self.toggleBtn.setText('开启追剧模式')
        self.playPauseBtn.setEnabled(False)
        self.statusIcon.setIcon(FIF.VIDEO)

        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)

    # ── Packet handlers ───────────────────────────────────────────

    def _register_handlers(self, client):
        """Register ring button event handlers on the BLE client."""
        if self._handlers_registered:
            return
        client.add_packet_handler(SensorCommand.KEY_SINGLE_PRESS, self._on_single_press)
        client.add_packet_handler(SensorCommand.KEY_DOUBLE_PRESS, self._on_double_press)
        client.add_packet_handler(SensorCommand.DOUBLE_TAP, self._on_double_tap)
        self._handlers_registered = True

    def _unregister_handlers(self, client):
        """Remove ring button event handlers from the BLE client."""
        if not self._handlers_registered:
            return
        for command, handler in [
            (SensorCommand.KEY_SINGLE_PRESS, self._on_single_press),
            (SensorCommand.KEY_DOUBLE_PRESS, self._on_double_press),
            (SensorCommand.DOUBLE_TAP, self._on_double_tap),
        ]:
            try:
                client.remove_packet_handler(command, handler)
            except (ValueError, KeyError):
                pass
        self._handlers_registered = False

    async def _on_single_press(self, packet):
        """Handle single press: toggle play/pause."""
        self._trigger_media('戒指单击', toggle_play_pause)

    async def _on_double_press(self, packet):
        """Handle double press: next track."""
        self._trigger_media('戒指双击', next_track)

    async def _on_double_tap(self, packet):
        """Handle double tap: previous track (only if enabled)."""
        if not self._doubleTapEnabled:
            return
        self._trigger_media('戒指双击本体', previous_track)

    def __onDoubleTapSwitchChanged(self, checked: bool):
        """Enable or disable double-tap previous track gesture."""
        self._doubleTapEnabled = checked
        status = '已开启' if checked else '已关闭'
        self.prevCard.keyLabel.setText(
            f'上一首（双击戒指本体）—— {status}'
        )

    def __onManualToggle(self):
        """Handle manual button click."""
        self._trigger_media('手动点击', toggle_play_pause)

    def _trigger_media(self, source: str, action):
        """Execute media action and log it."""
        action()
        action_name = {
            toggle_play_pause: '播放/暂停',
            next_track: '下一首',
            previous_track: '上一首',
        }.get(action, '媒体控制')
        self._log_event(source, action_name)

    # ── Event log ─────────────────────────────────────────────────

    def _log_event(self, action_type: str, action: str):
        """Add an event entry to the log."""
        now = datetime.datetime.now().strftime('%H:%M:%S')
        if self.emptyLogLabel.isVisible():
            self.emptyLogLabel.setVisible(False)

        item = EventItem(now, f'{action_type} → {action}', self.eventLogCard)
        self.eventLogLayout.insertWidget(0, item)

        while self.eventLogLayout.count() > 21:
            child = self.eventLogLayout.itemAt(self.eventLogLayout.count() - 1)
            w = child.widget()
            if w and w is not self.emptyLogLabel:
                w.deleteLater()
                break
            elif w is self.emptyLogLabel:
                break

    # ── Connection callbacks ─────────────────────────────────────

    def __onDeviceConnected(self, name: str, address: str):
        self.connectionHint.setText('已连接设备，可以开启追剧模式')
        self.toggleBtn.setEnabled(True)

    def __onDeviceDisconnected(self):
        if self._active:
            self.__stopMultimediaMode()
        self.connectionHint.setText('请先连接戒指设备')
        self.toggleBtn.setEnabled(False)
        self.toggleBtn.setChecked(False)
