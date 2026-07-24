# coding: utf-8
"""Real-time sensor data acquisition interface.

Continuously collects accelerometer and gyroscope data from the ring and
plots it in real time. Data can be exported to CSV for offline analysis.
"""
import csv
import datetime
import time
from dataclasses import dataclass

import pyqtgraph as pg
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFileDialog,
                             QLabel, QStackedWidget, QGridLayout)

from qfluentwidgets import (ScrollArea, FluentIcon, TitleLabel, BodyLabel,
                            StrongBodyLabel, CaptionLabel, SubtitleLabel,
                            SimpleCardWidget, TogglePushButton, IconWidget,
                            PrimaryPushButton, ComboBox, SwitchButton, InfoBar,
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


@dataclass
class _SensorSample:
    timestamp_ms: int
    accel_x: int
    accel_y: int
    accel_z: int
    gyro_x: int
    gyro_y: int
    gyro_z: int


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


class SensorInterface(ScrollArea):
    """Real-time IMU data acquisition and visualization interface."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        self._active = False
        self._connected = False
        self._samples: list[_SensorSample] = []
        self._max_plot_points = 500
        self._collector = _CollectorThread(self)
        self._start_time_s = 0.0
        self._durationTimer = QTimer(self)
        self._durationTimer.timeout.connect(self._updateDuration)

        # Display settings
        self._layoutMode = 'single'  # 'single' or 'separate'
        self._useMetersPerSecSq = True
        self._accelRangeG = None
        self._gyroRangeDps = None
        self._GRAVITY = 9.80665

        # Title
        self.titleLabel = TitleLabel('传感器采集', self.view)
        self.subtitleLabel = CaptionLabel(
            '实时采集戒指加速度计与陀螺仪数据并导出 CSV；开始采集前请确保戒指已切换至手势模式',
            self.view
        )
        self.subtitleLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        # Status card
        self.statusCard = SimpleCardWidget(self.view)
        self.statusCard.setBorderRadius(12)
        self.statusCard.setFixedHeight(96)

        self.statusIcon = IconWidget(FIF.MOVE, self.statusCard)
        self.statusIcon.setFixedSize(40, 40)

        self.statusLabel = StrongBodyLabel('传感器采集未开启', self.statusCard)
        self.statusLabel.setObjectName('sensorStatusLabel')
        self.statusLabel.setProperty('active', False)

        self.connectionHint = CaptionLabel('请先在「连接戒指」页面连接设备', self.statusCard)
        self.connectionHint.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.toggleBtn = TogglePushButton('开启采集', self.statusCard)
        self.toggleBtn.setFixedWidth(140)
        self.toggleBtn.setEnabled(False)

        self._buildStatusCard()

        # Chart controls
        self.chartControlsCard = SimpleCardWidget(self.view)
        self.chartControlsCard.setBorderRadius(12)
        self.chartControlsCard.setFixedHeight(72)

        self.layoutCombo = ComboBox(self.chartControlsCard)
        self.layoutCombo.addItems(['单图合并显示', '每轴独立显示'])
        self.layoutCombo.setCurrentIndex(0)
        self.layoutCombo.setFixedWidth(160)

        self.unitSwitch = SwitchButton(self.chartControlsCard)
        self.unitSwitch.setChecked(True)
        self.unitLabel = BodyLabel('加速度：m/s²', self.chartControlsCard)

        self.controlsLayout = QHBoxLayout(self.chartControlsCard)
        self.controlsLayout.setContentsMargins(20, 16, 20, 16)
        self.controlsLayout.addWidget(BodyLabel('图表布局', self.chartControlsCard))
        self.controlsLayout.addWidget(self.layoutCombo)
        self.controlsLayout.addSpacing(24)
        self.controlsLayout.addWidget(self.unitLabel)
        self.controlsLayout.addWidget(self.unitSwitch)
        self.controlsLayout.addStretch(1)

        # Chart card
        self.chartSection = SubtitleLabel('实时波形', self.view)
        self.chartCard = SimpleCardWidget(self.view)
        self.chartCard.setBorderRadius(12)
        self.chartCard.setMinimumHeight(360)

        self._chartStack = QStackedWidget(self.chartCard)
        self._singleChartPage, self._singleCurves = self._buildSingleChart()
        self._separateChartPage, self._separateCurves = self._buildSeparateCharts()
        self._chartStack.addWidget(self._singleChartPage)
        self._chartStack.addWidget(self._separateChartPage)

        self.chartLayout = QVBoxLayout(self.chartCard)
        self.chartLayout.setContentsMargins(12, 12, 12, 12)
        self.chartLayout.addWidget(self._chartStack)

        # Real-time values grid
        self.valuesSection = SubtitleLabel('实时数值', self.view)
        self.valuesCard = SimpleCardWidget(self.view)
        self.valuesCard.setBorderRadius(12)

        self._valueLabels = {}
        self.valuesGrid = QGridLayout(self.valuesCard)
        self.valuesGrid.setContentsMargins(12, 12, 12, 12)
        self.valuesGrid.setSpacing(8)

        value_configs = [
            ('accel_x', '加速度 X', 'm/s²'),
            ('accel_y', '加速度 Y', 'm/s²'),
            ('accel_z', '加速度 Z', 'm/s²'),
            ('gyro_x', '陀螺仪 X', 'raw'),
            ('gyro_y', '陀螺仪 Y', 'raw'),
            ('gyro_z', '陀螺仪 Z', 'raw'),
        ]
        for idx, (key, title, default_unit) in enumerate(value_configs):
            cell = self._createValueCell(key, title, default_unit)
            self._valueLabels[key] = cell.findChild(StrongBodyLabel)
            self.valuesGrid.addWidget(cell, idx // 3, idx % 3)

        # Stats card
        self.statsSection = SubtitleLabel('统计信息', self.view)
        self.statsCard = SimpleCardWidget(self.view)
        self.statsCard.setBorderRadius(12)
        self.statsCard.setFixedHeight(88)

        self.sampleCountLabel = self._createStatLabel('样本数', '0')
        self.durationLabel = self._createStatLabel('时长', '0.0s')
        self.rateLabel = self._createStatLabel('采样率', '-')

        self.statsLayout = QHBoxLayout(self.statsCard)
        self.statsLayout.setContentsMargins(20, 16, 20, 16)
        self.statsLayout.setSpacing(36)
        self.statsLayout.addWidget(self.sampleCountLabel)
        self.statsLayout.addWidget(self.durationLabel)
        self.statsLayout.addWidget(self.rateLabel)
        self.statsLayout.addStretch(1)

        # Export card
        self.exportSection = SubtitleLabel('数据导出', self.view)
        self.exportCard = SimpleCardWidget(self.view)
        self.exportCard.setBorderRadius(12)
        self.exportCard.setFixedHeight(88)

        self.exportHint = BodyLabel('停止采集后，可将数据导出为 CSV 文件', self.exportCard)
        self.exportHint.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.exportBtn = PrimaryPushButton('导出 CSV', self.exportCard)
        self.exportBtn.setFixedSize(140, 40)
        self.exportBtn.setEnabled(False)

        self.exportLayout = QHBoxLayout(self.exportCard)
        self.exportLayout.setContentsMargins(20, 16, 20, 16)
        self.exportLayout.addWidget(self.exportHint)
        self.exportLayout.addStretch(1)
        self.exportLayout.addWidget(self.exportBtn)

        self.__initWidget()
        self.__connectSignals()

    def _createStatLabel(self, title: str, value: str):
        """Create a vertical stat label pair."""
        widget = QWidget(self.statsCard)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        value_label = StrongBodyLabel(value, widget)
        title_label = CaptionLabel(title, widget)
        title_label.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        layout.addWidget(value_label)
        layout.addWidget(title_label)
        return widget

    def _createValueCell(self, key: str, title: str, unit: str):
        """Create a single real-time value display cell."""
        cell = SimpleCardWidget(self.valuesCard)
        cell.setBorderRadius(10)
        cell.setFixedHeight(88)

        layout = QVBoxLayout(cell)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)

        title_label = BodyLabel(title, cell)
        title_label.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        value_label = StrongBodyLabel('--', cell)
        value_label.setProperty('valueKey', key)

        unit_label = CaptionLabel(unit, cell)
        unit_label.setObjectName(f'unit_{key}')
        unit_label.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addWidget(unit_label)
        return cell

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

    def _buildSingleChart(self):
        """Create the single-chart page with all 6 curves overlaid."""
        page = QWidget(self._chartStack)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        plot = pg.PlotWidget(page)
        plot.setMenuEnabled(False)
        plot.setMouseEnabled(x=False, y=False)
        plot.showGrid(x=True, y=True, alpha=0.3)
        plot.setLabel('left', '数值')
        plot.setLabel('bottom', '样本')
        plot.addLegend()

        colors = {
            'accel_x': (255, 99, 71),
            'accel_y': (60, 179, 113),
            'accel_z': (30, 144, 255),
            'gyro_x': (255, 165, 0),
            'gyro_y': (147, 112, 219),
            'gyro_z': (220, 20, 60),
        }
        curves = {}
        for name, color in colors.items():
            pen = pg.mkPen(color=color, width=2)
            curves[name] = plot.plot(pen=pen, name=name)

        layout.addWidget(plot)
        return page, curves

    def _buildSeparateCharts(self):
        """Create the separate-chart page with one PlotWidget per axis."""
        page = QWidget(self._chartStack)
        layout = QGridLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        colors = {
            'accel_x': (255, 99, 71),
            'accel_y': (60, 179, 113),
            'accel_z': (30, 144, 255),
            'gyro_x': (255, 165, 0),
            'gyro_y': (147, 112, 219),
            'gyro_z': (220, 20, 60),
        }
        titles = {
            'accel_x': 'accel_x',
            'accel_y': 'accel_y',
            'accel_z': 'accel_z',
            'gyro_x': 'gyro_x',
            'gyro_y': 'gyro_y',
            'gyro_z': 'gyro_z',
        }
        curves = {}
        positions = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)]
        for (name, color), (row, col) in zip(colors.items(), positions):
            plot = pg.PlotWidget(page)
            plot.setMenuEnabled(False)
            plot.setMouseEnabled(x=False, y=False)
            plot.showGrid(x=True, y=True, alpha=0.3)
            plot.setTitle(titles[name])
            plot.setLabel('left', '数值')
            plot.setLabel('bottom', '样本')
            plot.setFixedHeight(180)
            pen = pg.mkPen(color=color, width=2)
            curves[name] = plot.plot(pen=pen)
            layout.addWidget(plot, row, col)

        return page, curves

    def __initWidget(self):
        self.setObjectName('sensorInterface')
        self.view.setObjectName('view')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        StyleSheet.SENSOR_INTERFACE.apply(self)

        self.vBoxLayout.setContentsMargins(36, 24, 36, 36)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.statusCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.chartSection)
        self.vBoxLayout.addWidget(self.chartControlsCard)
        self.vBoxLayout.addWidget(self.chartCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.valuesSection)
        self.vBoxLayout.addWidget(self.valuesCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.statsSection)
        self.vBoxLayout.addWidget(self.statsCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.exportSection)
        self.vBoxLayout.addWidget(self.exportCard)

    def __connectSignals(self):
        self.toggleBtn.toggled.connect(self.__onToggleCollection)
        self.exportBtn.clicked.connect(self.__onExport)
        self.layoutCombo.currentIndexChanged.connect(self.__onLayoutChanged)
        self.unitSwitch.checkedChanged.connect(self.__onUnitChanged)
        signalBus.deviceConnected.connect(self.__onDeviceConnected)
        signalBus.deviceDisconnected.connect(self.__onDeviceDisconnected)
        signalBus.modeStarted.connect(self.__onOtherModeStarted)

        self._collector.batchReceived.connect(self.__onBatchReceived)
        self._collector.error.connect(self.__onCollectorError)
        self._collector.startedSuccessfully.connect(self.__onCollectorStarted)
        self._collector.stopped.connect(self.__onCollectorStopped)

    def __onDeviceConnected(self, name: str, address: str):
        self._connected = True
        self.connectionHint.setText(f'已连接: {name} ({address})')
        self.toggleBtn.setEnabled(True)

    def __onDeviceDisconnected(self):
        self._connected = False
        self.connectionHint.setText('请先在「连接戒指」页面连接设备')
        self.toggleBtn.setEnabled(False)
        if self._active:
            self.__stopCollection()

    def __onToggleCollection(self, checked: bool):
        if checked:
            self.__startCollection()
        else:
            self.__stopCollection()

    def __startCollection(self):
        client = _get_shared_client()
        if client is None:
            InfoBar.warning(
                '未连接设备',
                '请先连接戒指再开启采集',
                parent=self.window(), duration=2000,
                position=InfoBarPosition.TOP_RIGHT
            )
            self.toggleBtn.setChecked(False)
            return

        from . import connect_interface
        loop_thread = connect_interface.async_loop_thread
        if loop_thread is None:
            InfoBar.warning(
                '事件循环未就绪',
                '请重新连接戒指后再试',
                parent=self.window(), duration=2000,
                position=InfoBarPosition.TOP_RIGHT
            )
            self.toggleBtn.setChecked(False)
            return

        self._samples.clear()
        self._updateStats()
        self.exportBtn.setEnabled(False)
        self._collector.set_client(client, loop_thread)
        self._collector.start()

    def __stopCollection(self):
        self._collector.stop_collecting()

    def __onCollectorStarted(self, start_info):
        self._active = True
        signalBus.modeStarted.emit('sensor')
        self._start_time_s = time.time()
        self._accelRangeG = start_info.accel_range_g
        self._gyroRangeDps = start_info.gyro_range_dps
        self._durationTimer.start(200)
        self.statusLabel.setText('传感器采集中')
        self.statusLabel.setProperty('active', True)
        self.statusIcon.setIcon(FIF.PAUSE)
        self.toggleBtn.setText('停止采集')
        self.rateLabel.findChild(StrongBodyLabel).setText(
            f'{start_info.sample_rate_hz} Hz'
        )
        self._updateAxisLabels()
        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)

        InfoBar.success(
            '采集已开启',
            '开始接收加速度计与陀螺仪数据',
            parent=self.window(), duration=2000,
            position=InfoBarPosition.TOP_RIGHT
        )

    def __onCollectorStopped(self):
        self._active = False
        signalBus.modeStopped.emit('sensor')
        self._durationTimer.stop()
        self.statusLabel.setText('传感器采集已停止')
        self.statusLabel.setProperty('active', False)
        self.statusIcon.setIcon(FIF.MOVE)
        self.toggleBtn.setText('开启采集')
        self.toggleBtn.setChecked(False)
        self.exportBtn.setEnabled(len(self._samples) > 0)

        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)

        InfoBar.info(
            '采集已停止',
            f'共采集 {len(self._samples)} 个样本',
            parent=self.window(), duration=2000,
            position=InfoBarPosition.TOP_RIGHT
        )

    def __onCollectorError(self, message: str):
        InfoBar.error(
            '采集出错',
            message,
            parent=self.window(), duration=3000,
            position=InfoBarPosition.TOP_RIGHT
        )
        self.toggleBtn.setChecked(False)
        self.__onCollectorStopped()

    def __onOtherModeStarted(self, mode: str):
        """Auto-stop collection when another mode starts."""
        if mode == 'sensor' or not self._active:
            return
        # If the new mode also uses the sensor stream, don't send
        # stop_sensor_report, otherwise it would kill the new mode's stream
        self._collector.stop_collecting(send_stop=mode not in _STREAM_MODES)
        InfoBar.info(
            '传感器采集已自动停止', '已开启其他模式，采集自动退出',
            parent=self.window(), duration=2000,
            position=InfoBarPosition.TOP_RIGHT
        )

    def __onBatchReceived(self, batch):
        latest = None
        for sample in batch.samples:
            latest = _SensorSample(
                sample.timestamp_ms,
                sample.accel_x, sample.accel_y, sample.accel_z,
                sample.gyro_x, sample.gyro_y, sample.gyro_z,
            )
            self._samples.append(latest)

        if latest is not None:
            self._updateValueLabels(latest)

        self._updatePlot()
        self._updateStats()

    def _updateValueLabels(self, sample: _SensorSample):
        """Refresh the 6 real-time value cells."""
        values = {
            'accel_x': self._convertValue('accel_x', sample.accel_x),
            'accel_y': self._convertValue('accel_y', sample.accel_y),
            'accel_z': self._convertValue('accel_z', sample.accel_z),
            'gyro_x': self._convertValue('gyro_x', sample.gyro_x),
            'gyro_y': self._convertValue('gyro_y', sample.gyro_y),
            'gyro_z': self._convertValue('gyro_z', sample.gyro_z),
        }
        for key, label in self._valueLabels.items():
            label.setText(f'{values[key]:.3f}')

    def _accelToMps2(self, raw: int) -> float:
        """Convert raw accelerometer reading to m/s^2."""
        if self._accelRangeG is None:
            return float(raw)
        return raw * self._accelRangeG * self._GRAVITY / 32768.0

    def _convertValue(self, name: str, raw: int) -> float:
        """Return displayed value for a channel."""
        if name.startswith('accel') and self._useMetersPerSecSq:
            return self._accelToMps2(raw)
        return float(raw)

    def _updateAxisLabels(self):
        """Update Y-axis labels based on current unit setting."""
        accel_label = 'm/s²' if self._useMetersPerSecSq else '原始值'
        for name, curve in self._singleCurves.items():
            plot = curve.getViewBox().parentWidget()
            if name.startswith('accel'):
                plot.setLabel('left', accel_label)
        for name, curve in self._separateCurves.items():
            plot = curve.getViewBox().parentWidget()
            if name.startswith('accel'):
                plot.setLabel('left', accel_label)

    def _updatePlot(self):
        count = len(self._samples)
        if count == 0:
            return

        start = max(0, count - self._max_plot_points)
        indices = list(range(start, count))

        data = {name: [] for name in self._singleCurves}
        for sample in self._samples[start:]:
            data['accel_x'].append(self._convertValue('accel_x', sample.accel_x))
            data['accel_y'].append(self._convertValue('accel_y', sample.accel_y))
            data['accel_z'].append(self._convertValue('accel_z', sample.accel_z))
            data['gyro_x'].append(self._convertValue('gyro_x', sample.gyro_x))
            data['gyro_y'].append(self._convertValue('gyro_y', sample.gyro_y))
            data['gyro_z'].append(self._convertValue('gyro_z', sample.gyro_z))

        if self._layoutMode == 'single':
            curves = self._singleCurves
        else:
            curves = self._separateCurves

        for name, curve in curves.items():
            curve.setData(indices, data[name])

    def __onLayoutChanged(self, index: int):
        self._layoutMode = 'separate' if index == 1 else 'single'
        self._chartStack.setCurrentIndex(index)
        self.chartCard.setMinimumHeight(560 if self._layoutMode == 'separate' else 360)
        self._updatePlot()

    def __onUnitChanged(self, checked: bool):
        self._useMetersPerSecSq = checked
        self.unitLabel.setText('加速度：m/s²' if checked else '加速度：原始值')

        accel_unit = 'm/s²' if checked else 'raw'
        for key in ['accel_x', 'accel_y', 'accel_z']:
            # unit labels are children of the value cell; find by object name prefix
            cell = self._valueLabels[key].parentWidget()
            unit_label = cell.findChild(CaptionLabel)
            if unit_label is not None:
                unit_label.setText(accel_unit)

        if self._samples:
            self._updateValueLabels(self._samples[-1])
        self._updateAxisLabels()
        self._updatePlot()

    def _updateStats(self):
        count = len(self._samples)
        self.sampleCountLabel.findChild(StrongBodyLabel).setText(str(count))

    def _updateDuration(self):
        if not self._active:
            return
        duration = time.time() - self._start_time_s
        self.durationLabel.findChild(StrongBodyLabel).setText(f'{duration:.1f}s')

    def __onExport(self):
        if not self._samples:
            InfoBar.warning(
                '没有数据',
                '请先采集数据再导出',
                parent=self.window(), duration=2000,
                position=InfoBarPosition.TOP_RIGHT
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            '导出传感器数据',
            f'ring_sensor_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv',
            'CSV Files (*.csv)'
        )
        if not path:
            return

        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                accel_suffix = '_mps2' if self._useMetersPerSecSq else ''
                writer.writerow([
                    'timestamp_ms',
                    f'accel_x{accel_suffix}', f'accel_y{accel_suffix}', f'accel_z{accel_suffix}',
                    'gyro_x', 'gyro_y', 'gyro_z'
                ])
                for s in self._samples:
                    writer.writerow([
                        s.timestamp_ms,
                        self._convertValue('accel_x', s.accel_x),
                        self._convertValue('accel_y', s.accel_y),
                        self._convertValue('accel_z', s.accel_z),
                        s.gyro_x, s.gyro_y, s.gyro_z
                    ])
            InfoBar.success(
                '导出成功',
                f'已保存到: {path}',
                parent=self.window(), duration=3000,
                position=InfoBarPosition.TOP_RIGHT
            )
        except Exception as exc:
            InfoBar.error(
                '导出失败',
                str(exc),
                parent=self.window(), duration=3000,
                position=InfoBarPosition.TOP_RIGHT
            )

    def closeEvent(self, event):
        if self._active:
            self.__stopCollection()
            self._collector.wait(3000)
        event.accept()
