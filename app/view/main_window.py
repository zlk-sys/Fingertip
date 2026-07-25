# coding: utf-8
import os

from PyQt5.QtCore import Qt, QUrl, QSize, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QIcon, QDesktopServices, QColor, QFont
from PyQt5.QtWidgets import QApplication, QWidget, QHBoxLayout, QLabel

from qfluentwidgets import (NavigationItemPosition, MessageBox, FluentWindow,
                            SplashScreen, SystemThemeListener, isDarkTheme,
                            NavigationAvatarWidget, setTheme, Theme, toggleTheme,
                            PillPushButton)
from qfluentwidgets import FluentIcon as FIF

from .home_interface import HomeInterface
from .connect_interface import ConnectInterface
from .device_info_interface import DeviceInfoInterface
from .meeting_interface import MeetingInterface
from .multimedia_interface import MultimediaInterface
from .sensor_interface import SensorInterface
from .level_interface import LevelInterface
from .drawing_interface import DrawingInterface
from .gesture_interface import GestureInterface
from .coding_interface import CodingInterface
from .collab_interface import CollabInterface
from .setting_interface import SettingInterface
from .plugin_interface import PluginInterface
from .plugin_management_interface import PluginManagementInterface
from ..common.config import cfg
from ..common.signal_bus import signalBus
from ..common.plugin_manager import PluginManager


# Mode values
MODE_RECORDING = '录音模式'
MODE_GESTURE = '手势模式'
MODE_UNKNOWN = '未知模式'


