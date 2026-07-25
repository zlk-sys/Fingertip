# coding: utf-8
"""Trajectory page: SDK transport and presentation only.

All IMU math and calibration live in :mod:`app.trajectory.engine`.  Keeping
the Qt layer free of sensor algorithms makes recorded data replayable and the
trajectory behavior testable without a GUI.
"""

import datetime
from pathlib import Path

import pyqtgraph as pg
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QFileDialog, QHBoxLayout, QVBoxLayout,
                             QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, IconWidget,
                            InfoBar, InfoBarPosition, PrimaryPushButton,
                            PushButton, ScrollArea, SimpleCardWidget, Slider,
                            StrongBodyLabel, SubtitleLabel, TitleLabel,
                            TogglePushButton)
from qfluentwidgets import FluentIcon as FIF

from ..common.signal_bus import signalBus
from ..common.style_sheet import StyleSheet
from ..sdk.ring_sound import (SensorCommand, start_sensor_report,
                               stop_sensor_report, wait_sensor_data)
from ..trajectory import TrackingPhase, TrajectoryEngine


_STREAM_MODES = ('sensor', 'level', 'drawing', 'hmm_gesture')
_CALIBRATION_PHASES = {
    TrackingPhase.CALIBRATING_RIGHT,
    TrackingPhase.CALIBRATING_STILL,
    TrackingPhase.CALIBRATING_UP,
}


def _get_shared_client():
    from . import connect_interface
    return connect_interface.shared_client


class _SensorStreamThread(QThread):
    """Bridge the shared asyncio BLE loop into Qt signals."""

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
            self.error.emit(f'启动传感器上报失败：{exc}')
            self.stopped.emit()
            return

        while self._running:
            try:
                batch = self._loop_thread.run_coro(
                    wait_sensor_data(self._client, timeout_s=2.0),
                    timeout=3.0,
                )
                self.batchReceived.emit(batch)
            except TimeoutError:
                continue
            except Exception as exc:
                if self._running:
                    self.error.emit(f'读取传感器数据失败：{exc}')
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
        self._send_stop = send_stop
        self._running = False


