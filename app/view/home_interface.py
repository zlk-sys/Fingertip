# coding: utf-8
from datetime import datetime

from PyQt5.QtCore import Qt, QRectF, pyqtSignal
from PyQt5.QtGui import QPixmap, QPainter, QColor, QBrush, QPainterPath, QLinearGradient
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame

from qfluentwidgets import (ScrollArea, isDarkTheme, FluentIcon, PushButton,
                            TitleLabel, BodyLabel, StrongBodyLabel, CaptionLabel,
                            CardWidget, IconWidget, SimpleCardWidget, TogglePushButton,
                            PrimaryPushButton, PillPushButton, HyperlinkButton)

from ..common.style_sheet import StyleSheet
from ..common.signal_bus import signalBus
from qfluentwidgets import FluentIcon as FIF


class AlertBanner(SimpleCardWidget):
    """Alert banner for connection/battery warnings."""

    reconnectClicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('alertBanner')
        self.setBorderRadius(12)
        self.setFixedHeight(56)
        self.setCursor(Qt.PointingHandCursor)

        self.iconWidget = IconWidget(FIF.INFO, self)
        self.iconWidget.setFixedSize(20, 20)
        self.messageLabel = BodyLabel('未获取到电量，可能连接异常', self)
        self.reconnectBtn = HyperlinkButton('', '重新连接', self)
        self.reconnectBtn.setObjectName('reconnectBtn')

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(16, 0, 16, 0)
        self.hBoxLayout.setSpacing(10)
        self.hBoxLayout.addWidget(self.iconWidget)
        self.hBoxLayout.addWidget(self.messageLabel)
        self.hBoxLayout.addStretch(1)
        self.hBoxLayout.addWidget(self.reconnectBtn)

        self.reconnectBtn.clicked.connect(self.reconnectClicked.emit)

    def setConnected(self, connected):
        if connected:
            self.setVisible(False)
        else:
            self.setVisible(True)
            self.messageLabel.setText('未连接设备，请前往「连接戒指」页面连接')
            self.reconnectBtn.setVisible(False)

    def setBatteryMissing(self):
        self.setVisible(True)
        self.messageLabel.setText('未获取到电量，可能连接异常')
        self.reconnectBtn.setVisible(True)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        self.reconnectClicked.emit()


class BannerWidget(QWidget):
    """ Banner widget """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setFixedHeight(280)

        self.vBoxLayout = QVBoxLayout(self)
        self.titleLabel = TitleLabel(f'{self.get_time_period()}好，我是Fingertip👋', self)
        self.subtitleLabel = BodyLabel('指尖控万物', self)
        self.connectStatus = AlertBanner(self)
        self.connectStatus.messageLabel.setText('当前尚未连接戒指，快去连接以开启智慧生活')
        self.connectStatus.reconnectBtn.setText('去连接')
        self.connectStatus.reconnectBtn.setVisible(True)

        self.vBoxLayout.setSpacing(0)
        self.vBoxLayout.setContentsMargins(36, 40, 36, 20)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(12)
        self.vBoxLayout.addWidget(self.connectStatus)

        self.vBoxLayout.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.titleLabel.setObjectName('titleLabel')
        self.subtitleLabel.setObjectName('subtitleLabel')

    def paintEvent(self, e):
        super().paintEvent(e)
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.SmoothPixmapTransform | QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)
        w, h = self.width(), self.height()
        path.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
        path.addRect(QRectF(0, h - 50, 50, 50))
        path.addRect(QRectF(w - 50, 0, 50, 50))
        path.addRect(QRectF(w - 50, h - 50, 50, 50))
        path = path.simplified()

        gradient = QLinearGradient(0, 0, 0, h)
        if not isDarkTheme():
            gradient.setColorAt(0, QColor(207, 216, 228, 255))
            gradient.setColorAt(1, QColor(207, 216, 228, 0))
        else:
            gradient.setColorAt(0, QColor(0, 0, 0, 255))
            gradient.setColorAt(1, QColor(0, 0, 0, 0))

        painter.fillPath(path, QBrush(gradient))

    def get_time_period(self):
        """
        获取当前时间并判断所属时段

        返回:
            str: 时段名称（早上/中午/下午/晚上/深夜）
        """
        now = datetime.now()
        hour = now.hour

        if 5 <= hour < 9:
            return "早上"
        elif 9 <= hour < 12:
            return "上午"
        elif 12 <= hour < 14:
            return "中午"
        elif 14 <= hour < 18:
            return "下午"
        elif 18 <= hour < 22:
            return "晚上"
        else:
            return "深夜"

    def onDeviceConnected(self, name: str, address: str):
        self.connectStatus.setVisible(False)

    def onDeviceDisconnected(self):
        self.connectStatus.messageLabel.setText('当前尚未连接戒指，快去连接以开启智慧生活')
        self.connectStatus.reconnectBtn.setText('去连接')
        self.connectStatus.reconnectBtn.setVisible(True)
        self.connectStatus.setVisible(True)


