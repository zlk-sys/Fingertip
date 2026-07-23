# coding: utf-8
from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QPixmap, QPainter, QColor, QBrush, QPainterPath, QLinearGradient
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame

from qfluentwidgets import (ScrollArea, isDarkTheme, FluentIcon, PushButton,
                            TitleLabel, BodyLabel, StrongBodyLabel, CaptionLabel,
                            CardWidget, IconWidget, SimpleCardWidget, TogglePushButton,
                            PrimaryPushButton, PillPushButton, HyperlinkButton)

from ..common.style_sheet import StyleSheet


class BannerWidget(QWidget):
    """ Banner widget """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setFixedHeight(280)

        self.vBoxLayout = QVBoxLayout(self)
        self.titleLabel = TitleLabel('Fingertip', self)
        self.subtitleLabel = BodyLabel('基于 PyQt5 的 Fluent Design 风格应用', self)

        self.vBoxLayout.setSpacing(0)
        self.vBoxLayout.setContentsMargins(36, 40, 36, 20)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addStretch(1)
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


class FeatureCard(SimpleCardWidget):
    """ Feature card widget """

    def __init__(self, icon, title, content, parent=None):
        super().__init__(parent=parent)
        self.iconWidget = IconWidget(icon, self)
        self.titleLabel = StrongBodyLabel(title, self)
        self.contentLabel = CaptionLabel(content, self)

        self.hBoxLayout = QHBoxLayout(self)
        self.vBoxLayout = QVBoxLayout()

        self.__initWidget()

    def __initWidget(self):
        self.iconWidget.setFixedSize(36, 36)
        self.contentLabel.setTextColor(QColor(96, 96, 96), QColor(216, 216, 216))

        self.hBoxLayout.setSpacing(16)
        self.hBoxLayout.setContentsMargins(20, 20, 20, 20)
        self.hBoxLayout.addWidget(self.iconWidget)
        self.hBoxLayout.addLayout(self.vBoxLayout)
        self.hBoxLayout.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        self.vBoxLayout.setSpacing(4)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.contentLabel)
        self.vBoxLayout.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)


class HomeInterface(ScrollArea):
    """ Home interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.view = QWidget(self)
        self.banner = BannerWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        self.__initWidget()
        self.__loadFeatures()

    def __initWidget(self):
        self.view.setObjectName('view')
        self.setObjectName('homeInterface')

        # apply style sheet for transparent background
        StyleSheet.HOME_INTERFACE.apply(self)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)

        self.vBoxLayout.setContentsMargins(0, 0, 0, 36)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.addWidget(self.banner)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

    def __loadFeatures(self):
        """ Load feature cards """
        # Section title
        sectionLabel = StrongBodyLabel('功能特性', self.view)
        sectionLabel.setContentsMargins(36, 0, 0, 0)
        self.vBoxLayout.addWidget(sectionLabel)

        # Feature cards container
        cardsWidget = QWidget(self.view)
        cardsLayout = QVBoxLayout(cardsWidget)
        cardsLayout.setSpacing(12)
        cardsLayout.setContentsMargins(36, 0, 36, 0)

        cardsLayout.addWidget(FeatureCard(
            FluentIcon.LAYOUT,
            'FluentWindow 主窗口',
            '使用 FluentWindow 构建带有侧边导航栏的主窗口，支持明暗主题切换。'
        ))
        cardsLayout.addWidget(FeatureCard(
            FluentIcon.CHECKBOX,
            '丰富的基础组件',
            '按钮、复选框、单选按钮、滑块、开关、下拉框等 Fluent 风格组件。'
        ))
        cardsLayout.addWidget(FeatureCard(
            FluentIcon.PALETTE,
            '主题与个性化',
            '支持浅色/深色/跟随系统主题，可自定义主题颜色，云母/亚克力效果。'
        ))
        cardsLayout.addWidget(FeatureCard(
            FluentIcon.SETTING,
            '完整的设置界面',
            '内置 SettingCard 系列组件，轻松搭建专业的应用设置页面。'
        ))
        cardsLayout.addWidget(FeatureCard(
            FluentIcon.MESSAGE,
            '对话框与提示',
            '消息对话框、气泡弹窗、教学提示、信息栏等丰富的交互组件。'
        ))

        self.vBoxLayout.addWidget(cardsWidget)
