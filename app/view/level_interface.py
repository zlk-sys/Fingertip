# coding: utf-8
"""3D level / inclinometer interface.

Displays the ring's tilt as a 3D ring model rotated by accelerometer data.
"""
import math

import numpy as np
import pyqtgraph.opengl as gl
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (ScrollArea, FluentIcon, TitleLabel, BodyLabel,
                            StrongBodyLabel, CaptionLabel, SubtitleLabel,
                            SimpleCardWidget, TogglePushButton, PrimaryPushButton,
                            IconWidget, InfoBar, InfoBarPosition)
from qfluentwidgets import FluentIcon as FIF

from ..common.style_sheet import StyleSheet
from ..common.signal_bus import signalBus
from ..sdk.ring_sound import start_sensor_report, stop_sensor_report, wait_sensor_data


def _get_shared_client():
    """Return the current shared BLE client, or None if not connected."""
    from . import connect_interface
    return connect_interface.shared_client


def _build_torus_mesh(major_radius=1.0, minor_radius=0.15, major_segments=64,
                      minor_segments=32):
    """Build vertex and face arrays for a torus centered at origin."""
    vertices = []
    uvs = []
    for i in range(major_segments + 1):
        theta = 2.0 * math.pi * i / major_segments
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        center = np.array([cos_t * major_radius, sin_t * major_radius, 0.0])
        normal_plane = np.array([cos_t, sin_t, 0.0])
        for j in range(minor_segments + 1):
            phi = 2.0 * math.pi * j / minor_segments
            ring_normal = (
                normal_plane * math.cos(phi) +
                np.array([0.0, 0.0, math.sin(phi)])
            )
            vertices.append(center + ring_normal * minor_radius)

    faces = []
    for i in range(major_segments):
        for j in range(minor_segments):
            a = i * (minor_segments + 1) + j
            b = a + minor_segments + 1
            faces.append([a, b, a + 1])
            faces.append([b, b + 1, a + 1])

    return np.array(vertices, dtype=np.float32), np.array(faces, dtype=np.uint32)


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

    def set_client(self, client, loop_thread):
        self._client = client
        self._loop_thread = loop_thread

    def run(self):
        if self._client is None or self._loop_thread is None:
            self.error.emit('BLE client not available')
            return

        self._running = True
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

        try:
            self._loop_thread.run_coro(
                stop_sensor_report(self._client, timeout_s=10.0),
                timeout=12.0,
            )
        except Exception:
            pass
        self.stopped.emit()

    def stop_collecting(self):
        self._running = False