class ModeCard(SimpleCardWidget):
    """ Compact mode card widget - clickable to navigate """

    clicked = pyqtSignal()

    def __init__(self, title, subtitle, parent=None):
        super().__init__(parent=parent)
        self.titleLabel = StrongBodyLabel(title, self)
        self.subtitleLabel = CaptionLabel(subtitle, self)

        self.vBoxLayout = QVBoxLayout(self)

        self.__initWidget()

    def __initWidget(self):
        self.setObjectName('modeCard')
        self.setFixedHeight(80)
        self.setCursor(Qt.PointingHandCursor)

        self.subtitleLabel.setTextColor(QColor(96, 96, 96), QColor(216, 216, 216))

        self.vBoxLayout.setContentsMargins(18, 0, 18, 0)
        self.vBoxLayout.setSpacing(4)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        self.clicked.emit()


class HomeInterface(ScrollArea):
    """ Home interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.view = QWidget(self)
        self.banner = BannerWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        self.__initWidget()
        self.__connectSignals()
        self.__loadModeCards()

    def __connectSignals(self):
        signalBus.deviceConnected.connect(self.banner.onDeviceConnected)
        signalBus.deviceDisconnected.connect(self.banner.onDeviceDisconnected)
        self.banner.connectStatus.reconnectClicked.connect(
            lambda: signalBus.switchToConnect.emit())

    def __initWidget(self):
        self.view.setObjectName('view')
        self.setObjectName('homeInterface')

        # apply style sheet for transparent background
        StyleSheet.HOME_INTERFACE.apply(self)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        self.vBoxLayout.setContentsMargins(0, 0, 0, 36)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.addWidget(self.banner)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

    def __loadModeCards(self):
        """ Load mode cards in a compact 3x2 grid """
        gridWidget = QWidget(self.view)
        gridLayout = QGridLayout(gridWidget)
        gridLayout.setSpacing(16)
        gridLayout.setContentsMargins(36, 0, 36, 0)

        cards = [
            ('设备', '查看设备信息与存储状态', signalBus.switchToDevice),
            ('演讲模式', '戒指按键控制 PPT 翻页', signalBus.switchToMeeting),
            ('媒体模式', '手势控制视频播放与暂停', signalBus.switchToMultimedia),
            ('指尖实验室', '实时采集传感器数据', signalBus.switchToSensor),
            ('水平仪', '可视化查看设备倾斜角度', signalBus.switchToLevel),
            ('轨迹绘制', '手势在空中绘制轨迹', signalBus.switchToDrawing),
        ]

        for index, (title, subtitle, signal) in enumerate(cards):
            card = ModeCard(title, subtitle, self.view)
            card.clicked.connect(signal.emit)
            gridLayout.addWidget(card, index // 3, index % 3)

        self.vBoxLayout.addWidget(gridWidget)

        moreLabel = CaptionLabel('更多场景开发中', self.view)
        moreLabel.setTextColor(QColor(96, 96, 96), QColor(216, 216, 216))
        moreLabel.setAlignment(Qt.AlignCenter)
        moreLabel.setContentsMargins(36, 4, 36, 0)
        self.vBoxLayout.addWidget(moreLabel)