class ModeProbeThread(QThread):
    """Periodically probe device mode via start_sensor_report."""
    modeDetected = pyqtSignal(str)  # MODE_RECORDING, MODE_GESTURE, MODE_UNKNOWN
    error = pyqtSignal(str)

    INTERVAL_MS = 8000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._paused = False
        self._client = None
        self._loop_thread = None

    def set_client(self, client, loop_thread):
        self._client = client
        self._loop_thread = loop_thread

    def set_paused(self, paused: bool):
        """Pause probing while a sensor-stream mode is running, otherwise the
        probe's stop_sensor_report would kill the running data stream."""
        self._paused = paused

    def stop(self):
        self._running = False

    def run(self):
        if self._client is None or self._loop_thread is None:
            return
        self._running = True
        from ..sdk.ring_sound import start_sensor_report, stop_sensor_report
        from ..sdk.ring_sound import DeviceError

        while self._running:
            if self._paused:
                self.msleep(200)
                continue
            try:
                start_info = self._loop_thread.run_coro(
                    start_sensor_report(self._client, timeout_s=5.0),
                    timeout=6.0,
                )
                # If successful -> gesture mode, stop report immediately
                self.modeDetected.emit(MODE_GESTURE)
                try:
                    self._loop_thread.run_coro(
                        stop_sensor_report(self._client, timeout_s=5.0),
                        timeout=6.0,
                    )
                except Exception:
                    pass
            except DeviceError as de:
                if de.error_code == 2:
                    self.modeDetected.emit(MODE_RECORDING)
                else:
                    self.modeDetected.emit(MODE_UNKNOWN)
            except Exception:
                self.modeDetected.emit(MODE_UNKNOWN)

            # Wait interval
            for _ in range(self.INTERVAL_MS // 100):
                if not self._running:
                    break
                self.msleep(100)


class ModeIndicator(QWidget):
    """Compact mode indicator widget for the title bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('modeIndicator')

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 2, 8, 2)
        self._layout.setSpacing(6)

        self._dot = QLabel('●', self)
        self._dot.setFont(QFont('Microsoft YaHei', 10))

        self._label = QLabel(MODE_UNKNOWN, self)
        self._label.setFont(QFont('Microsoft YaHei', 10))

        self._layout.addWidget(self._dot)
        self._layout.addWidget(self._label)

        self.setVisible(False)
        self._applyStyle(MODE_UNKNOWN)

    def setMode(self, mode: str):
        self.setVisible(True)
        self._label.setText(mode)
        self._applyStyle(mode)

    def clear(self):
        self.setVisible(False)

    def _applyStyle(self, mode: str):
        if mode == MODE_GESTURE:
            color = '#4dcb66' if isDarkTheme() else '#00a854'
        elif mode == MODE_RECORDING:
            color = '#ff7043' if isDarkTheme() else '#e64a19'
        else:
            color = '#8a8a8a' if isDarkTheme() else '#5c5c5c'

        self._dot.setStyleSheet(f'color: {color};')
        self._label.setStyleSheet(f'color: {color};')


class MainWindow(FluentWindow):

    def __init__(self):
        super().__init__()
        self.initWindow()

        # create system theme listener
        self.themeListener = SystemThemeListener(self)

        # create sub interfaces
        self.homeInterface = HomeInterface(self)
        self.connectInterface = ConnectInterface(self)
        self.deviceInfoInterface = DeviceInfoInterface(self)
        self.meetingInterface = MeetingInterface(self)
        self.multimediaInterface = MultimediaInterface(self)
        self.sensorInterface = SensorInterface(self)
        self.levelInterface = LevelInterface(self)
        self.drawingInterface = DrawingInterface(self)
        self.gestureInterface = GestureInterface(self)
        self.codingInterface = CodingInterface(self)
        self.collabInterface = CollabInterface(self)
        self.settingInterface = SettingInterface(self)

        # Initialize plugin system
        # Use %LOCALAPPDATA%/Fingertip/plugin/ as the plugin directory
        localAppData = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
        plugin_dir = os.path.join(localAppData, 'Fingertip', 'plugin')
        os.makedirs(plugin_dir, exist_ok=True)
        self.pluginManager = PluginManager(plugin_dir, self)
        self.pluginManager.load_plugins()
        
        # Create plugin interfaces (only for enabled plugins)
        enabled_plugins = cfg.get(cfg.enabledPlugins) or []
        self.pluginInterfaces = {}
        for plugin_id, plugin_info in self.pluginManager.plugins.items():
            if plugin_id in enabled_plugins:
                self.pluginInterfaces[plugin_id] = PluginInterface(plugin_info, self.pluginManager, self)
                # Add plugin card to home page
                self.homeInterface.addPluginCard(
                    plugin_id, plugin_info.name, plugin_info.description,
                    signalBus.switchToPlugin
                )
        
        # Plugin management interface
        self.pluginManagementInterface = PluginManagementInterface(self.pluginManager, self)

        # enable acrylic effect on navigation
        self.navigationInterface.setAcrylicEnabled(True)

        # Mode indicator in title bar
        self.modeIndicator = ModeIndicator(self.titleBar)
        self.modeProbeThread = ModeProbeThread(self)
        self.modeProbeThread.modeDetected.connect(self.modeIndicator.setMode)
        self._insertModeIndicator()

        # Active sensor-stream modes (sensor/level/drawing)
        self._activeStreamModes = set()

        self.connectSignalToSlot()

        # add items to navigation interface
        self.initNavigation()
        self.splashScreen.finish()

        # start theme listener
        self.themeListener.start()

    def _insertModeIndicator(self):
        """Insert mode indicator widget into title bar, right after the title."""
        tb = self.titleBar
        # hBoxLayout: [icon][title][spacer][buttonLayout]
        # Insert after title (index 2 = before spacer)
        tb.hBoxLayout.insertWidget(2, self.modeIndicator)

    def connectSignalToSlot(self):
        signalBus.micaEnableChanged.connect(self.setMicaEffectEnabled)
        signalBus.switchToMeeting.connect(
            lambda: self.switchTo(self.meetingInterface))
        signalBus.switchToMultimedia.connect(
            lambda: self.switchTo(self.multimediaInterface))
        signalBus.switchToConnect.connect(
            lambda: self.switchTo(self.connectInterface))
        signalBus.switchToDevice.connect(
            lambda: self.switchTo(self.deviceInfoInterface))
        signalBus.switchToSensor.connect(
            lambda: self.switchTo(self.sensorInterface))
        signalBus.switchToLevel.connect(
            lambda: self.switchTo(self.levelInterface))
        signalBus.switchToDrawing.connect(
            lambda: self.switchTo(self.drawingInterface))
        signalBus.switchToGesture.connect(
            lambda: self.switchTo(self.gestureInterface))
        signalBus.switchToCoding.connect(
            lambda: self.switchTo(self.codingInterface))
        signalBus.switchToCollab.connect(
            lambda: self.switchTo(self.collabInterface))

        # Plugin navigation
        signalBus.switchToPlugin.connect(self.__switchToPlugin)

        # Device connect/disconnect for mode probe
        signalBus.deviceConnected.connect(self._onDeviceConnected)
        signalBus.deviceDisconnected.connect(self._onDeviceDisconnected)

        # Mode mutual exclusion: pause probe while a stream mode is running
        signalBus.modeStarted.connect(self._onModeStarted)
        signalBus.modeStopped.connect(self._onModeStopped)

    def initNavigation(self):
        # add navigation items
        self.addSubInterface(self.homeInterface, FIF.HOME, self.tr('首页'))
        self.addSubInterface(self.deviceInfoInterface, FIF.DEVELOPER_TOOLS, self.tr('设备'))
        self.navigationInterface.addSeparator()
        self.addSubInterface(self.meetingInterface, FIF.FEEDBACK, self.tr('演讲模式'))
        self.addSubInterface(self.multimediaInterface, FIF.VIDEO, self.tr('媒体模式'))
        self.addSubInterface(self.sensorInterface, FIF.MOVE, self.tr('指尖实验室'))
        self.addSubInterface(self.levelInterface, FIF.ROTATE, self.tr('水平仪'))
        self.addSubInterface(self.drawingInterface, FIF.PENCIL_INK, self.tr('轨迹绘制'))
        self.addSubInterface(self.gestureInterface, FIF.ROBOT, self.tr('HMM 手势'))
        self.addSubInterface(self.codingInterface, FIF.CODE, self.tr('Coding 模式'))
        self.addSubInterface(self.collabInterface, FIF.CHAT, self.tr('协同模式'))

        # Add plugin interfaces
        from .plugin_interface import get_icon
        for plugin_id, plugin_interface in self.pluginInterfaces.items():
            plugin_info = self.pluginManager.get_plugin(plugin_id)
            icon = get_icon(plugin_info.icon)
            self.addSubInterface(plugin_interface, icon, self.tr(plugin_info.name))

        # Plugin management (above connect ring)
        self.addSubInterface(self.pluginManagementInterface, FIF.APPLICATION, self.tr('插件管理'), NavigationItemPosition.BOTTOM)

        # add settings to bottom
        self.addSubInterface(self.connectInterface, FIF.CONNECT, self.tr('连接戒指'), NavigationItemPosition.BOTTOM)
        self.addSubInterface(
            self.settingInterface, FIF.SETTING, self.tr('设置'),
            NavigationItemPosition.BOTTOM)

    def initWindow(self):
        self.resize(960, 780)
        self.setMinimumWidth(760)
        self.setWindowTitle('Fingertip')

        # set application logo
        logoPath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'logo.png')
        self.setWindowIcon(QIcon(logoPath))

        # make the top-level window translucent-capable, otherwise OpenGL
        # compositing (triggered by GLViewWidget) discards the alpha channel
        # and the transparent mica areas are rendered black
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.updateFrameless()

        self.setMicaEffectEnabled(cfg.get(cfg.micaEnabled))

        # create splash screen
        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.splashScreen.raise_()

        desktop = QApplication.desktop().availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w // 2 - self.width() // 2, h // 2 - self.height() // 2)
        self.show()
        QApplication.processEvents()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, 'splashScreen'):
            self.splashScreen.resize(self.size())

    def closeEvent(self, e):
        self.themeListener.terminate()
        self.themeListener.deleteLater()
        super().closeEvent(e)

    def _onThemeChangedFinished(self):
        super()._onThemeChangedFinished()

        # retry mica effect
        if self.isMicaEffectEnabled():
            QTimer.singleShot(100, lambda: self.windowEffect.setMicaEffect(self.winId(), isDarkTheme()))

    def _onDeviceConnected(self, name, address):
        from . import connect_interface
        client = connect_interface.shared_client
        loop_thread = connect_interface.async_loop_thread
        if client and loop_thread:
            self.modeProbeThread.set_client(client, loop_thread)
            self.modeProbeThread.start()

    def _onDeviceDisconnected(self):
        self.modeProbeThread.stop()
        self.modeProbeThread.wait(3000)
        self.modeIndicator.clear()
        self._activeStreamModes.clear()
        self.modeProbeThread.set_paused(False)

    def _onModeStarted(self, mode: str):
        if mode in ('sensor', 'level', 'drawing', 'hmm_gesture'):
            self._activeStreamModes.add(mode)
            self.modeProbeThread.set_paused(True)
            # Stream started successfully => device must be in gesture mode
            self.modeIndicator.setMode(MODE_GESTURE)

    def _onModeStopped(self, mode: str):
        self._activeStreamModes.discard(mode)
        if not self._activeStreamModes:
            self.modeProbeThread.set_paused(False)

    def __switchToPlugin(self, plugin_id: str):
        """Switch to a plugin page."""
        if plugin_id in self.pluginInterfaces:
            self.switchTo(self.pluginInterfaces[plugin_id])
