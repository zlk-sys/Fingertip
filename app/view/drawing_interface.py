# coding: utf-8
"""2D real-time trajectory drawing interface.

Maps the ring's gyroscope motion (air-mouse style) to a 2D canvas and draws
the trace in real time. Two pen modes are supported:
  - 常落笔模式: the pen is always down, drawing starts immediately
  - 手动模式:   press the button (UI or ring single-click) to toggle pen up/down

Calibration: capture the gravity direction at the moment of calibration.
The canvas is the VERTICAL plane (perpendicular to the calibrated surface,
like a whiteboard in front of the user): rotation about the true vertical
axis (yaw) moves the pen horizontally, rotation about the in-canvas
horizontal axis (pitch) moves it vertically.
"""
import time
from collections import deque

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt, pyqtSignal, QThread
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (ScrollArea, FluentIcon, TitleLabel, BodyLabel,
                            StrongBodyLabel, CaptionLabel, SubtitleLabel,
                            SimpleCardWidget, TogglePushButton, IconWidget,
                            PushButton, PrimaryPushButton, ComboBox, Slider,
                            InfoBar, InfoBarPosition)
from qfluentwidgets import FluentIcon as FIF

from ..common.style_sheet import StyleSheet
from ..common.signal_bus import signalBus
from ..sdk.ring_sound import (SensorCommand, start_sensor_report,
                               stop_sensor_report, wait_sensor_data)


def _get_shared_client():
    """Return the current shared BLE client, or None if not connected."""
    from . import connect_interface
    return connect_interface.shared_client


# Modes that consume the sensor data stream (start/stop_sensor_report)
_STREAM_MODES = ('sensor', 'level', 'drawing')


class _CollectorThread(QThread):
    """Background thread that schedules BLE sensor reading on AsyncLoopThread."""

    batchReceived = pyqtSignal(object)
    error = pyqtSignal(str)
    startedSuccessfully = pyqtSignal(object)
    stopped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._client = None
        self._loop_thread = None
        self._send_stop = True

    def set_client(self, client, loop_thread):
        self._client = client
        self._loop_thread = loop_thread

    def run(self):
        if self._client is None or self._loop_thread is None:
            self.error.emit('BLE client not available')
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
            self.error.emit(f'启动传感器上报失败: {exc}')
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
                    self.error.emit(f'读取传感器数据失败: {exc}')
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

    def stop_collecting(self, send_stop=True):
        """Stop collecting. send_stop=False skips stop_sensor_report,
        used when another stream mode is taking over the data stream."""
        self._send_stop = send_stop
        self._running = False


