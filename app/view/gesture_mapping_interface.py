# coding: utf-8
"""手势-快捷键映射界面：录制按键组合并绑定到手势模型。"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
)

from ..common.config import cfg
from ..common.style_sheet import StyleSheet
from ..common import keyboard_simulator

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
_BASE = Path(__file__).resolve().parent.parent / 'hmm_gesture'
_MODEL_DIR = _BASE / 'models'
_PRETRAINED_DIR = _BASE / 'pretrained_models'

# ---------------------------------------------------------------------------
# Qt Key → keyboard_simulator key name 映射
# ---------------------------------------------------------------------------
_MODIFIER_MAP = {
    Qt.Key_Control: 'ctrl',
    Qt.Key_Shift: 'shift',
    Qt.Key_Alt: 'alt',
    Qt.Key_Meta: 'win',
}

_SPECIAL_KEY_MAP = {
    Qt.Key_Enter: 'enter',
    Qt.Key_Return: 'enter',
    Qt.Key_Space: 'space',
    Qt.Key_Tab: 'tab',
    Qt.Key_Escape: 'escape',
    Qt.Key_Backspace: 'backspace',
    Qt.Key_Delete: 'delete',
    Qt.Key_Up: 'up',
    Qt.Key_Down: 'down',
    Qt.Key_Left: 'left',
    Qt.Key_Right: 'right',
    Qt.Key_Home: 'home',
    Qt.Key_End: 'end',
    Qt.Key_PageUp: 'pageup',
    Qt.Key_PageDown: 'pagedown',
}


def _qt_key_to_name(key: int) -> str | None:
    """将 Qt key 枚举值转换为 keyboard_simulator 可用的键名。"""
    if key in _MODIFIER_MAP:
        return _MODIFIER_MAP[key]
    if key in _SPECIAL_KEY_MAP:
        return _SPECIAL_KEY_MAP[key]
    # 字母键 A-Z
    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(key).lower()
    # 数字键 0-9
    if Qt.Key_0 <= key <= Qt.Key_9:
        return chr(key)
    # 功能键 F1-F12
    if Qt.Key_F1 <= key <= Qt.Key_F12:
        return f'f{key - Qt.Key_F1 + 1}'
    return None


# ═══════════════════════════════════════════════════════════════════════════
# KeyRecorderDialog — 录制快捷键组合
# ═══════════════════════════════════════════════════════════════════════════
class KeyRecorderDialog(MessageBoxBase):
    """模态对话框：用户按下快捷键组合后确认。"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._modifiers: set[str] = set()
        self._final_keys: list[str] = []

        # ---- UI ----
        # MessageBoxBase 没有内置 titleLabel，需自行创建标题标签
        self._titleLabel = SubtitleLabel('录制快捷键', self.widget)
        self._titleLabel.setAlignment(Qt.AlignCenter)

        self._hintLabel = CaptionLabel('请按下快捷键组合…', self.widget)
        self._hintLabel.setAlignment(Qt.AlignCenter)

        self._keyDisplayLabel = QLabel('—', self.widget)
        self._keyDisplayLabel.setObjectName('keyDisplayLabel')
        self._keyDisplayLabel.setAlignment(Qt.AlignCenter)
        self._keyDisplayLabel.setMinimumHeight(80)

        self.viewLayout.addWidget(self._titleLabel)
        self.viewLayout.addWidget(self._hintLabel)
        self.viewLayout.addWidget(self._keyDisplayLabel)

        # 设置按钮文本
        self.yesButton.setText('确定')
        self.cancelButton.setText('取消')

        # MessageBoxBase 默认会把焦点设在「确定」按钮上，直接重写对话框的
        # keyPressEvent 收不到按键。改用在所有可获焦控件上安装事件过滤器，
        # 确保无论焦点落在哪个控件上都能捕获按键。
        self.widget.setFocusPolicy(Qt.StrongFocus)
        for target in (self.widget, self.yesButton, self.cancelButton):
            target.installEventFilter(self)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        # 必须在对话框真正显示之后设置焦点才会生效
        self.widget.setFocus()

    # ---- 键盘事件（通过事件过滤器捕获） ----
    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.KeyPress:
            if self._handleKeyPress(event):
                return True
        elif event.type() == QEvent.KeyRelease:
            if self._handleKeyRelease(event):
                return True
        return super().eventFilter(obj, event)

    def _handleKeyPress(self, event) -> bool:
        key = event.key()
        name = _qt_key_to_name(key)
        if name is None:
            return False

        if key in _MODIFIER_MAP:
            self._modifiers.add(name)
            self._hintLabel.setText('已按下修饰键，请继续按主键…')
        else:
            # 非修饰键：组合当前修饰键 + 该键
            keys = sorted(self._modifiers) + [name]
            self._final_keys = keys
            display = ' + '.join(k.capitalize() for k in keys)
            self._keyDisplayLabel.setText(display)
            self._hintLabel.setText('录制成功，点击「确定」保存')
        return True

    def _handleKeyRelease(self, event) -> bool:
        key = event.key()
        name = _qt_key_to_name(key)
        if name is not None and key in _MODIFIER_MAP:
            self._modifiers.discard(name)
            return True
        return False

    # ---- 公共接口 ----
    def getKeys(self) -> list[str]:
        """返回录制到的按键列表，如 ``["ctrl", "enter"]``。"""
        return list(self._final_keys)


