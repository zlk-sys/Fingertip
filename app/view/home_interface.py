# coding: utf-8
from datetime import datetime

from PyQt5.QtCore import Qt, QRectF, pyqtSignal
from PyQt5.QtGui import QPixmap, QPainter, QColor, QBrush, QPainterPath, QLinearGradient
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame

from qfluentwidgets import (ScrollArea, isDarkTheme, FluentIcon, PushButton,
                            TitleLabel, BodyLabel, StrongBodyLabel, CaptionLabel,
                            CardWidget, IconWidget, SimpleCardWidget, TogglePushButton,
                            PrimaryPushButton, PillPushButton, HyperlinkButton)

from ..common.style_sheet import StyleSheet
from ..common.signal_bus import signalBus


class BannerWidget(QWidget):
    """ Banner widget """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setFixedHeight(280)

        self.vBoxLayout = QVBoxLayout(self)
        self.titleLabel = TitleLabel(f'{self.get_time_period()}好，我是Fingertip👋', self)
        self.subtitleLabel = BodyLabel('指尖控万物', self)
        self.connectStatus = BodyLabel('当前尚未连接戒指，快去连接以开启智慧生活', self)

        self.vBoxLayout.setSpacing(0)
        self.vBoxLayout.setContentsMargins(36, 40, 36, 20)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.connectStatus)

        self.vBoxLayout.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.titleLabel.setObjectName('titleLabel')
        self.subtitleLabel.setObjectName('subtitleLabel')
        self.connectStatus.setObjectName('connectStatus')

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
        self.connectStatus.setText(f'已连接: {name} ({address})')

    def onDeviceDisconnected(self):
        self.connectStatus.setText('当前尚未连接戒指，快去连接以开启智慧生活')


class ModeCard(SimpleCardWidget):
    """ Mode card widget - large clickable card """

    clicked = pyqtSignal()

    def __init__(self, icon, title, subtitle, parent=None):
        super().__init__(parent=parent)
        self.iconWidget = IconWidget(icon, self)
        self.titleLabel = StrongBodyLabel(title, self)
        self.subtitleLabel = CaptionLabel(subtitle, self)

        self.vBoxLayout = QVBoxLayout(self)

        self.__initWidget()

    def __initWidget(self):
        self.setObjectName('modeCard')
        self.setMinimumHeight(140)
        self.setCursor(Qt.PointingHandCursor)

        self.iconWidget.setFixedSize(48, 48)
        self.subtitleLabel.setTextColor(QColor(96, 96, 96), QColor(216, 216, 216))

        self.vBoxLayout.setSpacing(8)
        self.vBoxLayout.setContentsMargins(24, 24, 24, 24)
        self.vBoxLayout.addWidget(self.iconWidget)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.setAlignment(Qt.AlignLeft | Qt.AlignTop)

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
        """ Load mode cards """
        # 2x2 grid container
        gridWidget = QWidget(self.view)
        gridLayout = QHBoxLayout(gridWidget)
        gridLayout.setSpacing(16)
        gridLayout.setContentsMargins(36, 0, 36, 0)

        # Left column
        leftColumn = QVBoxLayout()
        leftColumn.setSpacing(16)

        connectCard = ModeCard(
            FluentIcon.CONNECT,
            '连接戒指',
            '连接智能戒指，查看设备状态与数据',
            self.view
        )
        connectCard.clicked.connect(lambda: print('连接戒指'))
        leftColumn.addWidget(connectCard)

        sleepCard = ModeCard(
            FluentIcon.POWER_BUTTON,
            '睡眠模式',
            '开启睡眠监测，自动记录睡眠数据',
            self.view
        )
        sleepCard.clicked.connect(lambda: print('睡眠模式'))
        leftColumn.addWidget(sleepCard)

        # Right column
        rightColumn = QVBoxLayout()
        rightColumn.setSpacing(16)

        meetingCard = ModeCard(
            FluentIcon.MUTE,
            '会议模式',
            '静音通知，专注会议不被打扰',
            self.view
        )
        meetingCard.clicked.connect(lambda: signalBus.switchToMeeting.emit())
        rightColumn.addWidget(meetingCard)

        movieCard = ModeCard(
            FluentIcon.VIDEO,
            '追剧模式',
            '手势控制播放，轻松享受影视内容',
            self.view
        )
        movieCard.clicked.connect(lambda: signalBus.switchToMultimedia.emit())
        rightColumn.addWidget(movieCard)

        gridLayout.addLayout(leftColumn, 1)
        gridLayout.addLayout(rightColumn, 1)

        self.vBoxLayout.addWidget(gridWidget)
