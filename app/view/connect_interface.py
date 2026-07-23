# coding: utf-8
import asyncio
import threading
import time

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (ScrollArea, ExpandLayout, FluentIcon, setFont,
                            PushButton, PrimaryPushButton,
                            StrongBodyLabel, BodyLabel, TitleLabel, CaptionLabel,
                            InfoBar, InfoBarPosition, isDarkTheme,
                            IndeterminateProgressBar,
                            SubtitleLabel, IconWidget,
                            SimpleCardWidget, HeaderCardWidget, GroupHeaderCardWidget,
                            HyperlinkLabel, SearchLineEdit)
from qfluentwidgets import FluentIcon as FIF

from ..common.style_sheet import StyleSheet
from ..common.signal_bus import signalBus
from ..sdk import ring_sound as sdk

# Shared client reference — set by ConnectInterface after successful connection.
# Imported by MeetingInterface to register packet handlers for ring button events.
shared_client = None


# ── Persistent async loop thread ─────────────────────────────────

class AsyncLoopThread(QThread):
    """Background thread that keeps one asyncio event loop alive.

    bleak callbacks capture the event loop that created the client. Reusing
    a single loop avoids 'Event loop is closed' errors on refresh/disconnect.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop = None
        self._ready = threading.Event()
        self._running = True

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            if self._loop and not self._loop.is_closed():
                tasks = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                for task in tasks:
                    task.cancel()
                if tasks:
                    self._loop.run_until_complete(
                        asyncio.gather(*tasks, return_exceptions=True))
                self._loop.close()

    def stop(self):
        self._running = False
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def run_coro(self, coro, timeout=None):
        if not self._running:
            raise RuntimeError('AsyncLoopThread is not running')
        self._ready.wait()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout)


# ── BLE Worker Threads ───────────────────────────────────────────

class ScanThread(QThread):
    """Continuous scan thread: runs short scan cycles and emits devices in real-time."""
    devicesUpdated = pyqtSignal(list)  # full accumulated list
    scanFinished = pyqtSignal(list)    # final list
    error = pyqtSignal(str)

    CYCLE_S = 5.0       # each scan cycle duration
    MAX_S = 60.0        # max total scan time

    def __init__(self, loop_thread, parent=None):
        super().__init__(parent)
        self._loop_thread = loop_thread
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        try:
            seen = {}  # address -> BleDeviceInfo
            start = time.monotonic()

            while not self._stop_flag and (time.monotonic() - start) < self.MAX_S:
                devices = self._loop_thread.run_coro(
                    sdk.scan_rings(timeout_s=self.CYCLE_S), timeout=self.CYCLE_S + 5)
                if self._stop_flag:
                    break

                for dev in devices:
                    key = dev.address.lower()
                    if key not in seen or (dev.rssi is not None and dev.rssi > (seen[key].rssi or -999)):
                        seen[key] = dev

                self.devicesUpdated.emit(list(seen.values()))

            self.scanFinished.emit(list(seen.values()))
        except Exception as e:
            self.error.emit(str(e))


class ConnectThread(QThread):
    """Thread for connecting to a BLE device."""
    connected = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, address, loop_thread, parent=None):
        super().__init__(parent)
        self.address = address
        self._loop_thread = loop_thread

    def run(self):
        try:
            client = self._loop_thread.run_coro(
                sdk.connect_ring(address=self.address, auto_time_sync=True), timeout=60)
            self.connected.emit(client)
        except Exception as e:
            self.error.emit(str(e))


class GetInfoThread(QThread):
    """Thread for getting system info."""
    infoReceived = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, client, loop_thread, timeout_s=None, parent=None):
        super().__init__(parent)
        self.client = client
        self._loop_thread = loop_thread
        self.timeout_s = timeout_s

    def run(self):
        try:
            coro = sdk.get_system_info(self.client)
            if self.timeout_s:
                coro = asyncio.wait_for(coro, timeout=self.timeout_s)
            info = self._loop_thread.run_coro(coro, timeout=self.timeout_s or 30)
            self.infoReceived.emit(info)
        except Exception as e:
            self.error.emit(str(e))


class DisconnectThread(QThread):
    """Thread for disconnecting."""
    disconnected = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, client, loop_thread, parent=None):
        super().__init__(parent)
        self.client = client
        self._loop_thread = loop_thread

    def run(self):
        try:
            self._loop_thread.run_coro(self.client.disconnect(), timeout=20)
            self.disconnected.emit()
        except Exception as e:
            self.error.emit(str(e))


# ── UI Widgets ──────────────────────────────────────────────────

class DeviceCard(SimpleCardWidget):
    """Card widget for a discovered BLE device."""
    connectClicked = pyqtSignal(str, str)

    def __init__(self, name, address, rssi, parent=None):
        super().__init__(parent)
        self.address = address
        self.name = name or '未知设备'

        # self.iconWidget = IconWidget(FIF.TILES, self)
        self.nameLabel = BodyLabel(self.name, self)
        self.addressLabel = CaptionLabel(address, self)
        self.rssiLabel = CaptionLabel(f'RSSI: {rssi} dBm', self)
        self.connectBtn = PrimaryPushButton('连接', self)

        self.hBoxLayout = QHBoxLayout(self)
        self.infoLayout = QVBoxLayout()

        self.__initWidget()

    def __initWidget(self):
        self.setBorderRadius(8)
        self.setFixedHeight(73)
        # self.iconWidget.setFixedSize(40, 40)
        self.connectBtn.setFixedWidth(80)
        self.addressLabel.setTextColor(QColor(96, 96, 96), QColor(206, 206, 206))
        self.rssiLabel.setTextColor(QColor(96, 96, 96), QColor(206, 206, 206))

        self.infoLayout.setContentsMargins(0, 0, 0, 0)
        self.infoLayout.setSpacing(2)
        self.infoLayout.addWidget(self.nameLabel, 0, Qt.AlignVCenter)
        self.infoLayout.addWidget(self.addressLabel, 0, Qt.AlignVCenter)

        self.hBoxLayout.setContentsMargins(20, 11, 11, 11)
        self.hBoxLayout.setSpacing(15)
        # self.hBoxLayout.addWidget(self.iconWidget)
        self.hBoxLayout.addLayout(self.infoLayout)
        self.hBoxLayout.addStretch(1)
        self.hBoxLayout.addWidget(self.rssiLabel, 0, Qt.AlignRight | Qt.AlignVCenter)
        self.hBoxLayout.addWidget(self.connectBtn, 0, Qt.AlignRight | Qt.AlignVCenter)

        self.connectBtn.clicked.connect(
            lambda: self.connectClicked.emit(self.address, self.name))


class DeviceInfoCard(HeaderCardWidget):
    """Card showing connected device info."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle('设备信息')
        self.setBorderRadius(8)

        self.firmwareLabel = BodyLabel('固件版本: --', self)
        self.batteryLabel = BodyLabel('电量: --', self)
        self.modelLabel = BodyLabel('型号: --', self)
        self.snLabel = BodyLabel('序列号: --', self)
        self.storageLabel = BodyLabel('存储: --', self)
        self.chargingLabel = BodyLabel('充电状态: --', self)

        self.vBoxLayout = QVBoxLayout()
        self.vBoxLayout.setSpacing(8)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.addWidget(self.firmwareLabel)
        self.vBoxLayout.addWidget(self.batteryLabel)
        self.vBoxLayout.addWidget(self.modelLabel)
        self.vBoxLayout.addWidget(self.snLabel)
        self.vBoxLayout.addWidget(self.storageLabel)
        self.vBoxLayout.addWidget(self.chargingLabel)

        self.viewLayout.addLayout(self.vBoxLayout)

    def updateInfo(self, info):
        self.firmwareLabel.setText(f'固件版本: {info.firmware_version}')
        self.batteryLabel.setText(f'电量: {info.battery_percent}%')
        self.modelLabel.setText(f'型号: {info.model}')
        self.snLabel.setText(f'序列号: {info.sn}')
        total_kb = info.audio_storage_total // 1024
        avail_kb = info.audio_storage_available // 1024
        self.storageLabel.setText(f'存储: {avail_kb}KB / {total_kb}KB')
        self.chargingLabel.setText(
            f'充电状态: {"充电中" if info.battery_charging else "未充电"}')

    def reset(self):
        self.firmwareLabel.setText('固件版本: --')
        self.batteryLabel.setText('电量: --')
        self.modelLabel.setText('型号: --')
        self.snLabel.setText('序列号: --')
        self.storageLabel.setText('存储: --')
        self.chargingLabel.setText('充电状态: --')


