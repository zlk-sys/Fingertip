# coding: utf-8
"""Integrated collection, training and live HMM gesture recognition page."""

from __future__ import annotations

import datetime
import shutil
from pathlib import Path

import numpy as np
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    IconWidget,
    IndeterminateProgressBar,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
    TextEdit,
    TitleLabel,
    TogglePushButton,
)
from qfluentwidgets import FluentIcon as FIF

from ..common.signal_bus import signalBus
from ..common.style_sheet import StyleSheet
from ..hmm_gesture import HMMRecognizer, TrainingResult
from ..hmm_gesture import save_gesture, train_directory
from ..sdk.ring_sound import (
    TimeoutError as RingTimeoutError,
    start_sensor_report,
    stop_sensor_report,
    wait_sensor_data,
)


_STREAM_MODES = ('sensor', 'level', 'drawing', 'hmm_gesture')
_RESOURCE_DIR = Path(__file__).resolve().parents[1] / 'hmm_gesture'
_DATA_DIR = _RESOURCE_DIR / 'gesture_data'
_MODEL_DIR = _RESOURCE_DIR / 'models'


def _get_shared_transport():
    from . import connect_interface
    return (
        connect_interface.shared_client,
        connect_interface.async_loop_thread,
    )


class _GestureStreamThread(QThread):
    """Read the shared SDK IMU stream without opening a second BLE link."""

    batchReceived = pyqtSignal(object)
    startedSuccessfully = pyqtSignal(object)
    error = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client = None
        self._loop_thread = None
        self._running = False
        self._send_stop = True

    def set_transport(self, client, loop_thread):
        self._client = client
        self._loop_thread = loop_thread

    def run(self):
        if self._client is None or self._loop_thread is None:
            self.error.emit('BLE 客户端不可用')
            self.stopped.emit()
            return

        self._running = True
        self._send_stop = True
        try:
            start_info = self._loop_thread.run_coro(
                start_sensor_report(self._client, timeout_s=10.0),
                timeout=12.0,
            )
            self.startedSuccessfully.emit(start_info)
        except Exception as exc:
            self.error.emit(f'启动 IMU 上报失败：{exc}')
            self.stopped.emit()
            return

        while self._running:
            try:
                batch = self._loop_thread.run_coro(
                    wait_sensor_data(self._client, timeout_s=2.0),
                    timeout=3.0,
                )
                self.batchReceived.emit(batch)
            except (TimeoutError, RingTimeoutError):
                continue
            except Exception as exc:
                if self._running:
                    self.error.emit(f'读取 IMU 数据失败：{exc}')
                break

        if self._send_stop:
            try:
                self._loop_thread.run_coro(
                    stop_sensor_report(self._client, timeout_s=10.0),
                    timeout=12.0,
                )
            except Exception:
                pass
        self.stopped.emit()

    def stop_stream(self, send_stop=True):
        self._send_stop = bool(send_stop)
        self._running = False