class DrawingInterface(ScrollArea):
    """2D real-time trajectory drawing interface."""

    # Emitted from the BLE thread when the ring button is single-clicked
    ringPressed = pyqtSignal()

    CANVAS_RANGE = 100.0        # canvas extends from -RANGE to +RANGE
    GYRO_DEADZONE_DPS = 1.0     # ignore tiny angular velocities after bias removal
    STILL_THRESHOLD_DPS = 3.0   # |w - bias| below this counts as "still"
    LPF_ALPHA = 0.3             # EMA low-pass filter coefficient (0..1)

    # Direction (wear-orientation) calibration parameters
    DIRCAL_MOTION_DPS = 15.0    # samples above this count as intentional motion
    DIRCAL_MIN_SAMPLES = 5      # minimum motion samples needed to settle
    DIRCAL_MAX_SAMPLES = 80     # settle immediately once this many collected
    DIRCAL_END_DPS = 8.0        # motion is considered over below this
    DIRCAL_END_SAMPLES = 5      # consecutive low samples ending the swing
    DIRCAL_STILL_DPS = 5.0      # "hold still" threshold between the two steps
    DIRCAL_STILL_SAMPLES = 10   # consecutive still samples required
    DIRCAL_TIMEOUT_S = 8.0      # per-step timeout

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        # State
        self._active = False
        self._connected = False
        self._penDown = False
        self._mode = 'always'          # 'always' or 'manual'
        self._collector = _CollectorThread(self)
        self._handlers_registered = False

        # Position integration state
        self._posX = 0.0
        self._posY = 0.0
        self._lastTsMs = None
        self._gyroRangeDps = None

        # Canvas calibration state. The canvas is a VERTICAL plane:
        #   _axisVert  = true vertical axis (gravity dir in sensor frame),
        #                yaw about it -> horizontal pen movement
        #   _axisHoriz = in-canvas horizontal axis,
        #                pitch about it -> vertical pen movement
        # Default basis matches the uncalibrated air-mouse behaviour:
        # dx from gyro_z (yaw), dy from gyro_y (pitch)
        self._latestAccel = None                                      # raw accel vector
        self._axisVert = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        self._axisHoriz = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        self._calibrated = False

        # Filtering / gyro bias (still-position) calibration state
        self._gyroBias = np.zeros(3, dtype=np.float64)   # gyro zero offset (dps)
        self._wFilt = np.zeros(3, dtype=np.float64)      # low-pass filtered w (dps)
        self._stillCount = 0                             # consecutive still samples
        self._recentGyro = deque(maxlen=25)              # recent w samples (dps)

        # Direction (wear-orientation) calibration state machine:
        # None / 'right' / 'wait_still' / 'up'
        self._dirCalStage = None
        self._dirCalSamples = []
        self._dirCalRightAxis = None
        self._dirCalStillCount = 0
        self._dirCalEndCount = 0
        self._dirCalStartTime = 0.0

        # Strokes: each stroke is ([xs], [ys], PlotCurveItem)
        self._strokes = []
        self._currentStroke = None

        # ── Title ────────────────────────────────────────────────
        self.titleLabel = TitleLabel('轨迹绘制', self.view)
        self.subtitleLabel = CaptionLabel(
            '将戒指的实时运动映射到 2D 画布上绘制轨迹；开始前请确保戒指已切换至手势模式',
            self.view
        )
        self.subtitleLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        # ── Status card ──────────────────────────────────────────
        self.statusCard = SimpleCardWidget(self.view)
        self.statusCard.setBorderRadius(12)
        self.statusCard.setFixedHeight(96)

        self.statusIcon = IconWidget(FIF.PENCIL_INK, self.statusCard)
        self.statusIcon.setFixedSize(40, 40)

        self.statusLabel = StrongBodyLabel('轨迹绘制未开启', self.statusCard)
        self.statusLabel.setObjectName('drawingStatusLabel')
        self.statusLabel.setProperty('active', False)

        self.connectionHint = CaptionLabel('请先在「连接戒指」页面连接设备', self.statusCard)
        self.connectionHint.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.toggleBtn = TogglePushButton('开始绘制', self.statusCard)
        self.toggleBtn.setFixedWidth(140)
        self.toggleBtn.setEnabled(False)

        self.calibrateBtn = PrimaryPushButton('校准画布', self.statusCard)
        self.calibrateBtn.setFixedWidth(120)
        self.calibrateBtn.setEnabled(False)
        self.calibrateBtn.setToolTip('将戒指平放并保持静止后点击，同时校准画布方向与陀螺仪零偏')

        self.dirCalBtn = PushButton('方向校准', self.statusCard)
        self.dirCalBtn.setFixedWidth(110)
        self.dirCalBtn.setEnabled(False)
        self.dirCalBtn.setToolTip('按提示先向右、再向上挥动戒指，适配不同佩戴方向的上下左右')

        self._buildStatusCard()

        # ── Controls card ────────────────────────────────────────
        self.controlsCard = SimpleCardWidget(self.view)
        self.controlsCard.setBorderRadius(12)
        self.controlsCard.setFixedHeight(72)

        self.modeCombo = ComboBox(self.controlsCard)
        self.modeCombo.addItems(['常落笔模式', '手动模式'])
        self.modeCombo.setCurrentIndex(0)
        self.modeCombo.setFixedWidth(140)

        self.penBtn = TogglePushButton('落笔', self.controlsCard)
        self.penBtn.setFixedWidth(100)
        self.penBtn.setEnabled(False)

        self.penStateLabel = BodyLabel('已抬笔', self.controlsCard)
        self.penStateLabel.setObjectName('penStateLabel')
        self.penStateLabel.setProperty('penDown', False)

        self.sensitivityLabel = BodyLabel('灵敏度', self.controlsCard)
        self.sensitivitySlider = Slider(Qt.Horizontal, self.controlsCard)
        self.sensitivitySlider.setRange(1, 10)
        self.sensitivitySlider.setValue(5)
        self.sensitivitySlider.setFixedWidth(140)

        self.clearBtn = PushButton('清空画布', self.controlsCard)
        self.clearBtn.setFixedWidth(110)

        self.controlsLayout = QHBoxLayout(self.controlsCard)
        self.controlsLayout.setContentsMargins(20, 16, 20, 16)
        self.controlsLayout.setSpacing(12)
        self.controlsLayout.addWidget(BodyLabel('落笔模式', self.controlsCard))
        self.controlsLayout.addWidget(self.modeCombo)
        self.controlsLayout.addSpacing(12)
        self.controlsLayout.addWidget(self.penBtn)
        self.controlsLayout.addWidget(self.penStateLabel)
        self.controlsLayout.addStretch(1)
        self.controlsLayout.addWidget(self.sensitivityLabel)
        self.controlsLayout.addWidget(self.sensitivitySlider)
        self.controlsLayout.addSpacing(12)
        self.controlsLayout.addWidget(self.clearBtn)

        # ── Canvas card ──────────────────────────────────────────
        self.canvasSection = SubtitleLabel('2D 画布', self.view)
        self.canvasHint = CaptionLabel(
            '手动模式下：点击「落笔/抬笔」按钮或单击戒指按键切换笔的状态；'
            '建议开始后先点「方向校准」，按提示向右、向上挥动戒指，以适配你的佩戴方向',
            self.view
        )
        self.canvasHint.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.canvasCard = SimpleCardWidget(self.view)
        self.canvasCard.setBorderRadius(12)
        self.canvasCard.setMinimumHeight(480)

        self.plotWidget = pg.PlotWidget(self.canvasCard)
        self.plotWidget.setMenuEnabled(False)
        self.plotWidget.setMouseEnabled(x=False, y=False)
        self.plotWidget.hideButtons()
        self.plotWidget.setAspectLocked(True)
        self.plotWidget.showGrid(x=True, y=True, alpha=0.2)
        self.plotWidget.setXRange(-self.CANVAS_RANGE, self.CANVAS_RANGE, padding=0)
        self.plotWidget.setYRange(-self.CANVAS_RANGE, self.CANVAS_RANGE, padding=0)

        # Current position marker
        self._cursorItem = pg.ScatterPlotItem(
            size=12, brush=pg.mkBrush(0, 120, 212, 200),
            pen=pg.mkPen('w', width=1))
        self._cursorItem.setData([0.0], [0.0])
        self.plotWidget.addItem(self._cursorItem)

        self.canvasLayout = QVBoxLayout(self.canvasCard)
        self.canvasLayout.setContentsMargins(12, 12, 12, 12)
        self.canvasLayout.addWidget(self.plotWidget)

        self.__initWidget()
        self.__connectSignals()

    # ── UI setup ─────────────────────────────────────────────────

    def _buildStatusCard(self):
        cardLayout = QHBoxLayout(self.statusCard)
        cardLayout.setContentsMargins(20, 16, 20, 16)
        cardLayout.setSpacing(16)
        cardLayout.addWidget(self.statusIcon, 0, Qt.AlignVCenter)
        cardLayout.addSpacing(4)

        textLayout = QVBoxLayout()
        textLayout.setSpacing(4)
        textLayout.addWidget(self.statusLabel)
        textLayout.addWidget(self.connectionHint)
        textLayout.addStretch(1)
        cardLayout.addLayout(textLayout)
        cardLayout.addStretch(1)
        cardLayout.addWidget(self.dirCalBtn, 0, Qt.AlignVCenter)
        cardLayout.addWidget(self.calibrateBtn, 0, Qt.AlignVCenter)
        cardLayout.addWidget(self.toggleBtn, 0, Qt.AlignVCenter)

    def __initWidget(self):
        self.setObjectName('drawingInterface')
        self.view.setObjectName('view')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        StyleSheet.DRAWING_INTERFACE.apply(self)

        self.vBoxLayout.setContentsMargins(36, 24, 36, 36)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.statusCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.canvasSection)
        self.vBoxLayout.addWidget(self.controlsCard)
        self.vBoxLayout.addWidget(self.canvasCard)
        self.vBoxLayout.addWidget(self.canvasHint)

        self._updatePenUi()

    def __connectSignals(self):
        self.toggleBtn.toggled.connect(self.__onToggleDrawing)
        self.calibrateBtn.clicked.connect(self.__onCalibrate)
        self.dirCalBtn.clicked.connect(self.__onDirCalClicked)
        self.modeCombo.currentIndexChanged.connect(self.__onModeChanged)
        self.penBtn.toggled.connect(self.__onPenBtnToggled)
        self.clearBtn.clicked.connect(self.__clearCanvas)
        self.ringPressed.connect(self.__onRingPressed)

        signalBus.deviceConnected.connect(self.__onDeviceConnected)
        signalBus.deviceDisconnected.connect(self.__onDeviceDisconnected)
        signalBus.modeStarted.connect(self.__onOtherModeStarted)

        self._collector.batchReceived.connect(self.__onBatchReceived)
        self._collector.error.connect(self.__onCollectorError)
        self._collector.startedSuccessfully.connect(self.__onCollectorStarted)
        self._collector.stopped.connect(self.__onCollectorStopped)

    # ── Connection callbacks ─────────────────────────────────────

    def __onDeviceConnected(self, name: str, address: str):
        self._connected = True
        self.connectionHint.setText(f'已连接: {name} ({address})')
        self.toggleBtn.setEnabled(True)

    def __onDeviceDisconnected(self):
        self._connected = False
        self.connectionHint.setText('请先在「连接戒指」页面连接设备')
        self.toggleBtn.setEnabled(False)
        self.calibrateBtn.setEnabled(False)
        self.dirCalBtn.setEnabled(False)
        self.__cancelDirCal(silent=True)
        if self._active:
            self.__stopDrawing()

    # ── Start / stop ─────────────────────────────────────────────

    def __onToggleDrawing(self, checked: bool):
        if checked:
            self.__startDrawing()
        else:
            self.__stopDrawing()

    def __startDrawing(self):
        client = _get_shared_client()
        if client is None:
            InfoBar.warning(
                '未连接设备', '请先连接戒指再开始绘制',
                parent=self.window(), duration=2000,
                position=InfoBarPosition.TOP_RIGHT
            )
            self.toggleBtn.setChecked(False)
            return

        from . import connect_interface
        loop_thread = connect_interface.async_loop_thread
        if loop_thread is None:
            InfoBar.warning(
                '事件循环未就绪', '请重新连接戒指后再试',
                parent=self.window(), duration=2000,
                position=InfoBarPosition.TOP_RIGHT
            )
            self.toggleBtn.setChecked(False)
            return

        # Reset position and canvas
        self.__clearCanvas()
        self._posX = 0.0
        self._posY = 0.0
        self._lastTsMs = None

        # Reset filtering / bias state
        self._gyroBias = np.zeros(3, dtype=np.float64)
        self._wFilt = np.zeros(3, dtype=np.float64)
        self._stillCount = 0
        self._recentGyro.clear()

        self._collector.set_client(client, loop_thread)
        self._collector.start()

    def __stopDrawing(self):
        self._collector.stop_collecting()

    def __onCollectorStarted(self, start_info):
        self._active = True
        signalBus.modeStarted.emit('drawing')
        self._gyroRangeDps = getattr(start_info, 'gyro_range_dps', None) or 2000.0
        self.calibrateBtn.setEnabled(True)
        self.dirCalBtn.setEnabled(True)

        self.statusLabel.setText('轨迹绘制中')
        self.statusLabel.setProperty('active', True)
        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)
        self.toggleBtn.setText('停止绘制')

        # Pen state by mode
        if self._mode == 'always':
            self.__setPenDown(True)
        else:
            self.__setPenDown(False)
            self.penBtn.setEnabled(True)
            self._register_ring_handler()

        InfoBar.success(
            '绘制已开启',
            ('常落笔模式：移动即绘制' if self._mode == 'always'
             else '手动模式：按「落笔」按钮或单击戒指按键开始绘制')
            + '；建议先点击「校准画布」',
            parent=self.window(), duration=3000,
            position=InfoBarPosition.TOP_RIGHT
        )

    def __onCollectorStopped(self):
        self._active = False
        signalBus.modeStopped.emit('drawing')
        self._unregister_ring_handler()
        self.__setPenDown(False)
        self.penBtn.setEnabled(False)
        self.calibrateBtn.setEnabled(False)
        self.dirCalBtn.setEnabled(False)
        self.__cancelDirCal(silent=True)

        self.statusLabel.setText('轨迹绘制已停止')
        self.statusLabel.setProperty('active', False)
        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)
        self.toggleBtn.setText('开始绘制')
        self.toggleBtn.setChecked(False)

    def __onCollectorError(self, message: str):
        InfoBar.error(
            '绘制出错', message,
            parent=self.window(), duration=3000,
            position=InfoBarPosition.TOP_RIGHT
        )
        self.toggleBtn.setChecked(False)
        self.__onCollectorStopped()

    def __onOtherModeStarted(self, mode: str):
        """Auto-stop drawing when another mode starts."""
        if mode == 'drawing' or not self._active:
            return
        # If the new mode also uses the sensor stream, don't send
        # stop_sensor_report, otherwise it would kill the new mode's stream
        self._collector.stop_collecting(send_stop=mode not in _STREAM_MODES)
        InfoBar.info(
            '轨迹绘制已自动停止', '已开启其他模式，绘制自动退出',
            parent=self.window(), duration=2000,
            position=InfoBarPosition.TOP_RIGHT
        )

    # ── Pen mode / pen state ─────────────────────────────────────

    def __onModeChanged(self, index: int):
        self._mode = 'manual' if index == 1 else 'always'
        if not self._active:
            self._updatePenUi()
            return

        if self._mode == 'always':
            self._unregister_ring_handler()
            self.penBtn.setEnabled(False)
            self.__setPenDown(True)
        else:
            self._register_ring_handler()
            self.penBtn.setEnabled(True)
            self.__setPenDown(False)

    def __onPenBtnToggled(self, checked: bool):
        # Only meaningful in manual mode while drawing
        if self._mode != 'manual' or not self._active:
            return
        self.__setPenDown(checked)

    def __onRingPressed(self):
        """Ring single-click toggles pen state in manual mode."""
        if self._mode != 'manual' or not self._active:
            return
        # Sync the toggle button; its toggled signal applies the pen state
        self.penBtn.setChecked(not self._penDown)

    def __setPenDown(self, down: bool):
        if down == self._penDown:
            self._updatePenUi()
            return
        self._penDown = down

        if down:
            self.__beginStroke()
        else:
            self._currentStroke = None
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

    def __beginStroke(self):
        """Start a new stroke at the current position."""
        pen = pg.mkPen(color=(0, 120, 212), width=3)
        curve = pg.PlotCurveItem(pen=pen)
        self.plotWidget.addItem(curve)
        stroke = ([self._posX], [self._posY], curve)
        self._strokes.append(stroke)
        self._currentStroke = stroke

    def __clearCanvas(self):
        for xs, ys, curve in self._strokes:
            self.plotWidget.removeItem(curve)
        self._strokes.clear()
        self._currentStroke = None
        if self._penDown:
            self.__beginStroke()

    # ── Canvas calibration ────────────────────────────────

    def __onCalibrate(self):
        """Calibrate the canvas using the current ring attitude.

        The gravity direction gives the true vertical axis; the canvas is
        the vertical plane (perpendicular to the calibrated surface).
        Yaw about the vertical axis -> horizontal movement; pitch about
        the in-canvas horizontal axis -> vertical movement.
        """
        if self._latestAccel is None:
            InfoBar.warning(
                '暂无数据', '请先开始绘制并等待传感器数据到达',
                parent=self.window(), duration=2000,
                position=InfoBarPosition.TOP_RIGHT
            )
            return

        norm = np.linalg.norm(self._latestAccel)
        if norm < 0.1:
            InfoBar.warning(
                '数据无效', '加速度太小，无法校准，请保持戒指静止后重试',
                parent=self.window(), duration=2000,
                position=InfoBarPosition.TOP_RIGHT
            )
            return

        # True vertical axis = gravity direction in the sensor frame;
        # yaw about it moves the pen horizontally on the vertical canvas
        v = self._latestAccel / norm

        # In-canvas horizontal axis: project device Y axis onto the
        # horizontal plane (perpendicular to gravity); fall back to device
        # X axis if Y is (nearly) parallel to the vertical axis
        h = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        h = h - np.dot(h, v) * v
        if np.linalg.norm(h) < 0.2:
            h = np.array([1.0, 0.0, 0.0], dtype=np.float64)
            h = h - np.dot(h, v) * v
        h = h / np.linalg.norm(h)

        self._axisVert = v
        self._axisHoriz = h
        self._calibrated = True

        # Still-position gyro bias calibration: if the ring has been held
        # still, use the recent samples' mean as the gyro zero offset
        bias_done = False
        if len(self._recentGyro) >= 10:
            arr = np.array(self._recentGyro)
            if float(arr.std(axis=0).max()) < 5.0:
                self._gyroBias = arr.mean(axis=0)
                self._wFilt = np.zeros(3, dtype=np.float64)
                self._stillCount = 0
                bias_done = True

        # Restart drawing from canvas center on the new plane
        self._posX = 0.0
        self._posY = 0.0
        self._cursorItem.setData([0.0], [0.0])
        if self._penDown:
            self.__beginStroke()

        msg = '已以垂直于当前面的竖直面作为画布，光标已归中'
        if bias_done:
            msg += '；已完成陀螺仪静止零偏校准'
        else:
            msg += '；未能校准零偏（校准时请保持戒指静止）'
        InfoBar.success(
            '校准完成', msg,
            parent=self.window(), duration=2500,
            position=InfoBarPosition.TOP_RIGHT
        )

    # ── Direction (wear-orientation) calibration ────────────

    def __onDirCalClicked(self):
        """Start or cancel the guided direction calibration.

        The user is asked to swing the ring RIGHT, hold still, then swing
        UP. The dominant rotation axes of the two motions directly define
        the canvas mapping, so the drawing directions match the user's
        intuition regardless of how the ring is worn.
        """
        if self._dirCalStage is not None:
            self.__cancelDirCal()
            return
        if not self._active:
            return

        self.__setPenDown(False)
        self._dirCalStage = 'right'
        self._dirCalSamples = []
        self._dirCalRightAxis = None
        self._dirCalStillCount = 0
        self._dirCalEndCount = 0
        self._dirCalStartTime = time.monotonic()
        self.dirCalBtn.setText('取消校准')
        self.calibrateBtn.setEnabled(False)
        self.statusLabel.setText('方向校准 1/2：请向右挥动戒指')
        InfoBar.info(
            '方向校准开始', '第 1 步：请将戒指向右挥动一下（快挥、慢挥均可）',
            parent=self.window(), duration=3000,
            position=InfoBarPosition.TOP_RIGHT
        )

    def __cancelDirCal(self, silent=False):
        """Reset the direction calibration state machine."""
        was_running = self._dirCalStage is not None
        self._dirCalStage = None
        self._dirCalSamples = []
        self._dirCalRightAxis = None
        self._dirCalStillCount = 0
        self._dirCalEndCount = 0
        self.dirCalBtn.setText('方向校准')
        if silent or not was_running:
            return
        self.calibrateBtn.setEnabled(self._active)
        self.statusLabel.setText('轨迹绘制中' if self._active else '轨迹绘制未开启')
        InfoBar.info(
            '已取消', '方向校准已取消',
            parent=self.window(), duration=2000,
            position=InfoBarPosition.TOP_RIGHT
        )

    def __processDirCalSample(self, w):
        """Feed one bias-removed gyro sample (dps) to the calibration
        state machine. Returns True while calibration is consuming data."""
        stage = self._dirCalStage
        if stage is None:
            return False

        now = time.monotonic()
        if now - self._dirCalStartTime > self.DIRCAL_TIMEOUT_S:
            # Be lenient: if we already caught part of a swing, use it
            if stage in ('right', 'up') and len(self._dirCalSamples) >= self.DIRCAL_MIN_SAMPLES:
                self.__settleDirCalStage(now)
            else:
                self.__finishDirCal(False, '未检测到有效挥动，请重试（挥动一下即可）')
            return True

        mag = float(np.linalg.norm(w))

        if stage == 'wait_still':
            if mag < self.DIRCAL_STILL_DPS:
                self._dirCalStillCount += 1
            else:
                self._dirCalStillCount = 0
            if self._dirCalStillCount >= self.DIRCAL_STILL_SAMPLES:
                self._dirCalSamples = []
                self._dirCalEndCount = 0
                self._dirCalStage = 'up'
                self._dirCalStartTime = now
                self.statusLabel.setText('方向校准 2/2：请向上挥动戒指')
                InfoBar.info(
                    '很好！', '第 2 步：请将戒指向上挥动一下',
                    parent=self.window(), duration=3000,
                    position=InfoBarPosition.TOP_RIGHT
                )
            return True

        # stage in ('right', 'up'): collect one swing, settle when it ends
        if mag >= self.DIRCAL_MOTION_DPS:
            self._dirCalSamples.append(w.copy())
            self._dirCalEndCount = 0
            if len(self._dirCalSamples) >= self.DIRCAL_MAX_SAMPLES:
                self.__settleDirCalStage(now)
            return True

        # Below motion threshold: if a swing was in progress, check whether
        # it has ended so a single quick flick is enough to settle the stage
        if self._dirCalSamples:
            if mag < self.DIRCAL_END_DPS:
                self._dirCalEndCount += 1
            else:
                self._dirCalEndCount = 0
            if (self._dirCalEndCount >= self.DIRCAL_END_SAMPLES
                    and len(self._dirCalSamples) >= self.DIRCAL_MIN_SAMPLES):
                self.__settleDirCalStage(now)
        return True

    def __settleDirCalStage(self, now):
        """Compute the dominant rotation axis of the collected swing and
        advance the state machine.

        A quick flick usually contains a return swing whose rotation is
        opposite; averaging everything would cancel out. We therefore keep
        only samples consistent with the peak-magnitude sample (the initial
        stroke) and use a magnitude-weighted mean.
        """
        arr = np.array(self._dirCalSamples)
        self._dirCalSamples = []
        self._dirCalEndCount = 0

        mags = np.linalg.norm(arr, axis=1)
        peak = arr[int(np.argmax(mags))]
        keep = arr[arr @ peak > 0.0]
        if len(keep) == 0:
            self.__finishDirCal(False, '未检测到一致的转动方向，请重试')
            return

        mean = keep.sum(axis=0)  # samples already magnitude-weighted
        mean_norm = float(np.linalg.norm(mean))
        if mean_norm < 1e-6:
            self.__finishDirCal(False, '未检测到一致的转动方向，请重试')
            return
        axis = mean / mean_norm

        if self._dirCalStage == 'right':
            self._dirCalRightAxis = axis
            self._dirCalStillCount = 0
            self._dirCalStage = 'wait_still'
            self._dirCalStartTime = now
            self.statusLabel.setText('方向校准：请保持戒指静止…')
        else:
            self.__applyDirCal(axis)

    def __applyDirCal(self, up_axis):
        """Build the canvas mapping from the two measured rotation axes.

        dx = -(w·n): swinging right must give dx > 0, so n = -right_axis.
        dy = -(w·u): swinging up must give dy > 0, so u = -up_axis,
        orthogonalized against n (Gram-Schmidt).
        """
        n = -self._dirCalRightAxis
        u_raw = -up_axis

        if abs(float(np.dot(n, u_raw))) > 0.85:
            self.__finishDirCal(False, '两次挥动方向过于接近，请重试（先向右、再向上）')
            return

        u = u_raw - np.dot(u_raw, n) * n
        u = u / np.linalg.norm(u)

        self._axisVert = n
        self._axisHoriz = u
        self._calibrated = True
        self._wFilt = np.zeros(3, dtype=np.float64)

        # Recenter cursor on the new mapping
        self._posX = 0.0
        self._posY = 0.0
        self._cursorItem.setData([0.0], [0.0])

        self.__finishDirCal(True, '已根据你的佩戴方向校准上下左右，光标已归中')

    def __finishDirCal(self, success, message):
        """End the calibration flow and restore normal drawing state."""
        self._dirCalStage = None
        self._dirCalSamples = []
        self._dirCalRightAxis = None
        self._dirCalStillCount = 0
        self._dirCalEndCount = 0
        self.dirCalBtn.setText('方向校准')
        self.calibrateBtn.setEnabled(self._active)
        self.statusLabel.setText('轨迹绘制中' if self._active else '轨迹绘制未开启')

        # Restore pen for always-down mode after calibration
        if success and self._active and self._mode == 'always':
            self.__setPenDown(True)

        if success:
            InfoBar.success(
                '方向校准完成', message,
                parent=self.window(), duration=3000,
                position=InfoBarPosition.TOP_RIGHT
            )
        else:
            InfoBar.warning(
                '方向校准失败', message,
                parent=self.window(), duration=3000,
                position=InfoBarPosition.TOP_RIGHT
            )

    # ── Data processing ──────────────────────────────────────

    def __onBatchReceived(self, batch):
        gain = self.sensitivitySlider.value() * 0.02
        rng = self._gyroRangeDps or 2000.0
        scale = rng / 32768.0
        n, u = self._axisVert, self._axisHoriz
        updated = False

        for sample in batch.samples:
            # Keep latest accel vector for canvas calibration
            self._latestAccel = np.array(
                [sample.accel_x, sample.accel_y, sample.accel_z],
                dtype=np.float64)

            ts = sample.timestamp_ms
            if self._lastTsMs is None:
                self._lastTsMs = ts
                continue
            dt = (ts - self._lastTsMs) / 1000.0
            self._lastTsMs = ts
            if dt <= 0 or dt > 0.5:
                continue

            # Angular velocity (dps) in the sensor frame
            w_raw = np.array(
                [sample.gyro_x, sample.gyro_y, sample.gyro_z],
                dtype=np.float64) * scale
            self._recentGyro.append(w_raw)

            # Auto still-position bias tracking: while the ring is (nearly)
            # still, slowly follow the readings to learn the gyro zero
            # offset. Converges faster during the initial still period.
            if np.linalg.norm(w_raw - self._gyroBias) < self.STILL_THRESHOLD_DPS:
                self._stillCount += 1
                beta = 0.1 if self._stillCount <= 50 else 0.02
                self._gyroBias = (1.0 - beta) * self._gyroBias + beta * w_raw
            else:
                self._stillCount = 0

            # Remove bias, then feed the direction calibration if running
            w = w_raw - self._gyroBias
            if self._dirCalStage is not None:
                self.__processDirCalSample(w)
                continue

            # EMA low-pass filter to smooth the stroke
            self._wFilt = self.LPF_ALPHA * w + (1.0 - self.LPF_ALPHA) * self._wFilt

            # Project onto the calibrated axes:
            # yaw about the true vertical axis -> horizontal movement,
            # pitch about the in-canvas horizontal axis -> vertical movement
            # (vertical canvas, air-mouse model). Default axes (Z, Y) match
            # the uncalibrated mapping: dx from gyro_z, dy from gyro_y.
            wn = float(np.dot(self._wFilt, n))
            wu = float(np.dot(self._wFilt, u))
            if abs(wn) < self.GYRO_DEADZONE_DPS:
                wn = 0.0
            if abs(wu) < self.GYRO_DEADZONE_DPS:
                wu = 0.0

            dx = -wn * dt * gain * 10.0
            dy = -wu * dt * gain * 10.0
            if dx == 0.0 and dy == 0.0:
                continue

            self._posX = max(-self.CANVAS_RANGE, min(self.CANVAS_RANGE, self._posX + dx))
            self._posY = max(-self.CANVAS_RANGE, min(self.CANVAS_RANGE, self._posY + dy))
            updated = True

            if self._penDown and self._currentStroke is not None:
                xs, ys, _ = self._currentStroke
                xs.append(self._posX)
                ys.append(self._posY)

        if not updated:
            return

        # Refresh cursor and current stroke
        self._cursorItem.setData([self._posX], [self._posY])
        if self._penDown and self._currentStroke is not None:
            xs, ys, curve = self._currentStroke
            curve.setData(xs, ys)

    # ── Ring button handler ──────────────────────────────────────

    def _register_ring_handler(self):
        client = _get_shared_client()
        if client is None or self._handlers_registered:
            return
        client.add_packet_handler(SensorCommand.KEY_SINGLE_PRESS, self._on_single_press)
        self._handlers_registered = True

    def _unregister_ring_handler(self):
        if not self._handlers_registered:
            return
        client = _get_shared_client()
        if client is not None:
            try:
                client.remove_packet_handler(
                    SensorCommand.KEY_SINGLE_PRESS, self._on_single_press)
            except (ValueError, KeyError):
                pass
        self._handlers_registered = False

    async def _on_single_press(self, packet):
        """Called from the BLE loop thread; marshal to the GUI thread."""
        self.ringPressed.emit()

    # ── Cleanup ──────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._active:
            self.__stopDrawing()
            self._collector.wait(3000)
        event.accept()