# ═══════════════════════════════════════════════════════════════════════════
# GestureMappingInterface — 手势快捷键映射主界面
# ═══════════════════════════════════════════════════════════════════════════
class GestureMappingInterface(ScrollArea):
    """管理手势 → 快捷键映射。"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._recorded_keys: list[str] = []

        # ---- 根容器 ----
        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        self._buildHeader()
        self._buildAddCard()
        self._buildTableCard()
        self._initWidget()
        self._connectSignals()
        self._loadGestures()
        self._loadMappings()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _buildHeader(self):
        self.titleLabel = TitleLabel('手势快捷键映射', self.view)
        self.titleLabel.setObjectName('titleLabel')
        self.subtitleLabel = CaptionLabel(
            '为每个手势分配一个快捷键组合，识别到手势后自动触发',
            self.view,
        )

    def _buildAddCard(self):
        self.addCard = SimpleCardWidget(self.view)
        self.addCard.setBorderRadius(12)

        self.gestureCombo = ComboBox(self.addCard)
        self.gestureCombo.setMinimumWidth(160)
        self.gestureCombo.setPlaceholderText('选择手势')

        self.recordBtn = PrimaryPushButton('录制快捷键', self.addCard)
        self.recordBtn.setFixedWidth(120)

        self.recordedKeysLabel = BodyLabel('未录制', self.addCard)

        self.addMappingBtn = PushButton('添加映射', self.addCard)
        self.addMappingBtn.setFixedWidth(100)

        layout = QHBoxLayout(self.addCard)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(BodyLabel('手势', self.addCard))
        layout.addWidget(self.gestureCombo)
        layout.addWidget(self.recordBtn)
        layout.addWidget(self.recordedKeysLabel, 1)
        layout.addWidget(self.addMappingBtn)

    def _buildTableCard(self):
        self.tableCard = SimpleCardWidget(self.view)
        self.tableCard.setBorderRadius(12)

        self.mappingTable = TableWidget(self.tableCard)
        self.mappingTable.setColumnCount(3)
        self.mappingTable.setHorizontalHeaderLabels(
            ['手势名称', '快捷键', '操作'])
        self.mappingTable.setSelectionBehavior(
            QAbstractItemView.SelectRows)
        self.mappingTable.setSelectionMode(
            QAbstractItemView.SingleSelection)
        self.mappingTable.setEditTriggers(
            QAbstractItemView.NoEditTriggers)
        self.mappingTable.verticalHeader().setVisible(False)
        self.mappingTable.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.mappingTable.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.mappingTable.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents)

        self.emptyHintLabel = QLabel(
            '暂无已训练的手势模型，请先在「手语实验室（Beta）」页面完成训练',
            self.tableCard,
        )
        self.emptyHintLabel.setObjectName('emptyHintLabel')
        self.emptyHintLabel.setAlignment(Qt.AlignCenter)
        self.emptyHintLabel.setWordWrap(True)

        cardLayout = QVBoxLayout(self.tableCard)
        cardLayout.setContentsMargins(20, 16, 20, 16)
        cardLayout.setSpacing(10)
        cardLayout.addWidget(self.mappingTable)
        cardLayout.addWidget(self.emptyHintLabel)

    def _initWidget(self):
        self.setObjectName('gestureMappingInterface')
        self.view.setObjectName('view')
        StyleSheet.GESTURE_MAPPING_INTERFACE.apply(self)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        self.vBoxLayout.setContentsMargins(36, 28, 36, 36)
        self.vBoxLayout.setSpacing(14)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.addCard)
        self.vBoxLayout.addWidget(self.tableCard)
        self.vBoxLayout.addStretch(1)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

    def _connectSignals(self):
        self.recordBtn.clicked.connect(self._onRecordClicked)
        self.addMappingBtn.clicked.connect(self._onAddClicked)

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def _loadGestures(self):
        """扫描 models/ 和 pretrained_models/ 目录，获取手势名填入 ComboBox。"""
        names: set[str] = set()
        for directory in (_MODEL_DIR, _PRETRAINED_DIR):
            if not directory.is_dir():
                continue
            for pkl in directory.glob('*.pkl'):
                stem = pkl.stem
                # 去掉 -hmm 后缀（pretrained_models 中的命名惯例）
                if stem.endswith('-hmm'):
                    stem = stem[:-4]
                names.add(stem)

        self.gestureCombo.clear()
        sorted_names = sorted(names, key=str.casefold)
        if sorted_names:
            self.gestureCombo.addItems(sorted_names)
            self.gestureCombo.setEnabled(True)
            self.emptyHintLabel.hide()
            self.mappingTable.show()
        else:
            self.gestureCombo.setEnabled(False)
            self.emptyHintLabel.show()
            self.mappingTable.hide()

    def _loadMappings(self):
        """从 cfg 加载映射并填入表格。"""
        self._refreshTable()

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _onRecordClicked(self):
        """弹出 KeyRecorderDialog，获取录制结果。"""
        dialog = KeyRecorderDialog(self.window())
        if dialog.exec():
            keys = dialog.getKeys()
            if keys:
                self._recorded_keys = keys
                display = ' + '.join(k.capitalize() for k in keys)
                self.recordedKeysLabel.setText(display)
            else:
                self.recordedKeysLabel.setText('未录制')

    def _onAddClicked(self):
        """验证后保存映射到 cfg 并刷新表格。"""
        gesture = self.gestureCombo.currentText().strip()
        if not gesture:
            self._showWarning('未选择手势', '请先在下拉框中选择一个手势')
            return
        if not self._recorded_keys:
            self._showWarning('未录制快捷键', '请先点击「录制快捷键」')
            return

        mappings: dict = cfg.get(cfg.gestureKeyMappings) or {}
        mappings[gesture] = list(self._recorded_keys)
        cfg.set(cfg.gestureKeyMappings, mappings)

        display = ' + '.join(k.capitalize() for k in self._recorded_keys)
        InfoBar.success(
            '映射已添加',
            f'{gesture} → {display}',
            parent=self.window(),
            duration=2500,
            position=InfoBarPosition.TOP_RIGHT,
        )
        self._recorded_keys = []
        self.recordedKeysLabel.setText('未录制')
        self._refreshTable()

    def _onDeleteClicked(self, gesture_name: str):
        """删除该行映射并更新 cfg。"""
        mappings: dict = cfg.get(cfg.gestureKeyMappings) or {}
        if gesture_name in mappings:
            del mappings[gesture_name]
            cfg.set(cfg.gestureKeyMappings, mappings)
            InfoBar.info(
                '映射已删除',
                f'已移除 {gesture_name} 的快捷键映射',
                parent=self.window(),
                duration=2000,
                position=InfoBarPosition.TOP_RIGHT,
            )
            self._refreshTable()

    # ------------------------------------------------------------------
    # 表格刷新
    # ------------------------------------------------------------------
    def _refreshTable(self):
        """根据当前 cfg 数据刷新表格内容。"""
        mappings: dict = cfg.get(cfg.gestureKeyMappings) or {}
        rows = list(mappings.items())

        self.mappingTable.setRowCount(len(rows))
        for row, (gesture, keys) in enumerate(rows):
            # 手势名称
            name_item = QTableWidgetItem(gesture)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.mappingTable.setItem(row, 0, name_item)

            # 快捷键
            key_display = ' + '.join(k.capitalize() for k in keys)
            key_item = QTableWidgetItem(key_display)
            key_item.setFlags(key_item.flags() & ~Qt.ItemIsEditable)
            self.mappingTable.setItem(row, 1, key_item)

            # 操作列 — 删除按钮
            delete_btn = PushButton('删除', self.mappingTable)
            delete_btn.setFixedWidth(80)
            # 使用默认参数绑定当前手势名
            delete_btn.clicked.connect(
                lambda _checked, g=gesture: self._onDeleteClicked(g))
            self.mappingTable.setCellWidget(row, 2, delete_btn)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _showWarning(self, title: str, message: str):
        InfoBar.warning(
            title, message,
            parent=self.window(),
            duration=3000,
            position=InfoBarPosition.TOP_RIGHT,
        )