# ── Main Interface ──────────────────────────────────────────────

class ConnectInterface(ScrollArea):
    """Connect ring interface."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        # Title
        self.titleLabel = TitleLabel('连接戒指', self.view)
        self.subtitleLabel = BodyLabel('通过蓝牙连接智能戒指设备', self.view)

        # Progress bar
        self.progressBar = IndeterminateProgressBar(self.view)
        self.progressBar.setVisible(False)
        self.progressBar.setFixedHeight(4)

        # Action card
        self.actionCard = GroupHeaderCardWidget(self.view)
        self.actionCard.setTitle('蓝牙连接')
        self.actionCard.setBorderRadius(8)

        self.scanBtn = PrimaryPushButton('扫描戒指', self)
        self.disconnectBtn = PushButton('断开连接', self)
        self.statusLabel = BodyLabel('未连接', self)

        self.scanBtn.setFixedWidth(120)
        self.disconnectBtn.setFixedWidth(120)
        self.disconnectBtn.setEnabled(False)

        self._actionWidget = QWidget(self)
        self._actionLayout = QHBoxLayout(self._actionWidget)
        self._actionLayout.setContentsMargins(0, 0, 0, 0)
        self._actionLayout.setSpacing(12)
        self._actionLayout.addWidget(self.statusLabel)
        self._actionLayout.addStretch(1)
        self._actionLayout.addWidget(self.disconnectBtn)
        self._actionLayout.addWidget(self.scanBtn)

        self.actionCard.addGroup(
            FIF.CONNECT, '设备扫描', '搜索附近的智能戒指设备', self._actionWidget)

        # Device list
        self.deviceListLabel = SubtitleLabel('附近设备', self.view)
        self.macFilterEdit = SearchLineEdit(self.view)
        self.macFilterEdit.setPlaceholderText('输入 MAC 地址过滤，如 AA:BB 或 aabb...')
        self.macFilterEdit.setFixedWidth(320)
        self.macFilterEdit.setVisible(False)
        self.deviceCountLabel = CaptionLabel('', self.view)
        self.deviceCountLabel.setVisible(False)
        self.deviceListView = QVBoxLayout()
        self.deviceListView.setSpacing(8)

        self.emptyLabel = CaptionLabel('点击「扫描戒指」搜索附近设备', self.view)
        self.emptyLabel.setAlignment(Qt.AlignCenter)
        self.deviceListView.addWidget(self.emptyLabel)

        # Device info card
        self.deviceInfoCard = DeviceInfoCard(self.view)

        # State
        self._client = None
        self._threads = []
        self._allDevices = []
        self._scanThread = None
        self._deviceName = None
        self._deviceAddress = None
        self._asyncLoopThread = AsyncLoopThread(self)
        self._asyncLoopThread.start()

        self.__initWidget()

    def __initWidget(self):
        self.setObjectName('connectInterface')
        self.view.setObjectName('view')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        # apply style sheet
        StyleSheet.CONNECT_INTERFACE.apply(self)

        # layout
        self.vBoxLayout.setContentsMargins(36, 24, 36, 36)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addWidget(self.progressBar)
        self.vBoxLayout.addWidget(self.actionCard, 0, Qt.AlignTop)
        self.vBoxLayout.addWidget(self.deviceListLabel)
        self.vBoxLayout.addWidget(self.macFilterEdit)
        self.vBoxLayout.addWidget(self.deviceCountLabel)
        self.vBoxLayout.addLayout(self.deviceListView)
        self.vBoxLayout.addWidget(self.deviceInfoCard, 0, Qt.AlignTop)

        # signals
        self.scanBtn.clicked.connect(self.__onScan)
        self.disconnectBtn.clicked.connect(self.__onDisconnect)
        self.macFilterEdit.textChanged.connect(self.__onFilterChanged)
        signalBus.refreshSystemInfoRequested.connect(self.__onRefreshSystemInfo)

        # cleanup
        loop_thread = self._asyncLoopThread
        self.destroyed.connect(lambda: loop_thread.stop())

    # ── Scan ────────────────────────────────────────────────────

    def __onScan(self):
        """Toggle scan on/off."""
        if self._scanThread and self._scanThread.isRunning():
            self._scanThread.stop()
            self.scanBtn.setText('扫描戒指')
            return

        self.scanBtn.setText('停止扫描')
        self.progressBar.setVisible(True)
        self.progressBar.start()
        self.__clearDeviceList()
        self._allDevices = []
        self.macFilterEdit.setVisible(False)
        self.emptyLabel.setText('正在扫描...')
        self.emptyLabel.setVisible(True)
        self.deviceListView.addWidget(self.emptyLabel)
        self.deviceCountLabel.setVisible(False)

        self._scanThread = ScanThread(self._asyncLoopThread, parent=self)
        self._scanThread.devicesUpdated.connect(self.__onDevicesUpdated)
        self._scanThread.scanFinished.connect(self.__onScanFinished)
        self._scanThread.error.connect(self.__onScanError)
        self._scanThread.finished.connect(self.__onScanThreadDone)
        self._scanThread.start()

    def __onDevicesUpdated(self, devices):
        """Real-time update during continuous scan."""
        self._allDevices = list(devices)
        self.emptyLabel.setVisible(False)
        self.macFilterEdit.setVisible(True)
        self.__renderDeviceList(devices)

    def __onScanFinished(self, devices):
        """Called when scan reaches end (timeout or stopped)."""
        self.progressBar.stop()
        self.progressBar.setVisible(False)
        self.scanBtn.setText('扫描戒指')
        self._allDevices = list(devices)

        if not devices:
            self.macFilterEdit.setVisible(False)
            self.__clearDeviceList()
            self.emptyLabel.setText('未发现戒指设备，请确保戒指已开机且在附近')
            self.emptyLabel.setVisible(True)
            self.deviceListView.addWidget(self.emptyLabel)
            self.deviceCountLabel.setVisible(False)
            return

        self.macFilterEdit.setVisible(True)
        self.__renderDeviceList(devices)

    def __onScanThreadDone(self):
        """Cleanup after scan thread exits."""
        if self._scanThread:
            self.__threadDone(self._scanThread)
            self._scanThread = None
        self.scanBtn.setText('扫描戒指')

    def __renderDeviceList(self, devices):
        """Render device cards from a device list."""
        self.__clearDeviceList()
        self.emptyLabel.setVisible(False)

        if not devices:
            self.emptyLabel.setText('没有匹配的设备')
            self.emptyLabel.setVisible(True)
            self.deviceListView.addWidget(self.emptyLabel)
            self.deviceCountLabel.setText(f'共搜索到 {len(self._allDevices)} 个设备，当前显示 0 个')
            self.deviceCountLabel.setVisible(True)
            return

        # Sort: devices with 'ring' in name first
        def is_ring_device(dev):
            name = dev.name or ''
            return 'ring' in name.lower()

        sorted_devices = sorted(devices, key=lambda d: (not is_ring_device(d), d.name or ''))

        self.deviceCountLabel.setText(
            f'共搜索到 {len(self._allDevices)} 个设备，当前显示 {len(sorted_devices)} 个')
        self.deviceCountLabel.setVisible(True)

        for dev in sorted_devices:
            card = DeviceCard(dev.name, dev.address, dev.rssi, self.view)
            card.connectClicked.connect(self.__onConnectDevice)
            self.deviceListView.addWidget(card)

    def __onFilterChanged(self, text):
        """Filter devices by MAC address (fuzzy match)."""
        if not self._allDevices:
            return

        keyword = text.strip().replace(':', '').replace('-', '').lower()
        if not keyword:
            self.__renderDeviceList(self._allDevices)
            return

        filtered = [
            dev for dev in self._allDevices
            if keyword in dev.address.replace(':', '').replace('-', '').lower()
            or keyword in (dev.name or '').lower()
        ]
        self.__renderDeviceList(filtered)

    def __onScanError(self, error_msg):
        self.progressBar.stop()
        self.progressBar.setVisible(False)
        self.scanBtn.setEnabled(True)
        self.__clearDeviceList()
        self.emptyLabel.setText(f'扫描失败: {error_msg}')
        self.emptyLabel.setVisible(True)
        self.deviceListView.addWidget(self.emptyLabel)
        self.deviceCountLabel.setVisible(False)
        InfoBar.error('扫描失败', error_msg,
                      parent=self.window(), duration=4000,
                      position=InfoBarPosition.TOP_RIGHT)

    # ── Connect ─────────────────────────────────────────────────

    def __onConnectDevice(self, address, name):
        self._deviceAddress = address
        self._deviceName = name
        self.scanBtn.setEnabled(False)
        self.progressBar.setVisible(True)
        self.progressBar.start()
        self.statusLabel.setText(f'正在连接 {name}...')

        thread = ConnectThread(address, self._asyncLoopThread, parent=self)
        thread.connected.connect(self.__onConnected)
        thread.error.connect(self.__onConnectError)
        thread.finished.connect(lambda: self.__threadDone(thread))
        thread.start()
        self._threads.append(thread)

    def __onConnected(self, client):
        self.progressBar.stop()
        self.progressBar.setVisible(False)
        self._client = client
        self.statusLabel.setText('已连接')
        self.disconnectBtn.setEnabled(True)
        self.scanBtn.setEnabled(True)

        # Share client globally for meeting mode
        global shared_client
        shared_client = client

        InfoBar.success('连接成功', '戒指已连接',
                        parent=self.window(), duration=2000,
                        position=InfoBarPosition.TOP_RIGHT)
        signalBus.deviceConnected.emit(self._deviceName or '未知设备', self._deviceAddress or '')
        self.__fetchSystemInfo()

    def __onConnectError(self, error_msg):
        self.progressBar.stop()
        self.progressBar.setVisible(False)
        self.statusLabel.setText('连接失败')
        self.scanBtn.setEnabled(True)
        InfoBar.error('连接失败', error_msg,
                      parent=self.window(), duration=4000,
                      position=InfoBarPosition.TOP_RIGHT)

    # ── Disconnect ──────────────────────────────────────────────

    def __onDisconnect(self):
        if self._client is None:
            return
        self.disconnectBtn.setEnabled(False)
        self.progressBar.setVisible(True)
        self.progressBar.start()
        self.statusLabel.setText('正在断开...')

        thread = DisconnectThread(self._client, self._asyncLoopThread, parent=self)
        thread.disconnected.connect(self.__onDisconnected)
        thread.error.connect(self.__onDisconnectError)
        thread.finished.connect(lambda: self.__threadDone(thread))
        thread.start()
        self._threads.append(thread)

    def __onDisconnected(self):
        self.progressBar.stop()
        self.progressBar.setVisible(False)
        self._client = None
        self._deviceName = None
        self._deviceAddress = None
        self.statusLabel.setText('未连接')
        self.disconnectBtn.setEnabled(False)
        self.deviceInfoCard.reset()

        # Clear shared client
        global shared_client
        shared_client = None

        signalBus.deviceDisconnected.emit()
        InfoBar.info('已断开', '戒指已断开连接',
                     parent=self.window(), duration=2000,
                     position=InfoBarPosition.TOP_RIGHT)

    def __onDisconnectError(self, error_msg):
        self.progressBar.stop()
        self.progressBar.setVisible(False)
        self.statusLabel.setText('断开失败')
        self.disconnectBtn.setEnabled(True)

    # ── System Info ─────────────────────────────────────────────

    def __fetchSystemInfo(self, timeout_s=None):
        if self._client is None:
            return
        thread = GetInfoThread(self._client, self._asyncLoopThread, timeout_s=timeout_s, parent=self)
        thread.infoReceived.connect(self.__onSystemInfo)
        thread.error.connect(self.__onSystemInfoError)
        thread.finished.connect(lambda: self.__threadDone(thread))
        thread.start()
        self._threads.append(thread)

    def __onRefreshSystemInfo(self):
        if self._client is None:
            return
        self.__fetchSystemInfo(timeout_s=60)

    def __onSystemInfo(self, info):
        self.deviceInfoCard.updateInfo(info)
        signalBus.systemInfoReceived.emit(info)

    def __onSystemInfoError(self, error_msg):
        InfoBar.warning('获取信息失败', error_msg,
                        parent=self.window(), duration=3000,
                        position=InfoBarPosition.TOP_RIGHT)

    # ── Helpers ─────────────────────────────────────────────────

    def __clearDeviceList(self):
        while self.deviceListView.count():
            item = self.deviceListView.takeAt(0)
            widget = item.widget()
            if widget and widget is not self.emptyLabel:
                widget.deleteLater()

    def __threadDone(self, thread):
        if thread in self._threads:
            self._threads.remove(thread)
