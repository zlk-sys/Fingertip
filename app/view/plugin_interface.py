# coding: utf-8
"""Auto-generated interface for plugins.

Each plugin gets its own page with:
- Status card with toggle button
- Event handler descriptions
- Optional settings (like double-tap enable)
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (ScrollArea, FluentIcon, TitleLabel, BodyLabel,
                            StrongBodyLabel, CaptionLabel, SubtitleLabel,
                            SimpleCardWidget, TogglePushButton, IconWidget,
                            InfoBar, InfoBarPosition, SwitchButton)
from qfluentwidgets import FluentIcon as FIF

from ..common.style_sheet import StyleSheet
from ..common.signal_bus import signalBus
from ..common.plugin_manager import PluginInfo


# Map icon names to FluentIcon enum values
ICON_MAP = {
    'CODE': FIF.CODE,
    'MOVE': FIF.MOVE,
    'PLAY': FIF.PLAY,
    'PLAY_SOLID': FIF.PLAY_SOLID,
    'PAUSE': FIF.PAUSE,
    'VOLUME': FIF.VOLUME,
    'MUTE': FIF.MUTE,
    'DEVELOPER_TOOLS': FIF.DEVELOPER_TOOLS,
    'SETTING': FIF.SETTING,
    'HOME': FIF.HOME,
    'SEARCH': FIF.SEARCH,
    'CALENDAR': FIF.CALENDAR,
    'MAIL': FIF.MAIL,
    'PHONE': FIF.PHONE,
    'VIDEO': FIF.VIDEO,
    'MUSIC': FIF.MUSIC,
    'GAME': FIF.GAME,
    'ROBOT': FIF.ROBOT,
    'BLUETOOTH': FIF.BLUETOOTH,
    'WIFI': FIF.WIFI,
    'CONNECT': FIF.CONNECT,
    'PIN': FIF.PIN,
    'HEART': FIF.HEART,
    'FLAG': FIF.FLAG,
    'TAG': FIF.TAG,
    'EDIT': FIF.EDIT,
    'DELETE': FIF.DELETE,
    'SHARE': FIF.SHARE,
    'DOWNLOAD': FIF.DOWNLOAD,
    'COPY': FIF.COPY,
    'CUT': FIF.CUT,
    'PASTE': FIF.PASTE,
    'SYNC': FIF.SYNC,
    'SAVE': FIF.SAVE,
    'PRINT': FIF.PRINT,
    'INFO': FIF.INFO,
    'HELP': FIF.HELP,
    'CLOSE': FIF.CLOSE,
    'ADD': FIF.ADD,
    'REMOVE': FIF.REMOVE,
    'UP': FIF.UP,
    'DOWN': FIF.DOWN,
    'CHAT': FIF.CHAT,
    'FEEDBACK': FIF.FEEDBACK,
    'PENCIL_INK': FIF.PENCIL_INK,
    'ROTATE': FIF.ROTATE,
    'CAMERA': FIF.CAMERA,
    'PHOTO': FIF.PHOTO,
    'DOCUMENT': FIF.DOCUMENT,
    'FOLDER': FIF.FOLDER,
    'LINK': FIF.LINK,
    'SEND': FIF.SEND,
    'MESSAGE': FIF.MESSAGE,
    'PEOPLE': FIF.PEOPLE,
    'MICROPHONE': FIF.MICROPHONE,
    'HEADPHONE': FIF.HEADPHONE,
    'SPEAKERS': FIF.SPEAKERS,
    'POWER_BUTTON': FIF.POWER_BUTTON,
    'BRIGHTNESS': FIF.BRIGHTNESS,
    'AIRPLANE': FIF.AIRPLANE,
    'CAR': FIF.CAR,
    'BUS': FIF.BUS,
    'TRAIN': FIF.TRAIN,
}


def get_icon(icon_name: str) -> FIF:
    """Get FluentIcon from string name."""
    return ICON_MAP.get(icon_name.upper(), FIF.SETTING)


class InstructionCard(SimpleCardWidget):
    """Card showing a single event handler description."""

    def __init__(self, icon, action_text, description, parent=None):
        super().__init__(parent)
        self.setBorderRadius(10)
        self.setFixedHeight(76)

        self.iconWidget = IconWidget(icon, self)
        self.iconWidget.setFixedSize(18, 18)

        self.actionLabel = StrongBodyLabel(action_text, self)
        self.descLabel = CaptionLabel(description, self)
        self.descLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(20, 16, 20, 16)
        self.hBoxLayout.setSpacing(16)
        self.hBoxLayout.addWidget(self.iconWidget)
        self.hBoxLayout.addSpacing(4)

        self.textLayout = QVBoxLayout()
        self.textLayout.setSpacing(4)
        self.textLayout.addWidget(self.actionLabel)
        self.textLayout.addWidget(self.descLabel)
        self.textLayout.addStretch(1)
        self.hBoxLayout.addLayout(self.textLayout)
        self.hBoxLayout.addStretch(1)


class PluginInterface(ScrollArea):
    """Auto-generated interface for a plugin."""

    def __init__(self, plugin_info: PluginInfo, plugin_manager, parent=None):
        super().__init__(parent=parent)
        self.plugin_info = plugin_info
        self.plugin_manager = plugin_manager

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        # State
        self._active = False
        self._handlers_registered = False
        self._doubleTapEnabled = False

        # Title
        self.titleLabel = TitleLabel(plugin_info.name, self.view)
        self.subtitleLabel = BodyLabel(plugin_info.description, self.view)

        # Status card
        self.statusCard = SimpleCardWidget(self.view)
        self.statusCard.setBorderRadius(12)
        self.statusCard.setFixedHeight(80)

        self.statusLabel = StrongBodyLabel(f'{plugin_info.name}未开启', self.statusCard)
        self.statusLabel.setObjectName('pluginStatusLabel')
        self.statusLabel.setProperty('active', False)
        self.connectionHint = CaptionLabel('请先连接戒指设备', self.statusCard)
        self.connectionHint.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.toggleBtn = TogglePushButton(f'开启 {plugin_info.name}', self.statusCard)
        self.toggleBtn.setFixedWidth(180)
        self.toggleBtn.setEnabled(False)

        self._buildStatusCard()

        # Settings section (double-tap toggle)
        if 'double_tap' in plugin_info.handlers:
            self.settingsSection = SubtitleLabel('功能设置', self.view)
            self.settingsCard = SimpleCardWidget(self.view)
            self.settingsCard.setBorderRadius(12)
            self.settingsCard.setFixedHeight(72)

            self.doubleTapLabel = BodyLabel('启用敲桌面功能', self.settingsCard)
            self.doubleTapSwitch = SwitchButton(self.settingsCard)
            self.doubleTapSwitch.setChecked(False)
            self.doubleTapSwitch.checkedChanged.connect(self.__onDoubleTapChanged)

            self.settingsLayout = QHBoxLayout(self.settingsCard)
            self.settingsLayout.setContentsMargins(20, 16, 20, 16)
            self.settingsLayout.addWidget(self.doubleTapLabel)
            self.settingsLayout.addStretch(1)
            self.settingsLayout.addWidget(self.doubleTapSwitch)

        # Instruction cards section
        self.instructionSection = SubtitleLabel('操作说明', self.view)
        self._buildInstructionCards()

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

    def _buildInstructionCards(self):
        """Create instruction cards based on plugin handlers (without adding to layout)."""
        handlers = self.plugin_info.handlers
        self._instructionCards = []
        
        if 'single_press' in handlers:
            self.singlePressCard = InstructionCard(
                FIF.ACCEPT,
                '单击戒指按钮',
                f'触发: {handlers["single_press"]}()',
                self.view
            )
            self._instructionCards.append(self.singlePressCard)

        if 'double_press' in handlers:
            self.doublePressCard = InstructionCard(
                FIF.ADD_TO,
                '双击戒指按钮',
                f'触发: {handlers["double_press"]}()',
                self.view
            )
            self._instructionCards.append(self.doublePressCard)

        if 'double_tap' in handlers:
            self.doubleTapCard = InstructionCard(
                FIF.BACK_TO_WINDOW,
                '敲两下桌面',
                f'触发: {handlers["double_tap"]}()（需开启开关）',
                self.view
            )
            self._instructionCards.append(self.doubleTapCard)

    def __initWidget(self):
        self.setObjectName(f'pluginInterface_{self.plugin_info.id}')
        self.view.setObjectName('view')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        # Apply meeting interface style (similar layout)
        StyleSheet.MEETING_INTERFACE.apply(self)

        self.vBoxLayout.setContentsMargins(36, 24, 36, 36)
        self.vBoxLayout.setSpacing(0)

        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(16)
        self.vBoxLayout.addWidget(self.statusCard)

        if 'double_tap' in self.plugin_info.handlers:
            self.vBoxLayout.addSpacing(20)
            self.vBoxLayout.addWidget(self.settingsSection)
            self.vBoxLayout.addSpacing(8)
            self.vBoxLayout.addWidget(self.settingsCard)

        self.vBoxLayout.addSpacing(20)
        self.vBoxLayout.addWidget(self.instructionSection)
        self.vBoxLayout.addSpacing(8)

        # Add instruction cards after the section title
        for card in self._instructionCards:
            self.vBoxLayout.addWidget(card)
            self.vBoxLayout.addSpacing(8)

        self.vBoxLayout.addStretch(1)

        # Signals
        self.toggleBtn.toggled.connect(self.__onToggleMode)
        signalBus.deviceConnected.connect(self.__onDeviceConnected)
        signalBus.deviceDisconnected.connect(self.__onDeviceDisconnected)
        signalBus.modeStarted.connect(self.__onOtherModeStarted)

        # Initial state check
        from ..view import connect_interface
        if connect_interface.shared_client is not None:
            self.__onDeviceConnected('', '')

    # ── Mode toggle ───────────────────────────────────────────────

    def __onToggleMode(self, checked: bool):
        """Start or stop plugin mode."""
        if checked:
            self.__startPluginMode()
        else:
            self.__stopPluginMode()

    def __startPluginMode(self):
        """Activate the plugin."""
        from ..view import connect_interface
        client = connect_interface.shared_client
        if client is None:
            self.toggleBtn.setChecked(False)
            InfoBar.warning('未连接设备', '请先在「连接戒指」页面连接戒指',
                            parent=self.window(), duration=3000,
                            position=InfoBarPosition.TOP_RIGHT)
            return

        # Use async loop to activate plugin
        from ..view.connect_interface import async_loop_thread
        if async_loop_thread:
            async_loop_thread.run_coro(
                self.plugin_manager.activate_plugin(self.plugin_info.id, client)
            )

        self._active = True
        signalBus.modeStarted.emit(f'plugin_{self.plugin_info.id}')
        self.statusLabel.setText(f'{self.plugin_info.name}已开启 - 监听中')
        self.statusLabel.setProperty('active', True)
        self.toggleBtn.setText(f'关闭 {self.plugin_info.name}')

        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)

        InfoBar.success(f'{self.plugin_info.name}已开启',
                        '戒指按钮事件已绑定',
                        parent=self.window(), duration=2000,
                        position=InfoBarPosition.TOP_RIGHT)

    def __stopPluginMode(self):
        """Deactivate the plugin."""
        from ..view.connect_interface import async_loop_thread
        if async_loop_thread:
            async_loop_thread.run_coro(
                self.plugin_manager.deactivate_plugin(self.plugin_info.id)
            )

        self._active = False
        self.statusLabel.setText(f'{self.plugin_info.name}未开启')
        self.statusLabel.setProperty('active', False)
        self.toggleBtn.setText(f'开启 {self.plugin_info.name}')

        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)
        signalBus.modeStopped.emit(f'plugin_{self.plugin_info.id}')

    def __onOtherModeStarted(self, mode: str):
        """Auto-stop when another mode starts."""
        my_mode_id = f'plugin_{self.plugin_info.id}'
        if mode == my_mode_id or not self._active:
            return
        self.toggleBtn.setChecked(False)
        InfoBar.info(f'{self.plugin_info.name}已自动关闭',
                     '已开启其他模式，插件自动退出',
                     parent=self.window(), duration=2000,
                     position=InfoBarPosition.TOP_RIGHT)

    def __onDoubleTapChanged(self, checked: bool):
        """Toggle double-tap functionality."""
        self._doubleTapEnabled = checked

    # ── Connection callbacks ─────────────────────────────────────

    def __onDeviceConnected(self, name: str, address: str):
        self.connectionHint.setText(f'已连接设备，可以开启 {self.plugin_info.name}')
        self.toggleBtn.setEnabled(True)

    def __onDeviceDisconnected(self):
        if self._active:
            self.__stopPluginMode()
        self.connectionHint.setText('请先连接戒指设备')
        self.toggleBtn.setEnabled(False)
        self.toggleBtn.setChecked(False)
