# coding: utf-8
"""2D real-time trajectory drawing interface.

Maps the ring's gyroscope motion (air-mouse style) to a 2D canvas and draws
the trace in real time. Two pen modes are supported:
  - 常落笔模式: the pen is always down, drawing starts immediately
  - 手动模式:   press the button (UI or ring single-click) to toggle pen up/down
"""
import time

import pyqtgraph as pg
from PyQt5.QtCore import Qt, pyqtSignal, QThread
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (ScrollArea, FluentIcon, TitleLabel, BodyLabel,
                            StrongBodyLabel, CaptionLabel, SubtitleLabel,
                            SimpleCardWidget, TogglePushButton, IconWidget,
                            PushButton, ComboBox, Slider, InfoBar,
                            InfoBarPosition)
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

    CANVAS_RANGE = 100.0      # canvas extends from -RANGE to +RANGE
    GYRO_DEADZONE_DPS = 2.0   # ignore tiny angular velocities (noise)

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
            '手动模式下：点击「落笔/抬笔」按钮或单击戒指按键切换笔的状态',
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

        self._collector.set_client(client, loop_thread)
        self._collector.start()

    def __stopDrawing(self):
        self._collector.stop_collecting()

    def __onCollectorStarted(self, start_info):
        self._active = True
        signalBus.modeStarted.emit('drawing')
        self._gyroRangeDps = getattr(start_info, 'gyro_range_dps', None) or 2000.0

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
            '常落笔模式：移动即绘制' if self._mode == 'always'
            else '手动模式：按「落笔」按钮或单击戒指按键开始绘制',
            parent=self.window(), duration=2000,
            position=InfoBarPosition.TOP_RIGHT
        )

    def __onCollectorStopped(self):
        self._active = False
        signalBus.modeStopped.emit('drawing')
        self._unregister_ring_handler()
        self.__setPenDown(False)
        self.penBtn.setEnabled(False)

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

    # ── Data processing ──────────────────────────────────────────

    def __onBatchReceived(self, batch):
        gain = self.sensitivitySlider.value() * 0.02
        rng = self._gyroRangeDps or 2000.0
        scale = rng / 32768.0
        updated = False

        for sample in batch.samples:
            ts = sample.timestamp_ms
            if self._lastTsMs is None:
                self._lastTsMs = ts
                continue
            dt = (ts - self._lastTsMs) / 1000.0
            self._lastTsMs = ts
            if dt <= 0 or dt > 0.5:
                continue

            # Air-mouse mapping: yaw (gyro_z) -> horizontal, pitch (gyro_y) -> vertical
            gz = sample.gyro_z * scale
            gy = sample.gyro_y * scale
            if abs(gz) < self.GYRO_DEADZONE_DPS:
                gz = 0.0
            if abs(gy) < self.GYRO_DEADZONE_DPS:
                gy = 0.0

            dx = -gz * dt * gain * 10.0
            dy = -gy * dt * gain * 10.0
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
