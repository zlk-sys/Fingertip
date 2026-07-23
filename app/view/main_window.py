# coding: utf-8
from PyQt5.QtCore import QUrl, QSize, QTimer
from PyQt5.QtGui import QIcon, QDesktopServices, QColor
from PyQt5.QtWidgets import QApplication

from qfluentwidgets import (NavigationItemPosition, MessageBox, FluentWindow,
                            SplashScreen, SystemThemeListener, isDarkTheme,
                            NavigationAvatarWidget, setTheme, Theme, toggleTheme)
from qfluentwidgets import FluentIcon as FIF

from .home_interface import HomeInterface
from .connect_interface import ConnectInterface
from .device_info_interface import DeviceInfoInterface
from .meeting_interface import MeetingInterface
from .multimedia_interface import MultimediaInterface
from .sensor_interface import SensorInterface
from .level_interface import LevelInterface
from .basic_interface import BasicInterface
from .setting_interface import SettingInterface
from ..common.config import cfg
from ..common.signal_bus import signalBus


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
        self.basicInterface = BasicInterface(self)
        self.settingInterface = SettingInterface(self)

        # enable acrylic effect on navigation
        self.navigationInterface.setAcrylicEnabled(True)

        self.connectSignalToSlot()

        # add items to navigation interface
        self.initNavigation()
        self.splashScreen.finish()

        # start theme listener
        self.themeListener.start()

    def connectSignalToSlot(self):
        signalBus.micaEnableChanged.connect(self.setMicaEffectEnabled)
        signalBus.switchToMeeting.connect(
            lambda: self.switchTo(self.meetingInterface))
        signalBus.switchToMultimedia.connect(
            lambda: self.switchTo(self.multimediaInterface))

    def initNavigation(self):
        # add navigation items
        self.addSubInterface(self.homeInterface, FIF.HOME, self.tr('首页'))
        self.addSubInterface(self.connectInterface, FIF.CONNECT, self.tr('连接戒指'))
        self.addSubInterface(self.deviceInfoInterface, FIF.PHONE, self.tr('设备'))
        self.addSubInterface(self.meetingInterface, FIF.MUTE, self.tr('会议模式'))
        self.addSubInterface(self.multimediaInterface, FIF.VIDEO, self.tr('追剧模式'))
        self.addSubInterface(self.sensorInterface, FIF.MOVE, self.tr('传感器'))
        self.addSubInterface(self.levelInterface, FIF.ROTATE, self.tr('水平仪'))

        self.navigationInterface.addSeparator()

        self.addSubInterface(
            self.basicInterface, FIF.CHECKBOX, self.tr('基础组件'),
            NavigationItemPosition.SCROLL)

        # add settings to bottom
        self.addSubInterface(
            self.settingInterface, FIF.SETTING, self.tr('设置'),
            NavigationItemPosition.BOTTOM)

    def initWindow(self):
        self.resize(960, 780)
        self.setMinimumWidth(760)
        self.setWindowTitle('Fingertip')

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