class LevelInterface(ScrollArea):
    """3D level interface showing ring tilt angle in real time."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        self._active = False
        self._connected = False
        self._collector = _CollectorThread(self)
        self._accelRangeG = None
        self._GRAVITY = 9.80665
        self._flatRef = None  # normalized accel vector when ring is flat
        self._latestAccel = None

        # Title
        self.titleLabel = TitleLabel('水平仪', self.view)
        self.subtitleLabel = CaptionLabel(
            '通过加速度计实时显示戒指倾斜角度（3D 视图）',
            self.view
        )
        self.subtitleLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        # Status card
        self.statusCard = SimpleCardWidget(self.view)
        self.statusCard.setBorderRadius(12)
        self.statusCard.setFixedHeight(96)

        self.statusIcon = IconWidget(FIF.ROTATE, self.statusCard)
        self.statusIcon.setFixedSize(40, 40)

        self.statusLabel = StrongBodyLabel('水平仪未开启', self.statusCard)
        self.statusLabel.setObjectName('levelStatusLabel')
        self.statusLabel.setProperty('active', False)

        self.connectionHint = CaptionLabel('请先在「连接戒指」页面连接设备', self.statusCard)
        self.connectionHint.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.toggleBtn = TogglePushButton('开启水平仪', self.statusCard)
        self.toggleBtn.setFixedWidth(140)
        self.toggleBtn.setEnabled(False)

        self.calibrateBtn = PrimaryPushButton('校准水平', self.statusCard)
        self.calibrateBtn.setFixedWidth(120)
        self.calibrateBtn.setEnabled(False)
        self.calibrateBtn.setToolTip('将戒指平放在桌面后点击此按钮')

        self._buildStatusCard()

        # 3D view card
        self.view3DSection = SubtitleLabel('3D 视图', self.view)
        self.view3DCard = SimpleCardWidget(self.view)
        self.view3DCard.setBorderRadius(12)
        self.view3DCard.setMinimumHeight(420)

        self.glWidget = gl.GLViewWidget(self.view3DCard)
        self.glWidget.setCameraPosition(distance=4.5, elevation=25, azimuth=45)

        # Axis helper
        self.axisItem = gl.GLAxisItem()
        self.axisItem.setSize(1.5, 1.5, 1.5)
        self.glWidget.addItem(self.axisItem)

        # Ground grid
        self.gridItem = gl.GLGridItem()
        self.gridItem.setSize(4, 4)
        self.gridItem.setSpacing(1, 1)
        self.gridItem.translate(0, 0, -1.2)
        self.glWidget.addItem(self.gridItem)

        # Ring model
        vertices, faces = _build_torus_mesh(major_radius=1.0, minor_radius=0.18)
        faces = np.hstack([np.full((faces.shape[0], 1), 3, dtype=np.uint32), faces])
        self.ringMesh = gl.GLMeshItem(
            vertexes=vertices,
            faces=faces,
            smooth=True,
            color=(0.2, 0.6, 1.0, 1.0),
            shader='shaded',
            drawEdges=False,
        )
        self.glWidget.addItem(self.ringMesh)

        # Crosshair bubble
        self.bubble = gl.GLMeshItem(
            meshdata=gl.MeshData.sphere(rows=16, cols=16, radius=0.12),
            smooth=True,
            color=(1.0, 0.3, 0.3, 0.9),
            shader='shaded',
        )
        self.glWidget.addItem(self.bubble)

        self.view3DLayout = QVBoxLayout(self.view3DCard)
        self.view3DLayout.setContentsMargins(8, 8, 8, 8)
        self.view3DLayout.addWidget(self.glWidget)

        # Angle data card
        self.angleSection = SubtitleLabel('角度数据', self.view)
        self.angleCard = SimpleCardWidget(self.view)
        self.angleCard.setBorderRadius(12)
        self.angleCard.setFixedHeight(120)

        self.pitchLabel = self._createAngleLabel('Pitch（俯仰）', '--°')
        self.rollLabel = self._createAngleLabel('Roll（横滚）', '--°')
        self.resultantLabel = self._createAngleLabel('合倾斜角', '--°')

        self.angleLayout = QHBoxLayout(self.angleCard)
        self.angleLayout.setContentsMargins(20, 16, 20, 16)
        self.angleLayout.setSpacing(36)
        self.angleLayout.addWidget(self.pitchLabel)
        self.angleLayout.addWidget(self.rollLabel)
        self.angleLayout.addWidget(self.resultantLabel)
        self.angleLayout.addStretch(1)

        self.__initWidget()
        self.__connectSignals()

    def _createAngleLabel(self, title: str, value: str):
        widget = QWidget(self.angleCard)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        value_label = StrongBodyLabel(value, widget)
        title_label = CaptionLabel(title, widget)
        title_label.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        layout.addWidget(value_label)
        layout.addWidget(title_label)
        return widget

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
        cardLayout.addWidget(self.calibrateBtn, 0, Qt.AlignVCenter)
        cardLayout.addWidget(self.toggleBtn, 0, Qt.AlignVCenter)

    def __initWidget(self):
        self.setObjectName('levelInterface')
        self.view.setObjectName('view')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        StyleSheet.LEVEL_INTERFACE.apply(self)

        self.vBoxLayout.setContentsMargins(36, 24, 36, 36)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.statusCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.view3DSection)
        self.vBoxLayout.addWidget(self.view3DCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.angleSection)
        self.vBoxLayout.addWidget(self.angleCard)

    def __connectSignals(self):
        self.toggleBtn.toggled.connect(self.__onToggleLevel)
        self.calibrateBtn.clicked.connect(self.__onCalibrate)
        signalBus.deviceConnected.connect(self.__onDeviceConnected)
        signalBus.deviceDisconnected.connect(self.__onDeviceDisconnected)

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
        self.calibrateBtn.setEnabled(False)
        if self._active:
            self.__stopLevel()

    def __onToggleLevel(self, checked: bool):
        if checked:
            self.__startLevel()
        else:
            self.__stopLevel()

    def __startLevel(self):
        client = _get_shared_client()
        if client is None:
            InfoBar.warning(
                '未连接设备',
                '请先连接戒指再开启水平仪',
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

        self._collector.set_client(client, loop_thread)
        self._collector.start()

    def __stopLevel(self):
        self._collector.stop_collecting()

    def __onCollectorStarted(self, start_info):
        self._active = True
        self._accelRangeG = start_info.accel_range_g
        self.calibrateBtn.setEnabled(True)
        self.statusLabel.setText('水平仪运行中')
        self.statusLabel.setProperty('active', True)
        self.statusIcon.setIcon(FIF.PAUSE)
        self.toggleBtn.setText('关闭水平仪')

        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)

        InfoBar.success(
            '水平仪已开启',
            '开始接收加速度数据；建议先点击「校准水平」',
            parent=self.window(), duration=3000,
            position=InfoBarPosition.TOP_RIGHT
        )

    def __onCollectorStopped(self):
        self._active = False
        self.statusLabel.setText('水平仪未开启')
        self.statusLabel.setProperty('active', False)
        self.statusIcon.setIcon(FIF.ROTATE)
        self.toggleBtn.setText('开启水平仪')
        self.toggleBtn.setChecked(False)

        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)

    def __onCollectorError(self, message: str):
        InfoBar.error(
            '水平仪出错',
            message,
            parent=self.window(), duration=3000,
            position=InfoBarPosition.TOP_RIGHT
        )
        self.toggleBtn.setChecked(False)
        self.__onCollectorStopped()

    def __onCalibrate(self):
        """Set the current accelerometer direction as the 'flat' reference."""
        if self._latestAccel is None:
            InfoBar.warning(
                '暂无数据',
                '请先开启水平仪并等待数据到达',
                parent=self.window(), duration=2000,
                position=InfoBarPosition.TOP_RIGHT
            )
            return

        norm = np.linalg.norm(self._latestAccel)
        if norm < 0.1:
            InfoBar.warning(
                '数据无效',
                '加速度太小，无法校准',
                parent=self.window(), duration=2000,
                position=InfoBarPosition.TOP_RIGHT
            )
            return

        self._flatRef = self._latestAccel / norm
        InfoBar.success(
            '校准完成',
            '已将当前方向设为水平参考',
            parent=self.window(), duration=2000,
            position=InfoBarPosition.TOP_RIGHT
        )

    def __onBatchReceived(self, batch):
        if not batch.samples:
            return

        sample = batch.samples[-1]
        ax, ay, az = sample.accel_x, sample.accel_y, sample.accel_z

        if self._accelRangeG is not None:
            scale = self._accelRangeG * self._GRAVITY / 32768.0
            ax *= scale
            ay *= scale
            az *= scale

        self._latestAccel = np.array([ax, ay, az], dtype=np.float32)
        norm = np.linalg.norm(self._latestAccel)
        if norm < 0.1:
            return

        cur = self._latestAccel / norm

        if self._flatRef is None:
            # Default: assume Z is up when flat
            self._flatRef = np.array([0.0, 0.0, 1.0], dtype=np.float32)

        # Total tilt angle between current vector and flat reference
        cos_angle = float(np.clip(np.dot(self._flatRef, cur), -1.0, 1.0))
        resultant = math.degrees(math.acos(cos_angle))

        # Rotation axis and angle to go from flat reference to current vector
        cross = np.cross(self._flatRef, cur)
        cross_norm = np.linalg.norm(cross)

        if cross_norm < 1e-6:
            # Vectors are parallel or anti-parallel
            axis = np.array([1.0, 0.0, 0.0])
        else:
            axis = cross / cross_norm

        self._updateAngleLabels(resultant, axis, cur)
        self._update3DView(axis, resultant)

    def _updateAngleLabels(self, resultant: float, axis: np.ndarray, cur: np.ndarray):
        # Project current vector onto reference XY plane for component angles
        pitch = math.degrees(math.atan2(cur[0], cur[2]))
        roll = math.degrees(math.atan2(cur[1], cur[2]))

        self.pitchLabel.findChild(StrongBodyLabel).setText(f'{pitch:+.1f}°')
        self.rollLabel.findChild(StrongBodyLabel).setText(f'{roll:+.1f}°')
        self.resultantLabel.findChild(StrongBodyLabel).setText(f'{resultant:.1f}°')

    def _update3DView(self, axis: np.ndarray, angle: float):
        # Rotate model from flat reference to current orientation
        self.ringMesh.resetTransform()
        if angle > 0.01:
            self.ringMesh.rotate(angle, float(axis[0]), float(axis[1]), float(axis[2]))

        # Move bubble to show tilt direction (projected onto ring plane)
        bubble_scale = min(0.7, math.sin(math.radians(angle)))
        bubble_x = float(axis[0]) * bubble_scale
        bubble_y = float(axis[1]) * bubble_scale
        self.bubble.resetTransform()
        self.bubble.translate(bubble_x, bubble_y, 0.0)

    def closeEvent(self, event):
        if self._active:
            self.__stopLevel()
            self._collector.wait(3000)
        event.accept()