class _TrainingThread(QThread):
    progress = pyqtSignal(str)
    completed = pyqtSignal(object)
    error = pyqtSignal(str)

    def run(self):
        try:
            result = train_directory(
                _DATA_DIR,
                _MODEL_DIR,
                progress=self.progress.emit,
            )
            self.completed.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class GestureInterface(ScrollArea):
    """Three-stage HMM workflow adapted to the desktop application."""

    MODE_ID = 'hmm_gesture'

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _MODEL_DIR.mkdir(parents=True, exist_ok=True)

        self._connected = False
        self._active = False
        self._operation = None
        self._recording = False
        self._capture_repetitions: list[np.ndarray] = []
        self._current_samples: list[list[int]] = []
        self._target_repetitions = 5
        self._sample_rate_hz = 25.0
        self._recognizer = None
        self._recognition_count = 0
        self._history_lines: list[str] = []
        self._training_thread = None

        self._stream = _GestureStreamThread(self)
        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        self._buildHeader()
        self._buildStatusCard()
        self._buildCollectionCard()
        self._buildTrainingCard()
        self._buildModelCard()
        self._buildRecognitionCard()
        self._initWidget()
        self._connectSignals()
        self._refreshResourceSummary()

    def _buildHeader(self):
        self.titleLabel = TitleLabel('HMM 手势实验室', self.view)
        self.subtitleLabel = CaptionLabel(
            '按照「采集 → 训练 → 识别」流程创建并实时使用戒指手势模型',
            self.view,
        )
        self.subtitleLabel.setTextColor(
            QColor(96, 96, 96), QColor(180, 180, 180))

    def _buildStatusCard(self):
        self.statusCard = SimpleCardWidget(self.view)
        self.statusCard.setBorderRadius(12)
        self.statusCard.setFixedHeight(92)
        # self.statusIcon = IconWidget(FIF.ROBOT, self.statusCard)
        # self.statusIcon.setFixedSize(38, 38)
        self.statusLabel = StrongBodyLabel(
            '等待连接戒指', self.statusCard)
        self.statusLabel.setObjectName('gestureStatusLabel')
        self.statusLabel.setProperty('active', False)
        self.statusHint = CaptionLabel(
            '实时采集和识别要求戒指处于手势模式', self.statusCard)
        self.statusHint.setTextColor(
            QColor(96, 96, 96), QColor(180, 180, 180))
        self.resourceLabel = BodyLabel('', self.statusCard)

        layout = QHBoxLayout(self.statusCard)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(14)
        # layout.addWidget(self.statusIcon, 0, Qt.AlignVCenter)
        textLayout = QVBoxLayout()
        textLayout.setSpacing(3)
        textLayout.addWidget(self.statusLabel)
        textLayout.addWidget(self.statusHint)
        layout.addLayout(textLayout, 1)
        layout.addWidget(self.resourceLabel, 0, Qt.AlignVCenter)

    def _buildCollectionCard(self):
        self.collectionSection = SubtitleLabel(
            '1 · 采集手势数据', self.view)
        self.collectionCard = SimpleCardWidget(self.view)
        self.collectionCard.setBorderRadius(12)
        self.collectionCard.setFixedHeight(150)

        self.gestureNameEdit = LineEdit(self.collectionCard)
        self.gestureNameEdit.setPlaceholderText('例如：向右、打响指')
        self.gestureNameEdit.setFixedWidth(190)
        self.repetitionSpin = SpinBox(self.collectionCard)
        self.repetitionSpin.setRange(2, 20)
        self.repetitionSpin.setValue(5)
        self.repetitionSpin.setFixedWidth(120)
        self.captureSessionBtn = PrimaryPushButton(
            '开始采集流程', self.collectionCard)
        self.captureSessionBtn.setFixedWidth(120)
        self.recordBtn = TogglePushButton(
            '开始本次录制', self.collectionCard)
        self.recordBtn.setFixedWidth(120)
        self.recordBtn.setEnabled(False)
        self.captureProgressLabel = StrongBodyLabel(
            '尚未开始', self.collectionCard)
        self.captureHint = CaptionLabel(
            '每次点击开始后完成一个动作，再点击结束；至少录制 2 次，建议 5 次以上',
            self.collectionCard,
        )
        self.captureHint.setTextColor(
            QColor(96, 96, 96), QColor(180, 180, 180))

        layout = QVBoxLayout(self.collectionCard)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        controls = QHBoxLayout()
        controls.setSpacing(10)
        controls.addWidget(BodyLabel('手势名称', self.collectionCard))
        controls.addWidget(self.gestureNameEdit)
        controls.addWidget(BodyLabel('重复次数', self.collectionCard))
        controls.addWidget(self.repetitionSpin)
        controls.addWidget(self.captureSessionBtn)
        controls.addWidget(self.recordBtn)
        controls.addStretch(1)
        controls.addWidget(self.captureProgressLabel)
        layout.addLayout(controls)
        layout.addWidget(self.captureHint)

    def _buildTrainingCard(self):
        self.trainingSection = SubtitleLabel(
            '2 · 训练 HMM 模型', self.view)
        self.trainingCard = SimpleCardWidget(self.view)
        self.trainingCard.setBorderRadius(12)
        self.trainingCard.setFixedHeight(118)

        self.trainBtn = PrimaryPushButton(
            '训练全部数据', self.trainingCard)
        self.trainBtn.setFixedWidth(120)
        self.trainingProgress = IndeterminateProgressBar(self.trainingCard)
        self.trainingProgress.setFixedWidth(180)
        self.trainingProgress.setVisible(False)
        self.trainingStatusLabel = BodyLabel(
            '使用 SDK 默认参数：6 个状态、10 Hz 低通、8 帧特征窗口',
            self.trainingCard,
        )
        self.trainingStatusLabel.setTextColor(
            QColor(96, 96, 96), QColor(180, 180, 180))

        layout = QVBoxLayout(self.trainingCard)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        controls = QHBoxLayout()
        controls.addWidget(self.trainBtn)
        controls.addSpacing(12)
        controls.addWidget(self.trainingProgress)
        controls.addStretch(1)
        layout.addLayout(controls)
        layout.addWidget(self.trainingStatusLabel)

    def _buildRecognitionCard(self):
        self.recognitionSection = SubtitleLabel(
            '4 · 实时识别', self.view)
        self.recognitionCard = SimpleCardWidget(self.view)
        self.recognitionCard.setBorderRadius(12)
        self.recognitionCard.setMinimumHeight(260)

        self.recognitionBtn = PrimaryPushButton(
            '开始实时识别', self.recognitionCard)
        self.recognitionBtn.setFixedWidth(130)
        self.latestResultLabel = StrongBodyLabel(
            '等待识别结果', self.recognitionCard)
        self.latestResultLabel.setObjectName('gestureResultLabel')
        self.recognitionHint = CaptionLabel(
            '系统会自动检测动作片段，并显示最佳模型和置信度',
            self.recognitionCard,
        )
        self.recognitionHint.setTextColor(
            QColor(96, 96, 96), QColor(180, 180, 180))
        self.recognitionDiagnosticLabel = CaptionLabel(
            '诊断：等待动作片段', self.recognitionCard)
        self.recognitionDiagnosticLabel.setObjectName(
            'gestureDiagnosticLabel')
        self.recognitionDiagnosticLabel.setWordWrap(True)
        self.historyEdit = TextEdit(self.recognitionCard)
        self.historyEdit.setReadOnly(True)
        self.historyEdit.setPlaceholderText('识别历史将在这里显示')
        self.historyEdit.setFixedHeight(105)

        layout = QVBoxLayout(self.recognitionCard)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(9)
        controls = QHBoxLayout()
        controls.addWidget(self.recognitionBtn)
        controls.addSpacing(14)
        controls.addWidget(self.latestResultLabel)
        controls.addStretch(1)
        layout.addLayout(controls)
        layout.addWidget(self.recognitionHint)
        layout.addWidget(self.recognitionDiagnosticLabel)
        layout.addWidget(self.historyEdit)

    def _buildModelCard(self):
        self.modelSection = SubtitleLabel(
            '3 · 管理模型', self.view)
        self.modelCard = SimpleCardWidget(self.view)
        self.modelCard.setBorderRadius(12)
        self.modelCard.setMinimumHeight(250)

        self.modelTable = TableWidget(self.modelCard)
        self.modelTable.setColumnCount(4)
        self.modelTable.setHorizontalHeaderLabels(
            ['名称', '状态', '模型大小', '更新时间'])
        self.modelTable.setSelectionBehavior(
            QAbstractItemView.SelectRows)
        self.modelTable.setSelectionMode(
            QAbstractItemView.SingleSelection)
        self.modelTable.setEditTriggers(
            QAbstractItemView.NoEditTriggers)
        self.modelTable.verticalHeader().setVisible(False)
        self.modelTable.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.modelTable.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents)
        self.modelTable.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents)
        self.modelTable.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents)
        self.modelTable.setFixedHeight(155)

        self.importModelBtn = PrimaryPushButton(
            '导入', self.modelCard)
        self.renameModelBtn = PushButton('重命名', self.modelCard)
        self.exportModelBtn = PushButton('导出', self.modelCard)
        self.deleteModelBtn = PushButton('删除', self.modelCard)
        self.refreshModelBtn = PushButton('刷新', self.modelCard)
        self.modelHint = CaptionLabel(
            '识别标签取自模型文件名；仅导入来源可信的 .pkl 模型',
            self.modelCard,
        )
        self.modelHint.setTextColor(
            QColor(96, 96, 96), QColor(180, 180, 180))

        layout = QVBoxLayout(self.modelCard)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(9)
        layout.addWidget(self.modelTable)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.importModelBtn)
        controls.addWidget(self.renameModelBtn)
        controls.addWidget(self.exportModelBtn)
        controls.addWidget(self.deleteModelBtn)
        controls.addWidget(self.refreshModelBtn)
        controls.addStretch(1)
        controls.addWidget(self.modelHint)
        layout.addLayout(controls)

    def _initWidget(self):
        self.setObjectName('gestureInterface')
        self.view.setObjectName('view')
        StyleSheet.HMM_GESTURE_INTERFACE.apply(self)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        self.vBoxLayout.setContentsMargins(36, 28, 36, 36)
        self.vBoxLayout.setSpacing(14)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.statusCard)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.collectionSection)
        self.vBoxLayout.addWidget(self.collectionCard)
        self.vBoxLayout.addWidget(self.trainingSection)
        self.vBoxLayout.addWidget(self.trainingCard)
        self.vBoxLayout.addWidget(self.modelSection)
        self.vBoxLayout.addWidget(self.modelCard)
        self.vBoxLayout.addWidget(self.recognitionSection)
        self.vBoxLayout.addWidget(self.recognitionCard)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

    def _connectSignals(self):
        self.captureSessionBtn.clicked.connect(self._onCaptureSession)
        self.recordBtn.toggled.connect(self._onRecordToggled)
        self.trainBtn.clicked.connect(self._onTrain)
        self.importModelBtn.clicked.connect(self._onImportModel)
        self.renameModelBtn.clicked.connect(self._onRenameModel)
        self.exportModelBtn.clicked.connect(self._onExportModel)
        self.deleteModelBtn.clicked.connect(self._onDeleteModel)
        self.refreshModelBtn.clicked.connect(self._refreshModelTable)
        self.modelTable.itemSelectionChanged.connect(
            self._updateModelActions)
        self.recognitionBtn.clicked.connect(self._onRecognition)

        self._stream.batchReceived.connect(self._onBatch)
        self._stream.startedSuccessfully.connect(self._onStreamStarted)
        self._stream.error.connect(self._onStreamError)
        self._stream.stopped.connect(self._onStreamStopped)

        signalBus.deviceConnected.connect(self._onDeviceConnected)
        signalBus.deviceDisconnected.connect(self._onDeviceDisconnected)
        signalBus.modeStarted.connect(self._onOtherModeStarted)

    def _onDeviceConnected(self, name, address):
        self._connected = True
        if not self._active:
            self._setStatus(
                '戒指已连接',
                f'{name} ({address})；请确认设备处于手势模式',
                active=False,
            )

    def _onDeviceDisconnected(self):
        self._connected = False
        if self._stream.isRunning():
            self._stream.stop_stream(send_stop=False)
        self._setStatus(
            '等待连接戒指',
            '实时采集和识别要求戒指处于手势模式',
            active=False,
        )

    def _onCaptureSession(self):
        if self._operation == 'capture':
            self._capture_completed(False)
            self._stream.stop_stream()
            return
        if self._operation is not None:
            self._showWarning('操作进行中', '请先停止当前实时识别')
            return
        name = self.gestureNameEdit.text().strip()
        if not name:
            self._showWarning('缺少手势名称', '请先填写要采集的手势名称')
            return
        self._target_repetitions = self.repetitionSpin.value()
        self._capture_repetitions = []
        self._current_samples = []
        self._startStream('capture')

    def _onRecordToggled(self, checked):
        if self._operation != 'capture' or not self._active:
            return
        if checked:
            self._current_samples = []
            self._recording = True
            current = len(self._capture_repetitions) + 1
            self.recordBtn.setText('结束本次录制')
            self.captureProgressLabel.setText(
                f'正在录制 {current}/{self._target_repetitions}')
            self._setStatus(
                '正在采集手势',
                '现在完成动作，结束后点击「结束本次录制」',
                active=True,
            )
            return

        self._recording = False
        self.recordBtn.setText('开始本次录制')
        minimum = max(12, round(self._sample_rate_hz * 0.4))
        if len(self._current_samples) < minimum:
            self._showWarning(
                '录制太短',
                f'仅收到 {len(self._current_samples)} 帧，'
                f'至少需要 {minimum} 帧，请重新录制',
            )
            self._current_samples = []
            self._updateCaptureReadyText()
            return

        repetition = np.asarray(self._current_samples, dtype=np.int16)
        self._capture_repetitions.append(repetition)
        self._current_samples = []
        if len(self._capture_repetitions) >= self._target_repetitions:
            try:
                path = save_gesture(
                    self.gestureNameEdit.text().strip(),
                    self._capture_repetitions,
                    _DATA_DIR,
                    self._sample_rate_hz,
                )
            except Exception as exc:
                self._showError('保存采集数据失败', str(exc))
                self._capture_completed(False)
            else:
                self._capture_completed(True)
                self._refreshResourceSummary()
                InfoBar.success(
                    '采集完成',
                    f'已保存 {len(self._capture_repetitions)} 次录制：'
                    f'{path.name}',
                    parent=self.window(), duration=3500,
                    position=InfoBarPosition.TOP_RIGHT,
                )
            self._stream.stop_stream()
            return
        self._updateCaptureReadyText()

    def _updateCaptureReadyText(self):
        current = len(self._capture_repetitions) + 1
        self.captureProgressLabel.setText(
            f'准备录制 {current}/{self._target_repetitions}')
        self._setStatus(
            '采集流程已就绪',
            f'点击「开始本次录制」完成第 {current} 次动作',
            active=True,
        )

    def _capture_completed(self, completed):
        self._recording = False
        self.recordBtn.blockSignals(True)
        self.recordBtn.setChecked(False)
        self.recordBtn.setText('开始本次录制')
        self.recordBtn.setEnabled(False)
        self.recordBtn.blockSignals(False)
        self.captureSessionBtn.setText('开始采集流程')
        if not completed:
            self.captureProgressLabel.setText('采集已取消')

    def _onTrain(self):
        if self._operation is not None:
            self._showWarning('实时流正在运行', '请先停止采集或识别')
            return
        if self._training_thread is not None:
            return
        if not list(_DATA_DIR.glob('*.json')):
            self._showWarning(
                '没有训练数据',
                '请先完成第 1 步，采集至少一个手势',
            )
            return

        self.trainBtn.setEnabled(False)
        self.trainingProgress.setVisible(True)
        self.trainingStatusLabel.setText('正在准备训练…')
        thread = _TrainingThread(self)
        thread.progress.connect(self.trainingStatusLabel.setText)
        thread.completed.connect(self._onTrainingCompleted)
        thread.error.connect(self._onTrainingError)
        thread.finished.connect(self._onTrainingFinished)
        self._training_thread = thread
        self._updateModelActions()
        thread.start()

    def _onTrainingCompleted(self, result: TrainingResult):
        self._refreshResourceSummary()
        trained = '、'.join(result.trained) or '无'
        failed = '、'.join(result.failed)
        skipped_text = (
            f'；已是最新跳过 {len(result.skipped)} 个'
            if result.skipped else '')
        self.trainingStatusLabel.setText(
            f'训练完成：{trained}'
            + skipped_text
            + (f'；失败：{failed}' if failed else ''))
        if result.trained:
            message = f'成功训练 {len(result.trained)} 个模型'
        elif result.skipped:
            message = '所有模型均已是最新，无需重复训练'
        else:
            message = '没有模型被训练'
        InfoBar.success(
            '模型训练完成',
            message,
            parent=self.window(), duration=3500,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _onTrainingError(self, message):
        self.trainingStatusLabel.setText(f'训练失败：{message}')
        self._showError('模型训练失败', message)

    def _onTrainingFinished(self):
        self.trainBtn.setEnabled(True)
        self.trainingProgress.setVisible(False)
        thread = self._training_thread
        self._training_thread = None
        if thread is not None:
            thread.deleteLater()
        self._updateModelActions()

    def _modelManagementBusy(self):
        return (
            self._operation is not None
            or self._training_thread is not None)

    def _selectedModelRecord(self):
        row = self.modelTable.currentRow()
        if row < 0:
            return None
        item = self.modelTable.item(row, 0)
        if item is None:
            return None
        model_value = item.data(Qt.UserRole)
        data_value = item.data(Qt.UserRole + 1)
        model_path = Path(str(model_value)) if model_value else None
        data_path = Path(str(data_value)) if data_value else None
        if model_path is not None and not model_path.exists():
            model_path = None
        if data_path is not None and not data_path.exists():
            data_path = None
        if model_path is None and data_path is None:
            return None
        return item.text(), model_path, data_path

    def _selectedModelPath(self):
        record = self._selectedModelRecord()
        return None if record is None else record[1]

    def _updateModelActions(self):
        busy = self._modelManagementBusy()
        record = self._selectedModelRecord()
        selected = record is not None
        has_model = selected and record[1] is not None
        self.importModelBtn.setEnabled(not busy)
        self.renameModelBtn.setEnabled(selected and not busy)
        self.exportModelBtn.setEnabled(has_model and not busy)
        self.deleteModelBtn.setEnabled(selected and not busy)
        self.refreshModelBtn.setEnabled(not busy)

    def _refreshModelTable(self):
        selected_name = None
        selected = self._selectedModelRecord()
        if selected is not None:
            selected_name = selected[0]

        model_paths = {
            path.stem: path for path in _MODEL_DIR.glob('*.pkl')}
        data_paths = {
            path.stem: path for path in _DATA_DIR.glob('*.json')}
        names = sorted(
            set(model_paths) | set(data_paths),
            key=str.casefold,
        )
        self.modelTable.blockSignals(True)
        self.modelTable.setRowCount(len(names))
        selected_row = 0 if names else -1
        for row, name in enumerate(names):
            model_path = model_paths.get(name)
            data_path = data_paths.get(name)
            latest_path = model_path or data_path
            stat = latest_path.stat()
            name_item = QTableWidgetItem(name)
            name_item.setData(
                Qt.UserRole, str(model_path) if model_path else '')
            name_item.setData(
                Qt.UserRole + 1, str(data_path) if data_path else '')
            if model_path and data_path:
                status = '模型 + 训练数据'
            elif model_path:
                status = '仅模型'
            else:
                status = '待训练数据'
            status_item = QTableWidgetItem(status)
            size_item = QTableWidgetItem(
                f'{model_path.stat().st_size / 1024:.1f} KB'
                if model_path else '—')
            modified_item = QTableWidgetItem(
                datetime.datetime.fromtimestamp(
                    stat.st_mtime).strftime('%Y-%m-%d %H:%M'))
            self.modelTable.setItem(row, 0, name_item)
            self.modelTable.setItem(row, 1, status_item)
            self.modelTable.setItem(row, 2, size_item)
            self.modelTable.setItem(row, 3, modified_item)
            if name == selected_name:
                selected_row = row
        if selected_row >= 0:
            self.modelTable.setCurrentCell(selected_row, 0)
        self.modelTable.blockSignals(False)
        self._updateModelActions()

    @staticmethod
    def _safeModelStem(value):
        name = str(value).strip()
        if name.lower().endswith('.pkl'):
            name = name[:-4].strip()
        name = ''.join(
            character for character in name
            if character not in '<>:"/\\|?*' and ord(character) >= 32)
        return name[:64]

    def _onImportModel(self):
        if self._modelManagementBusy():
            self._showWarning('模型正在使用', '请先停止识别或等待训练完成')
            return
        source_name, _ = QFileDialog.getOpenFileName(
            self,
            '导入 HMM 模型',
            '',
            'HMM 模型 (*.pkl)',
        )
        if not source_name:
            return

        warning = MessageBox(
            '确认导入模型',
            'Pickle 模型可能包含可执行代码。请只导入来源可信的文件。',
            self.window(),
        )
        warning.yesButton.setText('继续导入')
        warning.cancelButton.setText('取消')
        if not warning.exec():
            return

        source = Path(source_name)
        destination = _MODEL_DIR / source.name
        if destination.exists():
            overwrite = MessageBox(
                '覆盖同名模型？',
                f'模型「{destination.stem}」已经存在，是否覆盖？',
                self.window(),
            )
            overwrite.yesButton.setText('覆盖')
            overwrite.cancelButton.setText('取消')
            if not overwrite.exec():
                return
        try:
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
        except Exception as exc:
            self._showError('导入模型失败', str(exc))
            return
        self._refreshResourceSummary()
        InfoBar.success(
            '模型已导入',
            destination.name,
            parent=self.window(), duration=2500,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _onRenameModel(self):
        record = self._selectedModelRecord()
        if record is None or self._modelManagementBusy():
            return
        old_name, model_path, data_path = record
        value, accepted = QInputDialog.getText(
            self,
            '重命名模型',
            '新的模型名称：',
            text=old_name,
        )
        if not accepted:
            return
        stem = self._safeModelStem(value)
        if not stem:
            self._showWarning('名称无效', '请输入有效的模型名称')
            return
        if stem == old_name:
            return
        model_destination = _MODEL_DIR / f'{stem}.pkl'
        data_destination = _DATA_DIR / f'{stem}.json'
        if model_destination.exists() or data_destination.exists():
            self._showWarning(
                '模型已存在',
                f'已经存在名为「{stem}」的模型',
            )
            return
        completed = []
        try:
            if model_path is not None:
                model_path.rename(model_destination)
                completed.append((model_destination, model_path))
            if data_path is not None:
                data_path.rename(data_destination)
                completed.append((data_destination, data_path))
        except Exception as exc:
            for destination, original in reversed(completed):
                try:
                    destination.rename(original)
                except Exception:
                    pass
            self._showError('重命名失败', str(exc))
            return
        self._refreshResourceSummary()

    def _onExportModel(self):
        path = self._selectedModelPath()
        if path is None or self._modelManagementBusy():
            return
        destination_name, _ = QFileDialog.getSaveFileName(
            self,
            '导出 HMM 模型',
            path.name,
            'HMM 模型 (*.pkl)',
        )
        if not destination_name:
            return
        destination = Path(destination_name)
        if destination.suffix.lower() != '.pkl':
            destination = destination.with_suffix('.pkl')
        try:
            if path.resolve() != destination.resolve():
                shutil.copy2(path, destination)
        except Exception as exc:
            self._showError('导出模型失败', str(exc))
            return
        InfoBar.success(
            '模型已导出',
            str(destination),
            parent=self.window(), duration=3000,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _onDeleteModel(self):
        record = self._selectedModelRecord()
        if record is None or self._modelManagementBusy():
            return
        name, model_path, data_path = record
        parts = []
        if model_path is not None:
            parts.append('模型文件')
        if data_path is not None:
            parts.append('对应训练数据')
        confirm = MessageBox(
            '删除模型和数据？',
            f'将永久删除「{name}」的{"及".join(parts)}。'
            '删除训练数据后，“训练全部数据”不会再生成该模型。',
            self.window(),
        )
        confirm.yesButton.setText('删除')
        confirm.cancelButton.setText('取消')
        if not confirm.exec():
            return
        errors = []
        for path in (model_path, data_path):
            if path is None:
                continue
            try:
                path.unlink()
            except Exception as exc:
                errors.append(f'{path.name}: {exc}')
        if errors:
            self._showError('删除失败', '\n'.join(errors))
            self._refreshResourceSummary()
            return
        self._refreshResourceSummary()
        InfoBar.success(
            '模型和训练数据已删除',
            name,
            parent=self.window(), duration=2200,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _onRecognition(self):
        if self._operation == 'recognize':
            self._stream.stop_stream()
            return
        if self._operation is not None:
            self._showWarning('操作进行中', '请先取消当前采集流程')
            return
        if not list(_MODEL_DIR.glob('*.pkl')):
            self._showWarning('没有模型', '请先训练模型')
            return
        self._startStream('recognize')

    def _startStream(self, operation):
        if not self._connected:
            self._showWarning('戒指未连接', '请先连接戒指')
            return
        client, loop_thread = _get_shared_transport()
        if client is None or loop_thread is None:
            self._showWarning('连接不可用', '请重新连接戒指后再试')
            return
        self._operation = operation
        self.captureSessionBtn.setEnabled(False)
        self.recognitionBtn.setEnabled(False)
        self._updateModelActions()
        self._stream.set_transport(client, loop_thread)
        self._setStatus(
            '正在启动 IMU',
            '正在请求戒指实时六轴数据…',
            active=True,
        )
        self._stream.start()

    def _onStreamStarted(self, start_info):
        self._active = True
        self._sample_rate_hz = float(
            getattr(start_info, 'sample_rate_hz', 25.0) or 25.0)
        signalBus.modeStarted.emit(self.MODE_ID)
        self.captureSessionBtn.setEnabled(True)
        self.recognitionBtn.setEnabled(True)

        if self._operation == 'capture':
            self.captureSessionBtn.setText('取消采集')
            self.recordBtn.setEnabled(True)
            self.gestureNameEdit.setEnabled(False)
            self.repetitionSpin.setEnabled(False)
            self._updateCaptureReadyText()
            return

        if self._operation == 'recognize':
            self._recognizer = HMMRecognizer(
                _MODEL_DIR,
                sample_rate=self._sample_rate_hz,
                min_confidence=0.15,
            )
            if not self._recognizer.model_names:
                self._showError('没有可用模型', '模型目录为空或模型加载失败')
                self._stream.stop_stream()
                return
            self.recognitionBtn.setText('停止实时识别')
            self._recognition_count = 0
            self.latestResultLabel.setText('正在检测动作…')
            self.recognitionDiagnosticLabel.setText(
                '诊断：等待完整动作片段')
            self._setStatus(
                '实时识别运行中',
                f'已加载 {len(self._recognizer.model_names)} 个模型，'
                '请直接完成手势动作',
                active=True,
            )

    def _onBatch(self, batch):
        samples = [
            [
                sample.accel_x, sample.accel_y, sample.accel_z,
                sample.gyro_x, sample.gyro_y, sample.gyro_z,
            ]
            for sample in batch.samples
        ]
        if self._operation == 'capture' and self._recording:
            self._current_samples.extend(samples)
            current = len(self._capture_repetitions) + 1
            self.captureProgressLabel.setText(
                f'录制 {current}/{self._target_repetitions} · '
                f'{len(self._current_samples)} 帧')
            return

        if self._operation == 'recognize' and self._recognizer is not None:
            try:
                decisions = self._recognizer.feed_decisions(samples)
            except Exception as exc:
                self._showError('识别处理失败', str(exc))
                self._stream.stop_stream()
                return
            for decision in decisions:
                self._showRecognitionDecision(decision)
            if decisions:
                self._history_lines = self._history_lines[-50:]
                self.historyEdit.setPlainText('\n'.join(
                    reversed(self._history_lines)))

    @staticmethod
    def _recognitionReasonText(reason):
        return {
            'accepted': '达到确认条件',
            'ambiguous': '多个动作得分接近',
            'near_threshold': '相似但接近模型阈值',
            'low_absolute_fit': '与所有模型的绝对相似度不足',
            'duration_outlier': '动作时长明显超出训练范围',
            'segment_too_short': '动作片段太短，无法提取特征',
        }.get(reason, reason)

    def _formatRecognitionDiagnostic(self, decision):
        reason = self._recognitionReasonText(decision.reason)
        parts = [f'{decision.segment_frames} 帧', reason]
        for index, candidate in enumerate(decision.candidates, start=1):
            bounds = candidate.length_bounds
            length_text = (
                f'，训练长度 {bounds[0]}–{bounds[1]} 帧'
                if bounds is not None else '')
            parts.append(
                f'Top {index}：{candidate.name} '
                f'{candidate.confidence:.1%}；'
                f'得分 {candidate.adjusted_score:.1f} / '
                f'阈值 {candidate.threshold:.1f}'
                f'{length_text}')
        return ' ｜ '.join(parts)

    def _showRecognitionDecision(self, decision):
        time_text = datetime.datetime.now().strftime('%H:%M:%S')
        candidate = decision.best_candidate
        self.recognitionDiagnosticLabel.setText(
            self._formatRecognitionDiagnostic(decision))

        if decision.status == 'confirmed' and candidate is not None:
            self._recognition_count += 1
            self.latestResultLabel.setText(
                f'已确认：{candidate.name} · {candidate.confidence:.1%}')
            self._history_lines.append(
                f'{time_text}  {candidate.name}  '
                f'已确认 {candidate.confidence:.1%}')
            return

        if decision.status == 'tentative' and candidate is not None:
            self.latestResultLabel.setText(
                f'候选：{candidate.name} · {candidate.confidence:.1%}')
            self._history_lines.append(
                f'{time_text}  候选：{candidate.name}  '
                f'{candidate.confidence:.1%}（'
                f'{self._recognitionReasonText(decision.reason)}）')
            return

        reason = self._recognitionReasonText(decision.reason)
        if candidate is None:
            self.latestResultLabel.setText(f'未识别：{reason}')
            self._history_lines.append(
                f'{time_text}  未识别（{reason}）')
        else:
            self.latestResultLabel.setText(
                f'未识别：{reason}；最像 {candidate.name}')
            self._history_lines.append(
                f'{time_text}  未识别（{reason}；'
                f'最像 {candidate.name} {candidate.confidence:.1%}）')

    def _onStreamError(self, message):
        self._showError('HMM 手势流程出错', message)

    def _onStreamStopped(self):
        was_active = self._active
        operation = self._operation
        self._active = False
        self._operation = None
        self._recognizer = None
        if was_active:
            signalBus.modeStopped.emit(self.MODE_ID)

        self.captureSessionBtn.setEnabled(True)
        self.captureSessionBtn.setText('开始采集流程')
        self.recognitionBtn.setEnabled(True)
        self.recognitionBtn.setText('开始实时识别')
        self.gestureNameEdit.setEnabled(True)
        self.repetitionSpin.setEnabled(True)
        self._capture_completed(
            operation == 'capture'
            and len(self._capture_repetitions) >= self._target_repetitions)
        self._updateModelActions()
        self._setStatus(
            '戒指已连接' if self._connected else '等待连接戒指',
            '可开始采集或实时识别'
            if self._connected else '请先连接戒指',
            active=False,
        )

    def _onOtherModeStarted(self, mode):
        if mode == self.MODE_ID or not self._active:
            return
        self._stream.stop_stream(send_stop=mode not in _STREAM_MODES)
        InfoBar.info(
            'HMM 手势流程已停止',
            '已切换到其他传感器功能',
            parent=self.window(), duration=2200,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _refreshResourceSummary(self):
        data_count = len(list(_DATA_DIR.glob('*.json')))
        model_count = len(list(_MODEL_DIR.glob('*.pkl')))
        self.resourceLabel.setText(
            f'训练数据 {data_count} · 模型 {model_count}')
        self._refreshModelTable()

    def _setStatus(self, title, hint, active):
        self.statusLabel.setText(title)
        self.statusHint.setText(hint)
        self.statusLabel.setProperty('active', bool(active))
        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)

    def _showWarning(self, title, message):
        InfoBar.warning(
            title, message,
            parent=self.window(), duration=3000,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _showError(self, title, message):
        InfoBar.error(
            title, message,
            parent=self.window(), duration=4000,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def closeEvent(self, event):
        if self._stream.isRunning():
            self._stream.stop_stream()
            self._stream.wait(3000)
        if self._training_thread is not None:
            self._training_thread.wait(5000)
        event.accept()
