# coding: utf-8
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QWidget, QLabel, QFileDialog, QLineEdit, QHBoxLayout, QVBoxLayout

from qfluentwidgets import (SettingCardGroup, SwitchSettingCard,
                            OptionsSettingCard, PushSettingCard,
                            ScrollArea,
                            ExpandLayout, Theme, CustomColorSettingCard,
                            setTheme, setThemeColor, RangeSettingCard, isDarkTheme,
                            TitleLabel, LineEdit,
                            InfoBar, InfoBarPosition, FluentIcon,
                            SimpleCardWidget, BodyLabel, CaptionLabel)

from ..common.config import cfg, AUTHOR, VERSION, YEAR, isWin11
from ..common.signal_bus import signalBus
from ..common.style_sheet import StyleSheet


class SettingInterface(ScrollArea):
    """ Setting interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        # setting label
        self.settingLabel = TitleLabel(self.tr("设置"), self)

        # personalization
        self.personalGroup = SettingCardGroup(
            self.tr('个性化'), self.scrollWidget)
        self.micaCard = SwitchSettingCard(
            FluentIcon.TRANSPARENT,
            self.tr('云母效果'),
            self.tr('将半透明效果应用于窗口和表面'),
            cfg.micaEnabled,
            self.personalGroup
        )
        self.themeCard = OptionsSettingCard(
            cfg.themeMode,
            FluentIcon.BRUSH,
            self.tr('应用主题'),
            self.tr("更改应用的外观"),
            texts=[
                self.tr('浅色'), self.tr('深色'),
                self.tr('使用系统设置')
            ],
            parent=self.personalGroup
        )
        self.themeColorCard = CustomColorSettingCard(
            cfg.themeColor,
            FluentIcon.PALETTE,
            self.tr('主题颜色'),
            self.tr('更改应用的主题颜色'),
            self.personalGroup
        )
        self.zoomCard = OptionsSettingCard(
            cfg.dpiScale,
            FluentIcon.ZOOM,
            self.tr("界面缩放"),
            self.tr("更改组件和文字的大小"),
            texts=[
                "100%", "125%", "150%", "175%", "200%",
                self.tr("使用系统设置")
            ],
            parent=self.personalGroup
        )

        # material
        self.materialGroup = SettingCardGroup(
            self.tr('材料'), self.scrollWidget)
        self.blurRadiusCard = RangeSettingCard(
            cfg.blurRadius,
            FluentIcon.ALBUM,
            self.tr('亚克力模糊半径'),
            self.tr('半径越大，图像越模糊'),
            self.materialGroup
        )

        # collab mode
        self.collabGroup = SettingCardGroup(
            self.tr('协同模式'), self.scrollWidget)

        # API Key card
        self.apiKeyCard = SimpleCardWidget(self.collabGroup)
        self.apiKeyCard.setBorderRadius(8)
        self.apiKeyCard.setFixedHeight(72)
        self._apiKeyLayout = QHBoxLayout(self.apiKeyCard)
        self._apiKeyLayout.setContentsMargins(20, 16, 20, 16)
        self._apiKeyLeft = QVBoxLayout()
        self._apiKeyLeft.setSpacing(2)
        self._apiKeyLabel = BodyLabel('StepFun API Key', self.apiKeyCard)
        self._apiKeyHint = CaptionLabel(
            '用于语音识别和 AI 推理，请在 StepFun 平台获取', self.apiKeyCard)
        self._apiKeyHint.setTextColor(
            Qt.gray if isDarkTheme() else QColor(96, 96, 96),
            QColor(160, 160, 160) if isDarkTheme() else QColor(180, 180, 180))
        self._apiKeyLeft.addWidget(self._apiKeyLabel)
        self._apiKeyLeft.addWidget(self._apiKeyHint)
        self.apiKeyEdit = LineEdit(self.apiKeyCard)
        self.apiKeyEdit.setPlaceholderText('输入 API Key')
        self.apiKeyEdit.setEchoMode(QLineEdit.Password)
        self.apiKeyEdit.setFixedWidth(280)
        self.apiKeyEdit.setText(cfg.get(cfg.stepFunApiKey))
        self.apiKeyEdit.textChanged.connect(self.__onApiKeyChanged)
        self._apiKeyLayout.addLayout(self._apiKeyLeft)
        self._apiKeyLayout.addStretch(1)
        self._apiKeyLayout.addWidget(self.apiKeyEdit)

        # Model card
        self.modelCard = SimpleCardWidget(self.collabGroup)
        self.modelCard.setBorderRadius(8)
        self.modelCard.setFixedHeight(72)
        self._modelLayout = QHBoxLayout(self.modelCard)
        self._modelLayout.setContentsMargins(20, 16, 20, 16)
        self._modelLeft = QVBoxLayout()
        self._modelLeft.setSpacing(2)
        self._modelLabel = BodyLabel('AI 推理模型', self.modelCard)
        self._modelHint = CaptionLabel(
            'StepFun 平台支持的模型名称', self.modelCard)
        self._modelHint.setTextColor(
            Qt.gray if isDarkTheme() else QColor(96, 96, 96),
            QColor(160, 160, 160) if isDarkTheme() else QColor(180, 180, 180))
        self._modelLeft.addWidget(self._modelLabel)
        self._modelLeft.addWidget(self._modelHint)
        self.modelEdit = LineEdit(self.modelCard)
        self.modelEdit.setPlaceholderText('模型名称')
        self.modelEdit.setFixedWidth(280)
        self.modelEdit.setText(cfg.get(cfg.collabModel))
        self.modelEdit.textChanged.connect(self.__onModelChanged)
        self._modelLayout.addLayout(self._modelLeft)
        self._modelLayout.addStretch(1)
        self._modelLayout.addWidget(self.modelEdit)

        # DeepSeek semantic composition
        self.deepSeekGroup = SettingCardGroup(
            self.tr('DeepSeek 语义组句'), self.scrollWidget)

        self.deepSeekApiKeyCard = SimpleCardWidget(self.deepSeekGroup)
        self.deepSeekApiKeyCard.setBorderRadius(8)
        self.deepSeekApiKeyCard.setFixedHeight(72)
        deepSeekKeyLayout = QHBoxLayout(self.deepSeekApiKeyCard)
        deepSeekKeyLayout.setContentsMargins(20, 16, 20, 16)
        deepSeekKeyLeft = QVBoxLayout()
        deepSeekKeyLeft.setSpacing(2)
        deepSeekKeyLabel = BodyLabel(
            'DeepSeek API Key', self.deepSeekApiKeyCard)
        deepSeekKeyHint = CaptionLabel(
            '用于将手势候选词匹配并组合成句子',
            self.deepSeekApiKeyCard,
        )
        deepSeekKeyHint.setTextColor(
            Qt.gray if isDarkTheme() else QColor(96, 96, 96),
            QColor(160, 160, 160)
            if isDarkTheme() else QColor(180, 180, 180),
        )
        deepSeekKeyLeft.addWidget(deepSeekKeyLabel)
        deepSeekKeyLeft.addWidget(deepSeekKeyHint)
        self.deepSeekApiKeyEdit = LineEdit(self.deepSeekApiKeyCard)
        self.deepSeekApiKeyEdit.setPlaceholderText(
            '输入 DeepSeek API Key')
        self.deepSeekApiKeyEdit.setEchoMode(QLineEdit.Password)
        self.deepSeekApiKeyEdit.setFixedWidth(280)
        self.deepSeekApiKeyEdit.setText(
            cfg.get(cfg.deepSeekApiKey))
        self.deepSeekApiKeyEdit.textChanged.connect(
            self.__onDeepSeekApiKeyChanged)
        deepSeekKeyLayout.addLayout(deepSeekKeyLeft)
        deepSeekKeyLayout.addStretch(1)
        deepSeekKeyLayout.addWidget(self.deepSeekApiKeyEdit)

        self.deepSeekModelCard = SimpleCardWidget(self.deepSeekGroup)
        self.deepSeekModelCard.setBorderRadius(8)
        self.deepSeekModelCard.setFixedHeight(72)
        deepSeekModelLayout = QHBoxLayout(self.deepSeekModelCard)
        deepSeekModelLayout.setContentsMargins(20, 16, 20, 16)
        deepSeekModelLeft = QVBoxLayout()
        deepSeekModelLeft.setSpacing(2)
        deepSeekModelLabel = BodyLabel(
            'DeepSeek 模型', self.deepSeekModelCard)
        deepSeekModelHint = CaptionLabel(
            '用于语义组句的模型名称',
            self.deepSeekModelCard,
        )
        deepSeekModelHint.setTextColor(
            Qt.gray if isDarkTheme() else QColor(96, 96, 96),
            QColor(160, 160, 160)
            if isDarkTheme() else QColor(180, 180, 180),
        )
        deepSeekModelLeft.addWidget(deepSeekModelLabel)
        deepSeekModelLeft.addWidget(deepSeekModelHint)
        self.deepSeekModelEdit = LineEdit(self.deepSeekModelCard)
        self.deepSeekModelEdit.setPlaceholderText(
            'deepseek-v4-flash')
        self.deepSeekModelEdit.setFixedWidth(280)
        self.deepSeekModelEdit.setText(
            cfg.get(cfg.deepSeekModel))
        self.deepSeekModelEdit.textChanged.connect(
            self.__onDeepSeekModelChanged)
        deepSeekModelLayout.addLayout(deepSeekModelLeft)
        deepSeekModelLayout.addStretch(1)
        deepSeekModelLayout.addWidget(self.deepSeekModelEdit)

        # about
        self.aboutGroup = SettingCardGroup(self.tr('关于'), self.scrollWidget)
        self.aboutCard = PushSettingCard(
            self.tr(''),
            FluentIcon.INFO,
            self.tr('关于'),
            '© ' + self.tr('版权所有') + f" {YEAR}, {AUTHOR}. " +
            self.tr('版本') + " " + VERSION,
            self.aboutGroup
        )
        self.aboutCard.button.setVisible(False)

        self.__initWidget()

    def __initWidget(self):
        self.resize(1000, 800)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 80, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()
        self.setObjectName('settingInterface')

        self.scrollWidget.setObjectName('scrollWidget')
        self.settingLabel.setObjectName('settingLabel')

        # apply style sheet for transparent background
        StyleSheet.SETTING_INTERFACE.apply(self)

        self.micaCard.setEnabled(isWin11())

        self.__initLayout()
        self.__connectSignalToSlot()

    def __initLayout(self):
        self.settingLabel.move(36, 30)

        # add cards to group
        self.personalGroup.addSettingCard(self.micaCard)
        self.personalGroup.addSettingCard(self.themeCard)
        self.personalGroup.addSettingCard(self.themeColorCard)
        self.personalGroup.addSettingCard(self.zoomCard)

        self.materialGroup.addSettingCard(self.blurRadiusCard)

        self.aboutGroup.addSettingCard(self.aboutCard)

        # add setting card group to layout
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 10, 36, 0)
        self.collabGroup.addSettingCard(self.apiKeyCard)
        self.collabGroup.addSettingCard(self.modelCard)
        self.deepSeekGroup.addSettingCard(self.deepSeekApiKeyCard)
        self.deepSeekGroup.addSettingCard(self.deepSeekModelCard)

        self.expandLayout.addWidget(self.personalGroup)
        self.expandLayout.addWidget(self.materialGroup)
        self.expandLayout.addWidget(self.collabGroup)
        self.expandLayout.addWidget(self.deepSeekGroup)
        self.expandLayout.addWidget(self.aboutGroup)

    def __showRestartTooltip(self):
        """ show restart tooltip """
        InfoBar.success(
            self.tr('更新成功'),
            self.tr('配置将在重启后生效'),
            duration=1500,
            parent=self
        )

    def __onApiKeyChanged(self, text: str):
        cfg.set(cfg.stepFunApiKey, text)

    def __onModelChanged(self, text: str):
        cfg.set(cfg.collabModel, text)

    def __onDeepSeekApiKeyChanged(self, text: str):
        cfg.set(cfg.deepSeekApiKey, text.strip())

    def __onDeepSeekModelChanged(self, text: str):
        cfg.set(cfg.deepSeekModel, text.strip())

    def __connectSignalToSlot(self):
        """ connect signal to slot """
        cfg.appRestartSig.connect(self.__showRestartTooltip)

        # personalization
        cfg.themeChanged.connect(setTheme)
        self.themeColorCard.colorChanged.connect(lambda c: setThemeColor(c))
        self.micaCard.checkedChanged.connect(signalBus.micaEnableChanged)
