# coding: utf-8
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFrame

from qfluentwidgets import (ScrollArea, ExpandLayout, FluentIcon,
                            PushButton, PrimaryPushButton, TransparentPushButton,
                            TogglePushButton, PillPushButton, HyperlinkButton,
                            CheckBox, RadioButton, Slider, SwitchButton,
                            ComboBox, EditableComboBox, LineEdit, PasswordLineEdit,
                            SearchLineEdit, TextEdit, SpinBox, DoubleSpinBox,
                            StrongBodyLabel, BodyLabel, TitleLabel,
                            InfoBar, InfoBarPosition, isDarkTheme, Theme, toggleTheme,
                            ToolTipFilter, ToolTipPosition)

from ..common.style_sheet import StyleSheet


class CardWidget(QFrame):
    """ Card widget for grouping components """

    def __init__(self, title, parent=None):
        super().__init__(parent=parent)
        self.titleLabel = StrongBodyLabel(title, self)
        self.vBoxLayout = QVBoxLayout(self)

        self.setObjectName('card')
        self.vBoxLayout.setSpacing(16)
        self.vBoxLayout.setContentsMargins(20, 20, 20, 20)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.setAlignment(Qt.AlignTop)


class BasicInterface(ScrollArea):
    """ Basic input interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self.titleLabel = TitleLabel('基础组件', self)

        self.__initWidget()
        self.__createCards()

    def __initWidget(self):
        self.setObjectName('basicInterface')
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 80, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)

        self.scrollWidget.setObjectName('scrollWidget')
        self.titleLabel.setObjectName('titleLabel')

        # apply style sheet for transparent background
        StyleSheet.BASIC_INTERFACE.apply(self)

        self.titleLabel.move(36, 30)

        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 10, 36, 0)

    def __createCards(self):
        # Button card
        buttonCard = CardWidget('按钮 Buttons', self.scrollWidget)
        btnLayout = QHBoxLayout()
        btnLayout.setSpacing(12)

        pushBtn = PushButton('普通按钮', buttonCard)
        primaryBtn = PrimaryPushButton('主要按钮', buttonCard)
        toggleBtn = TogglePushButton('切换按钮', buttonCard)
        pillBtn = PillPushButton('药丸按钮', buttonCard)

        pushBtn.installEventFilter(ToolTipFilter(pushBtn, 500, ToolTipPosition.TOP))
        pushBtn.setToolTip('这是一个普通按钮')

        btnLayout.addWidget(pushBtn)
        btnLayout.addWidget(primaryBtn)
        btnLayout.addWidget(toggleBtn)
        btnLayout.addWidget(pillBtn)
        btnLayout.addStretch(1)

        buttonCard.vBoxLayout.addLayout(btnLayout)
        self.expandLayout.addWidget(buttonCard)

        # Checkbox & Radio card
        checkRadioCard = CardWidget('复选框与单选按钮', self.scrollWidget)
        crLayout = QHBoxLayout()
        crLayout.setSpacing(24)

        checkLayout = QVBoxLayout()
        checkLayout.setSpacing(8)
        cb1 = CheckBox('选项 A', checkRadioCard)
        cb2 = CheckBox('选项 B', checkRadioCard)
        cb3 = CheckBox('选项 C', checkRadioCard)
        cb1.setChecked(True)
        checkLayout.addWidget(cb1)
        checkLayout.addWidget(cb2)
        checkLayout.addWidget(cb3)

        radioLayout = QVBoxLayout()
        radioLayout.setSpacing(8)
        rb1 = RadioButton('选择 1', checkRadioCard)
        rb2 = RadioButton('选择 2', checkRadioCard)
        rb3 = RadioButton('选择 3', checkRadioCard)
        rb1.setChecked(True)
        radioLayout.addWidget(rb1)
        radioLayout.addWidget(rb2)
        radioLayout.addWidget(rb3)

        crLayout.addLayout(checkLayout)
        crLayout.addLayout(radioLayout)
        crLayout.addStretch(1)

        checkRadioCard.vBoxLayout.addLayout(crLayout)
        self.expandLayout.addWidget(checkRadioCard)

        # Slider & Switch card
        sliderCard = CardWidget('滑块与开关', self.scrollWidget)
        sliderLayout = QVBoxLayout()
        sliderLayout.setSpacing(12)

        self.slider = Slider(Qt.Horizontal, sliderCard)
        self.slider.setRange(0, 100)
        self.slider.setValue(50)
        self.slider.setFixedWidth(300)

        switchLayout = QHBoxLayout()
        switchLayout.setSpacing(16)
        self.switchBtn = SwitchButton(sliderCard)
        self.switchBtn.setOnText('开')
        self.switchBtn.setOffText('关')
        switchLabel = BodyLabel('功能开关', sliderCard)
        switchLayout.addWidget(switchLabel)
        switchLayout.addWidget(self.switchBtn)
        switchLayout.addStretch(1)

        sliderLayout.addWidget(self.slider)
        sliderLayout.addLayout(switchLayout)

        sliderCard.vBoxLayout.addLayout(sliderLayout)
        self.expandLayout.addWidget(sliderCard)

        # Text input card
        textCard = CardWidget('文本输入', self.scrollWidget)
        textLayout = QVBoxLayout()
        textLayout.setSpacing(12)

        self.lineEdit = LineEdit(textCard)
        self.lineEdit.setPlaceholderText('请输入文本...')
        self.lineEdit.setFixedWidth(300)

        self.searchEdit = SearchLineEdit(textCard)
        self.searchEdit.setPlaceholderText('搜索...')
        self.searchEdit.setFixedWidth(300)

        self.passwordEdit = PasswordLineEdit(textCard)
        self.passwordEdit.setPlaceholderText('请输入密码...')
        self.passwordEdit.setFixedWidth(300)

        textLayout.addWidget(self.lineEdit)
        textLayout.addWidget(self.searchEdit)
        textLayout.addWidget(self.passwordEdit)

        textCard.vBoxLayout.addLayout(textLayout)
        self.expandLayout.addWidget(textCard)

        # ComboBox card
        comboCard = CardWidget('下拉框', self.scrollWidget)
        comboLayout = QVBoxLayout()
        comboLayout.setSpacing(12)

        self.comboBox = ComboBox(comboCard)
        self.comboBox.addItems(['Python', 'JavaScript', 'TypeScript', 'Rust', 'Go'])
        self.comboBox.setFixedWidth(200)

        self.editableCombo = EditableComboBox(comboCard)
        self.editableCombo.addItems(['北京', '上海', '广州', '深圳', '杭州'])
        self.editableCombo.setPlaceholderText('选择或输入城市...')
        self.editableCombo.setFixedWidth(200)

        comboLayout.addWidget(self.comboBox)
        comboLayout.addWidget(self.editableCombo)

        comboCard.vBoxLayout.addLayout(comboLayout)
        self.expandLayout.addWidget(comboCard)

        # Number input card
        numberCard = CardWidget('数字输入', self.scrollWidget)
        numLayout = QHBoxLayout()
        numLayout.setSpacing(16)

        self.spinBox = SpinBox(numberCard)
        self.spinBox.setRange(0, 100)
        self.spinBox.setValue(50)

        self.doubleSpinBox = DoubleSpinBox(numberCard)
        self.doubleSpinBox.setRange(0.0, 100.0)
        self.doubleSpinBox.setValue(3.14)
        self.doubleSpinBox.setDecimals(2)

        numLayout.addWidget(self.spinBox)
        numLayout.addWidget(self.doubleSpinBox)
        numLayout.addStretch(1)

        numberCard.vBoxLayout.addLayout(numLayout)
        self.expandLayout.addWidget(numberCard)

        # InfoBar demo card
        infoCard = CardWidget('信息栏 InfoBar', self.scrollWidget)
        infoBtnLayout = QHBoxLayout()
        infoBtnLayout.setSpacing(12)

        infoBtn = PushButton('信息', infoCard)
        successBtn = PushButton('成功', infoCard)
        warnBtn = PushButton('警告', infoCard)
        errorBtn = PushButton('错误', infoCard)

        infoBtn.clicked.connect(lambda: InfoBar.info(
            '提示', '这是一条信息通知。', parent=self, duration=3000, position=InfoBarPosition.TOP_RIGHT))
        successBtn.clicked.connect(lambda: InfoBar.success(
            '成功', '操作已成功完成！', parent=self, duration=3000, position=InfoBarPosition.TOP_RIGHT))
        warnBtn.clicked.connect(lambda: InfoBar.warning(
            '警告', '请注意相关事项。', parent=self, duration=3000, position=InfoBarPosition.TOP_RIGHT))
        errorBtn.clicked.connect(lambda: InfoBar.error(
            '错误', '发生了一个错误，请重试。', parent=self, duration=3000, position=InfoBarPosition.TOP_RIGHT))

        infoBtnLayout.addWidget(infoBtn)
        infoBtnLayout.addWidget(successBtn)
        infoBtnLayout.addWidget(warnBtn)
        infoBtnLayout.addWidget(errorBtn)
        infoBtnLayout.addStretch(1)

        infoCard.vBoxLayout.addLayout(infoBtnLayout)
        self.expandLayout.addWidget(infoCard)