class DrawingInterface(ScrollArea):
    """Thin Qt adapter around :class:`TrajectoryEngine`."""

    ringPressed = pyqtSignal()

    INITIAL_VIEW_RANGE = 100.0
    MIN_RENDER_DISTANCE = 0.08
    MAX_STROKES = 200

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self._active = False
        self._connected = False
        self._mode = 'always'
        self._penDown = False
        self._handlersRegistered = False
        self._restorePenAfterCalibration = False
        self._lastPhase = None
        self._lastStationary = None
        self._viewExtent = self.INITIAL_VIEW_RANGE

        self._engine = TrajectoryEngine()
        self._stream = _SensorStreamThread(self)
        self._strokes = []
        self._currentStroke = None
        self._lastRenderedPoint = None

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        self._buildHeader()
        self._buildStatusCard()
        self._buildControlsCard()
        self._buildCanvas()
        self._initWidget()
        self._connectSignals()
        self._updatePenUi()
        self._updateEngineUi()

    # ── UI construction ─────────────────────────────────────

    def _buildHeader(self):
        self.titleLabel = TitleLabel('轨迹绘制（Beta）', self.view)
        self.subtitleLabel = CaptionLabel(
            '六轴姿态跟踪会将不同佩戴角度统一到同一个绘画平面；'
            '改变佩戴方式后执行一次佩戴校准',
            self.view,
        )
        self.subtitleLabel.setTextColor(
            QColor(96, 96, 96), QColor(180, 180, 180))

    def _buildStatusCard(self):
        self.statusCard = SimpleCardWidget(self.view)
        self.statusCard.setBorderRadius(12)
        self.statusCard.setFixedHeight(100)

        # self.statusIcon = IconWidget(FIF.PENCIL_INK, self.statusCard)
        # self.statusIcon.setFixedSize(40, 40)
        self.statusLabel = StrongBodyLabel('轨迹绘制未开启', self.statusCard)
        self.statusLabel.setObjectName('drawingStatusLabel')
        self.statusLabel.setProperty('active', False)
        self.statusHint = CaptionLabel(
            '请先连接戒指并切换到手势模式', self.statusCard)
        self.statusHint.setTextColor(
            QColor(96, 96, 96), QColor(180, 180, 180))

        self.toggleBtn = TogglePushButton('开始绘制', self.statusCard)
        self.toggleBtn.setFixedWidth(120)
        self.toggleBtn.setEnabled(False)
        self.wearCalBtn = PrimaryPushButton('佩戴校准', self.statusCard)
        self.wearCalBtn.setFixedWidth(110)
        self.wearCalBtn.setEnabled(False)
        self.wearCalBtn.setToolTip('按提示依次向右、向上挥动，适配当前佩戴角度')
        self.recenterBtn = PushButton('光标归中', self.statusCard)
        self.recenterBtn.setFixedWidth(100)
        self.recenterBtn.setEnabled(False)

        layout = QHBoxLayout(self.statusCard)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)
        # layout.addWidget(self.statusIcon, 0, Qt.AlignVCenter)
        textLayout = QVBoxLayout()
        textLayout.setSpacing(4)
        textLayout.addWidget(self.statusLabel)
        textLayout.addWidget(self.statusHint)
        layout.addLayout(textLayout, 1)
        layout.addWidget(self.recenterBtn, 0, Qt.AlignVCenter)
        layout.addWidget(self.wearCalBtn, 0, Qt.AlignVCenter)
        layout.addWidget(self.toggleBtn, 0, Qt.AlignVCenter)

    def _buildControlsCard(self):
        self.controlsCard = SimpleCardWidget(self.view)
        self.controlsCard.setBorderRadius(12)
        self.controlsCard.setFixedHeight(72)

        self.modeCombo = ComboBox(self.controlsCard)
        self.modeCombo.addItems(['常落笔模式', '手动模式'])
        self.modeCombo.setCurrentIndex(0)
        self.modeCombo.setFixedWidth(130)

        self.penBtn = TogglePushButton('落笔', self.controlsCard)
        self.penBtn.setFixedWidth(90)
        self.penBtn.setEnabled(False)
        self.penStateLabel = BodyLabel('已抬笔', self.controlsCard)
        self.penStateLabel.setObjectName('penStateLabel')
        self.penStateLabel.setProperty('penDown', False)

        self.sensitivitySlider = Slider(Qt.Horizontal, self.controlsCard)
        self.sensitivitySlider.setRange(1, 10)
        self.sensitivitySlider.setValue(5)
        self.sensitivitySlider.setFixedWidth(140)
        self.sensitivityValue = BodyLabel('5', self.controlsCard)
        self.sensitivityValue.setFixedWidth(20)
        self.sensitivityValue.setAlignment(Qt.AlignCenter)

        self.exportBtn = PushButton('导出 PNG', self.controlsCard)
        self.exportBtn.setFixedWidth(100)
        self.clearBtn = PushButton('清空画布', self.controlsCard)
        self.clearBtn.setFixedWidth(105)

        layout = QHBoxLayout(self.controlsCard)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(BodyLabel('落笔模式', self.controlsCard))
        layout.addWidget(self.modeCombo)
        layout.addSpacing(8)
        layout.addWidget(self.penBtn)
        layout.addWidget(self.penStateLabel)
        layout.addStretch(1)
        layout.addWidget(BodyLabel('灵敏度', self.controlsCard))
        layout.addWidget(self.sensitivitySlider)
        layout.addWidget(self.sensitivityValue)
        layout.addSpacing(8)
        layout.addWidget(self.exportBtn)
        layout.addWidget(self.clearBtn)

    def _buildCanvas(self):
        self.canvasSection = SubtitleLabel('2D 轨迹', self.view)
        self.canvasCard = SimpleCardWidget(self.view)
        self.canvasCard.setBorderRadius(12)
        self.canvasCard.setMinimumHeight(500)

        self.plotWidget = pg.PlotWidget(self.canvasCard)
        self.plotWidget.setMenuEnabled(False)
        self.plotWidget.setMouseEnabled(x=False, y=False)
        self.plotWidget.hideButtons()
        self.plotWidget.setAspectLocked(True)
        self.plotWidget.showGrid(x=True, y=True, alpha=0.18)
        self._resetViewRange()

        self._cursor = pg.ScatterPlotItem(
            size=12,
            brush=pg.mkBrush(0, 120, 212),
            pen=pg.mkPen(255, 255, 255, width=2),
        )
        self._cursor.setData([0.0], [0.0])
        self.plotWidget.addItem(self._cursor)

        self.motionLabel = CaptionLabel('—', self.canvasCard)
        self.motionLabel.setAlignment(Qt.AlignCenter)
        self.motionLabel.setStyleSheet(
            'background: transparent; color: rgba(128,128,128,180);'
            'font-size: 12px; padding: 2px 8px;')

        layout = QVBoxLayout(self.canvasCard)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self.plotWidget)
        layout.addWidget(self.motionLabel)

        self.canvasHint = CaptionLabel(
            '提示：佩戴校准期间会自动抬笔并清空旧坐标系中的轨迹；'
            '六轴 IMU 绘制的是稳定角度轨迹，不是绝对空间位置',
            self.view,
        )
        self.canvasHint.setTextColor(
            QColor(96, 96, 96), QColor(180, 180, 180))

    def _initWidget(self):
        self.setObjectName('drawingInterface')
        self.view.setObjectName('view')
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()
        StyleSheet.DRAWING_INTERFACE.apply(self)

        self.vBoxLayout.setContentsMargins(36, 24, 36, 36)
        self.vBoxLayout.setSpacing(18)
        self.vBoxLayout.setAlignment(Qt.AlignTop)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.statusCard)
        self.vBoxLayout.addWidget(self.canvasSection)
        self.vBoxLayout.addWidget(self.controlsCard)
        self.vBoxLayout.addWidget(self.canvasCard)
        self.vBoxLayout.addWidget(self.canvasHint)

    def _connectSignals(self):
        self.toggleBtn.toggled.connect(self._onToggleDrawing)
        self.wearCalBtn.clicked.connect(self._onWearingCalibration)
        self.recenterBtn.clicked.connect(self._onRecenter)
        self.modeCombo.currentIndexChanged.connect(self._onModeChanged)
        self.penBtn.toggled.connect(self._onPenButton)
        self.sensitivitySlider.valueChanged.connect(self._onSensitivity)
        self.clearBtn.clicked.connect(self._clearCanvas)
        self.exportBtn.clicked.connect(self._exportPng)
        self.ringPressed.connect(self._onRingPressed)

        signalBus.deviceConnected.connect(self._onDeviceConnected)
        signalBus.deviceDisconnected.connect(self._onDeviceDisconnected)
        signalBus.modeStarted.connect(self._onOtherModeStarted)

        self._stream.startedSuccessfully.connect(self._onStreamStarted)
        self._stream.batchReceived.connect(self._onBatch)
        self._stream.error.connect(self._onStreamError)
        self._stream.stopped.connect(self._onStreamStopped)

    # ── Connection and stream lifecycle ────────────────────

    def _onDeviceConnected(self, name, address):
        self._connected = True
        self.statusHint.setText(f'已连接：{name} ({address})')
        self.toggleBtn.setEnabled(True)

    def _onDeviceDisconnected(self):
        self._connected = False
        self.toggleBtn.setEnabled(False)
        self.wearCalBtn.setEnabled(False)
        self.recenterBtn.setEnabled(False)
        self.statusHint.setText('请先连接戒指并切换到手势模式')
        if self._active:
            self._stopDrawing()

    def _onToggleDrawing(self, checked):
        if checked:
            self._startDrawing()
        else:
            self._stopDrawing()

    def _startDrawing(self):
        client = _get_shared_client()
        if client is None:
            self._showWarning('未连接设备', '请先连接戒指再开始绘制')
            self.toggleBtn.setChecked(False)
            return

        from . import connect_interface
        loop_thread = connect_interface.async_loop_thread
        if loop_thread is None:
            self._showWarning('事件循环未就绪', '请重新连接戒指后再试')
            self.toggleBtn.setChecked(False)
            return

        self._engine.reset()
        self._engine.set_sensitivity(self.sensitivitySlider.value())
        self._lastPhase = None
        self._lastStationary = None
        self._clearCanvas(restart=False)
        self._cursor.setData([0.0], [0.0])
        self._stream.set_transport(client, loop_thread)
        self._stream.start()
        self.statusLabel.setText('正在启动传感器…')

    def _stopDrawing(self):
        self._stream.stop_stream()

    def _onStreamStarted(self, start_info):
        self._engine.configure(
            getattr(start_info, 'accel_range_g', 2.0),
            getattr(start_info, 'gyro_range_dps', 2000.0),
            getattr(start_info, 'sample_rate_hz', 50.0),
        )
        self._active = True
        signalBus.modeStarted.emit('drawing')
        self.toggleBtn.setText('停止绘制')
        self.wearCalBtn.setEnabled(True)
        self.recenterBtn.setEnabled(True)
        self.statusLabel.setProperty('active', True)
        self._refreshStatusStyle()

        if self._mode == 'always':
            self._setPenDown(True)
        else:
            self.penBtn.setEnabled(True)
            self._setPenDown(False)
            self._registerRingHandler()
        self._updateEngineUi(force=True)

    def _onStreamStopped(self):
        was_active = self._active
        self._active = False
        if was_active:
            signalBus.modeStopped.emit('drawing')
        self._unregisterRingHandler()
        self._setPenDown(False)
        self.penBtn.setEnabled(False)
        self.wearCalBtn.setEnabled(False)
        self.recenterBtn.setEnabled(False)
        self.wearCalBtn.setText('佩戴校准')
        self.statusLabel.setText('轨迹绘制已停止')
        self.statusHint.setText(
            '已停止接收 IMU 数据' if self._connected
            else '请先连接戒指并切换到手势模式')
        self.statusLabel.setProperty('active', False)
        self._refreshStatusStyle()
        self.motionLabel.setText('—')
        self.toggleBtn.blockSignals(True)
        self.toggleBtn.setChecked(False)
        self.toggleBtn.setText('开始绘制')
        self.toggleBtn.blockSignals(False)

    def _onStreamError(self, message):
        InfoBar.error(
            '绘制出错', message,
            parent=self.window(), duration=3500,
            position=InfoBarPosition.TOP_RIGHT,
        )
        self._onStreamStopped()

    def _onOtherModeStarted(self, mode):
        if mode == 'drawing' or not self._active:
            return
        self._stream.stop_stream(send_stop=mode not in _STREAM_MODES)
        InfoBar.info(
            '轨迹绘制已停止', '已切换到其他传感器功能',
            parent=self.window(), duration=2000,
            position=InfoBarPosition.TOP_RIGHT,
        )

    # ── Engine integration ──────────────────────────────────

    def _onBatch(self, batch):
        curve_dirty = False
        for sample in batch.samples:
            frame = self._engine.process_raw(
                sample.timestamp_ms,
                sample.accel_x, sample.accel_y, sample.accel_z,
                sample.gyro_x, sample.gyro_y, sample.gyro_z,
            )
            self._handleEngineEvent(frame.event)
            self._updateEngineUi(frame)

            if not frame.moved:
                continue
            self._cursor.setData([frame.x], [frame.y])
            self._expandView(frame.x, frame.y)
            if (self._penDown
                    and frame.phase not in _CALIBRATION_PHASES
                    and self._appendCurrentPoint(frame.x, frame.y)):
                curve_dirty = True

        if curve_dirty and self._currentStroke is not None:
            xs, ys, curve = self._currentStroke
            curve.setData(xs, ys)

    def _handleEngineEvent(self, event):
        if event is None:
            return
        if event == 'calibration_complete':
            self.wearCalBtn.setText('佩戴校准')
            self._clearCanvas(restart=False)
            self._cursor.setData([0.0], [0.0])
            self._restorePenAfterCalibrationFlow()
            InfoBar.success(
                '佩戴校准完成',
                '当前佩戴角度已建立独立绘画平面，画布已归中',
                parent=self.window(), duration=3000,
                position=InfoBarPosition.TOP_RIGHT,
            )
        elif event in {'calibration_timeout', 'calibration_axes_too_close'}:
            self.wearCalBtn.setText('佩戴校准')
            self._restorePenAfterCalibrationFlow()
            message = (
                '两次动作方向过于接近，请明确地先向右、再向上挥动'
                if event == 'calibration_axes_too_close'
                else '规定时间内没有检测到完整动作，请重试')
            self._showWarning('佩戴校准失败', message)

    def _updateEngineUi(self, frame=None, force=False):
        phase = self._engine.phase if frame is None else frame.phase
        stationary = (
            self._engine.stationary if frame is None else frame.stationary)
        if not force and phase == self._lastPhase and stationary == self._lastStationary:
            return
        self._lastPhase = phase
        self._lastStationary = stationary

        if not self._active:
            return
        phase_text = {
            TrackingPhase.STABILIZING: (
                '正在建立静止基线', '请保持戒指静止约半秒'),
            TrackingPhase.READY: (
                '轨迹引擎已就绪', '改变佩戴角度后请执行「佩戴校准」'),
            TrackingPhase.CALIBRATING_RIGHT: (
                '佩戴校准 1/2', '保持手的位置，向右转动手腕一次，然后停住'),
            TrackingPhase.CALIBRATING_STILL: (
                '佩戴校准', '很好，请保持静止'),
            TrackingPhase.CALIBRATING_UP: (
                '佩戴校准 2/2', '保持手的位置，向上转动手腕一次，然后停住'),
            TrackingPhase.TRACKING: (
                '轨迹绘制中', '佩戴方向已校准'),
        }
        self.statusLabel.setText(phase_text[phase][0])
        self.statusHint.setText(phase_text[phase][1])

        if phase == TrackingPhase.STABILIZING:
            self.motionLabel.setText('● 正在稳定零偏')
            color = 'rgba(220,150,40,210)'
        elif phase in _CALIBRATION_PHASES:
            self.motionLabel.setText('● 校准中')
            color = 'rgba(140,90,220,210)'
        elif stationary:
            self.motionLabel.setText('● 静止')
            color = 'rgba(80,200,120,210)'
        else:
            self.motionLabel.setText('● 运动')
            color = 'rgba(0,120,212,210)'
        self.motionLabel.setStyleSheet(
            'background: transparent;'
            f'color: {color}; font-size: 12px; padding: 2px 8px;')

    # ── Wearing calibration ─────────────────────────────────

    def _onWearingCalibration(self):
        if not self._active:
            return
        if self._engine.phase in _CALIBRATION_PHASES:
            self._engine.cancel_wearing_calibration()
            self.wearCalBtn.setText('佩戴校准')
            self._restorePenAfterCalibrationFlow()
            self._updateEngineUi(force=True)
            return
        if not self._engine.bias_ready:
            self._showWarning('请稍候', '请先保持戒指静止，等待基线建立完成')
            return

        self._restorePenAfterCalibration = (
            self._penDown or self._mode == 'always')
        self._setPenDown(False)
        if self._engine.begin_wearing_calibration():
            self.wearCalBtn.setText('取消校准')
            self._updateEngineUi(force=True)

    def _restorePenAfterCalibrationFlow(self):
        should_restore = (
            self._mode == 'always' or self._restorePenAfterCalibration)
        self._restorePenAfterCalibration = False
        if self._active and should_restore:
            self._setPenDown(True)

    # ── Pen and canvas ──────────────────────────────────────

    def _onModeChanged(self, index):
        self._mode = 'manual' if index == 1 else 'always'
        if not self._active:
            self._updatePenUi()
            return
        if self._mode == 'always':
            self._unregisterRingHandler()
            self.penBtn.setEnabled(False)
            if self._engine.phase not in _CALIBRATION_PHASES:
                self._setPenDown(True)
        else:
            self._registerRingHandler()
            self.penBtn.setEnabled(True)
            self._setPenDown(False)

    def _onPenButton(self, checked):
        if (self._mode == 'manual' and self._active
                and self._engine.phase not in _CALIBRATION_PHASES):
            self._setPenDown(checked)

    def _onRingPressed(self):
        if (self._mode == 'manual' and self._active
                and self._engine.phase not in _CALIBRATION_PHASES):
            self.penBtn.setChecked(not self._penDown)

    def _setPenDown(self, down):
        down = bool(down)
        if down == self._penDown:
            self._updatePenUi()
            return
        self._penDown = down
        if down:
            self._beginStroke()
        else:
            self._currentStroke = None
            self._lastRenderedPoint = None
        self._updatePenUi()

    def _updatePenUi(self):
        self.penStateLabel.setText('已落笔' if self._penDown else '已抬笔')
        self.penStateLabel.setProperty('penDown', self._penDown)
        self.penStateLabel.style().unpolish(self.penStateLabel)
        self.penStateLabel.style().polish(self.penStateLabel)
        self.penBtn.blockSignals(True)
        self.penBtn.setChecked(self._penDown)
        self.penBtn.setText('抬笔' if self._penDown else '落笔')
        self.penBtn.blockSignals(False)

    def _beginStroke(self):
        position = self._engine.position
        curve = pg.PlotCurveItem(
            pen=pg.mkPen(color=(0, 120, 212), width=3),
            connect='finite',
        )
        self.plotWidget.addItem(curve)
        stroke = ([float(position[0])], [float(position[1])], curve)
        self._strokes.append(stroke)
        while len(self._strokes) > self.MAX_STROKES:
            _, _, old_curve = self._strokes.pop(0)
            self.plotWidget.removeItem(old_curve)
        self._currentStroke = stroke
        self._lastRenderedPoint = position.copy()

    def _appendCurrentPoint(self, x, y):
        if self._currentStroke is None:
            return False
        if self._lastRenderedPoint is not None:
            dx = x - float(self._lastRenderedPoint[0])
            dy = y - float(self._lastRenderedPoint[1])
            if dx * dx + dy * dy < self.MIN_RENDER_DISTANCE ** 2:
                return False
        xs, ys, _ = self._currentStroke
        xs.append(float(x))
        ys.append(float(y))
        self._lastRenderedPoint = (float(x), float(y))
        return True

    def _clearCanvas(self, checked=False, restart=True):
        del checked
        for _, _, curve in self._strokes:
            self.plotWidget.removeItem(curve)
        self._strokes.clear()
        self._currentStroke = None
        self._lastRenderedPoint = None
        self._resetViewRange()
        if restart and self._penDown:
            self._beginStroke()

    def _onRecenter(self):
        if not self._active:
            return
        self._engine.recenter()
        self._cursor.setData([0.0], [0.0])
        if self._penDown:
            self._beginStroke()

    def _onSensitivity(self, value):
        self.sensitivityValue.setText(str(value))
        self._engine.set_sensitivity(value)

    def _resetViewRange(self):
        self._viewExtent = self.INITIAL_VIEW_RANGE
        if hasattr(self, 'plotWidget'):
            self.plotWidget.setXRange(
                -self._viewExtent, self._viewExtent, padding=0)
            self.plotWidget.setYRange(
                -self._viewExtent, self._viewExtent, padding=0)

    def _expandView(self, x, y):
        needed = max(abs(x), abs(y)) * 1.15
        if needed <= self._viewExtent:
            return
        self._viewExtent = max(needed, self._viewExtent * 1.25)
        self.plotWidget.setXRange(
            -self._viewExtent, self._viewExtent, padding=0)
        self.plotWidget.setYRange(
            -self._viewExtent, self._viewExtent, padding=0)

    def _exportPng(self):
        if not self._strokes:
            self._showWarning('没有轨迹', '请先绘制轨迹再导出')
            return
        default_path = (
            Path.home() / 'Desktop'
            / f'fingertip_{datetime.datetime.now():%Y%m%d_%H%M%S}.png')
        path, _ = QFileDialog.getSaveFileName(
            self, '导出轨迹', str(default_path), 'PNG 图片 (*.png)')
        if not path:
            return
        try:
            from pyqtgraph.exporters import ImageExporter
            exporter = ImageExporter(self.plotWidget.plotItem)
            exporter.parameters()['width'] = 1600
            exporter.export(path)
            InfoBar.success(
                '导出成功', f'已保存到：{path}',
                parent=self.window(), duration=3000,
                position=InfoBarPosition.TOP_RIGHT,
            )
        except Exception as exc:
            InfoBar.error(
                '导出失败', str(exc),
                parent=self.window(), duration=3000,
                position=InfoBarPosition.TOP_RIGHT,
            )

    # ── Ring button handler and helpers ─────────────────────

    def _registerRingHandler(self):
        client = _get_shared_client()
        if client is None or self._handlersRegistered:
            return
        client.add_packet_handler(
            SensorCommand.KEY_SINGLE_PRESS, self._onSinglePressPacket)
        self._handlersRegistered = True

    def _unregisterRingHandler(self):
        if not self._handlersRegistered:
            return
        client = _get_shared_client()
        if client is not None:
            try:
                client.remove_packet_handler(
                    SensorCommand.KEY_SINGLE_PRESS,
                    self._onSinglePressPacket,
                )
            except (ValueError, KeyError):
                pass
        self._handlersRegistered = False

    async def _onSinglePressPacket(self, packet):
        del packet
        self.ringPressed.emit()

    def _showWarning(self, title, message):
        InfoBar.warning(
            title, message,
            parent=self.window(), duration=2600,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _refreshStatusStyle(self):
        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)

    def closeEvent(self, event):
        if self._active:
            self._stopDrawing()
            self._stream.wait(3000)
        event.accept()
