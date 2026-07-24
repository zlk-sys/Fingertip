# coding: utf-8
import asyncio
import threading
import time

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (ScrollArea, ExpandLayout, FluentIcon, setFont,
                            PushButton, PrimaryPushButton,
                            StrongBodyLabel, BodyLabel, TitleLabel, CaptionLabel,
                            InfoBar, InfoBarPosition, isDarkTheme,
                            IndeterminateProgressBar,
                            SubtitleLabel, IconWidget,
                            SimpleCardWidget, GroupHeaderCardWidget,
                            HyperlinkLabel, SearchLineEdit, PipsPager)
from qfluentwidgets import FluentIcon as FIF

from ..common.style_sheet import StyleSheet
from ..common.signal_bus import signalBus
from ..sdk import ring_sound as sdk

# Shared client reference — set by ConnectInterface after successful connection.
# Imported by MeetingInterface to register packet handlers for ring button events.
shared_client = None

# Shared persistent event loop thread — used by sensor interface and other pages.
async_loop_thread = None


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
    """Continuous scan thread: runs short scan cycles and emits devices in real-time.

    Note: On Windows the bleak/winrt scanner needs a thread with a COM apartment
    that supports BluetoothLEAdvertisementWatcher. We therefore run each scan
    cycle with asyncio.run() in this dedicated QThread rather than reusing the
    persistent AsyncLoopThread.
    """
    devicesUpdated = pyqtSignal(list)  # full accumulated list
    scanFinished = pyqtSignal(list)    # final list
    error = pyqtSignal(str)

    CYCLE_S = 5.0       # each scan cycle duration
    MAX_S = 60.0        # max total scan time

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        try:
            seen = {}  # address -> BleDeviceInfo
            start = time.monotonic()

            while not self._stop_flag and (time.monotonic() - start) < self.MAX_S:
                # Use a dedicated loop per cycle and allow pending
                # bleak/WinRT callbacks to drain before closing, which
                # avoids 'Event loop is closed' tracebacks on Windows.
                _loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_loop)
                try:
                    devices = _loop.run_until_complete(
                        sdk.scan_rings(timeout_s=self.CYCLE_S))
                    # Let any pending WinRT callbacks fire while the
                    # loop is still alive.
                    _loop.run_until_complete(asyncio.sleep(0.2))
                except Exception:
                    devices = []
                finally:
                    # Drain remaining callbacks silently
                    for handle in list(_loop._ready):
                        handle.cancel()
                    _loop.run_until_complete(asyncio.sleep(0))
                    _loop.close()

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


# ── Main Interface ──────────────────────────────────────────────

class ConnectInterface(ScrollArea):
    """Connect ring interface."""

    PAGE_SIZE = 10

    # Signal to marshal unexpected-disconnect callback to the main thread
    _unexpectedDisconnectSignal = pyqtSignal()

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

        # Pagination
        self.pipsPager = PipsPager(self.view)
        self.pipsPager.setVisibleNumber(5)
        self.pipsPager.setVisible(False)

        # State
        self._client = None
        self._threads = []
        self._allDevices = []
        self._displayDevices = []
        self._currentPage = 0
        self._scanThread = None
        self._pendingConnect = None   # (address, name) waiting for scan to stop
        self._deviceName = None
        self._deviceAddress = None
        self._asyncLoopThread = AsyncLoopThread(self)
        self._asyncLoopThread.start()

        # Reconnection state
        self._userDisconnecting = False   # True when user clicks disconnect
        self._reconnecting = False        # True during auto-reconnect attempts
        self._reconnectAttempts = 0
        self._maxReconnectAttempts = 3
        self._lastAddress = None          # saved for reconnection
        self._lastName = None             # saved for reconnection

        # Unresponsive device tracking
        self._consecutiveFailures = 0
        self._maxConsecutiveFailures = 3

        global async_loop_thread
        async_loop_thread = self._asyncLoopThread

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
        self.vBoxLayout.addWidget(self.pipsPager, 0, Qt.AlignHCenter)

        # signals
        self.scanBtn.clicked.connect(self.__onScan)
        self.disconnectBtn.clicked.connect(self.__onDisconnect)
        self.macFilterEdit.textChanged.connect(self.__onFilterChanged)
        self.pipsPager.currentIndexChanged.connect(self.__onPageChanged)
        signalBus.refreshSystemInfoRequested.connect(self.__onRefreshSystemInfo)
        self._unexpectedDisconnectSignal.connect(self.__onUnexpectedDisconnect)

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
        self._displayDevices = []
        self._currentPage = 0
        self.pipsPager.setVisible(False)
        self.macFilterEdit.setVisible(False)
        self.emptyLabel.setText('正在扫描...')
        self.emptyLabel.setVisible(True)
        self.deviceListView.addWidget(self.emptyLabel)
        self.deviceCountLabel.setVisible(False)

        self._scanThread = ScanThread(parent=self)
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

        # A connect request was queued while waiting for the scan to stop:
        # start it now that the adapter is free (scan/connect concurrency
        # on Windows hurts connection success rate)
        if self._pendingConnect is not None:
            address, name = self._pendingConnect
            self._pendingConnect = None
            self.__startConnect(address, name)

    def __renderDeviceList(self, devices):
        """Sort devices, update pager and render the current page."""
        if not devices:
            self.__clearDeviceList()
            self.emptyLabel.setText('没有匹配的设备')
            self.emptyLabel.setVisible(True)
            self.deviceListView.addWidget(self.emptyLabel)
            self.deviceCountLabel.setText(
                f'共搜索到 {len(self._allDevices)} 个设备，当前显示 0 个')
            self.deviceCountLabel.setVisible(True)
            self._displayDevices = []
            self.pipsPager.setVisible(False)
            return

        self.emptyLabel.setVisible(False)

        # Sort: devices with 'ring' in name first, then by name
        def is_ring_device(dev):
            name = dev.name or ''
            return 'ring' in name.lower()

        self._displayDevices = sorted(
            devices, key=lambda d: (not is_ring_device(d), d.name or ''))

        total = len(self._displayDevices)
        page_count = (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE

        self.deviceCountLabel.setText(
            f'共搜索到 {len(self._allDevices)} 个设备，当前第 {self._currentPage + 1} 页，共 {page_count} 页')
        self.deviceCountLabel.setVisible(True)

        # Update pager (keep current page if still valid)
        self.pipsPager.blockSignals(True)
        self.pipsPager.setPageNumber(page_count)
        if self._currentPage >= page_count:
            self._currentPage = 0
        self.pipsPager.setCurrentIndex(self._currentPage)
        self.pipsPager.blockSignals(False)
        self.pipsPager.setVisible(page_count > 1)

        self.__renderCurrentPage()

    def __renderCurrentPage(self):
        """Render the device cards for the current page."""
        self.__clearDeviceList()
        start = self._currentPage * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        for dev in self._displayDevices[start:end]:
            card = DeviceCard(dev.name, dev.address, dev.rssi, self.view)
            card.connectClicked.connect(self.__onConnectDevice)
            self.deviceListView.addWidget(card)

    def __onPageChanged(self, index):
        self._currentPage = index
        total = len(self._displayDevices)
        page_count = (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE
        self.deviceCountLabel.setText(
            f'共搜索到 {len(self._allDevices)} 个设备，当前第 {index + 1} 页，共 {page_count} 页')
        self.__renderCurrentPage()

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
        # Stop the continuous scan first: on Windows, scanning and
        # connecting at the same time significantly lowers the connection
        # success rate. Queue the request and connect once the scan stops.
        if self._scanThread and self._scanThread.isRunning():
            self._pendingConnect = (address, name)
            self._scanThread.stop()
            self.scanBtn.setEnabled(False)
            self.progressBar.setVisible(True)
            self.progressBar.start()
            self.__setStatus(f'正在停止扫描，随后连接 {name}...')
            return

        self.__startConnect(address, name)

    def __startConnect(self, address, name):
        # Prevent duplicate connections
        if self._client is not None:
            InfoBar.warning('已连接设备', '请先断开当前设备再连接新设备',
                            parent=self.window(), duration=3000,
                            position=InfoBarPosition.TOP_RIGHT)
            return

        self._deviceAddress = address
        self._deviceName = name
        self._lastAddress = address
        self._lastName = name
        self.scanBtn.setEnabled(False)
        self.progressBar.setVisible(True)
        self.progressBar.start()
        self.__setStatus(f'正在连接 {name}...')

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
        self._reconnecting = False
        self._reconnectAttempts = 0
        self._consecutiveFailures = 0
        self.__setStatus('已连接', 'success')
        self.disconnectBtn.setEnabled(True)
        self.scanBtn.setEnabled(True)

        # Share client globally for meeting mode
        global shared_client
        shared_client = client

        # Register unexpected disconnect callback for auto-reconnect
        client.on_unexpected_disconnect = self._onUnexpectedDisconnectFromThread

        InfoBar.success('连接成功', '戒指已连接',
                        parent=self.window(), duration=2000,
                        position=InfoBarPosition.TOP_RIGHT)
        signalBus.deviceConnected.emit(self._deviceName or '未知设备', self._deviceAddress or '')

        # Wait 500ms before fetching system info to let BLE stack settle
        QTimer.singleShot(500, self.__fetchSystemInfo)

    def __onConnectError(self, error_msg):
        self.progressBar.stop()
        self.progressBar.setVisible(False)
        self.__setStatus('连接失败', 'error')
        self.scanBtn.setEnabled(True)
        InfoBar.error('连接失败', error_msg,
                      parent=self.window(), duration=4000,
                      position=InfoBarPosition.TOP_RIGHT)

    # ── Disconnect ──────────────────────────────────────────────

    def __onDisconnect(self):
        if self._client is None:
            return
        self._userDisconnecting = True  # Mark as user-initiated disconnect
        self.disconnectBtn.setEnabled(False)
        self.progressBar.setVisible(True)
        self.progressBar.start()
        self.__setStatus('正在断开...')

        thread = DisconnectThread(self._client, self._asyncLoopThread, parent=self)
        thread.disconnected.connect(self.__onDisconnected)
        thread.error.connect(self.__onDisconnectError)
        thread.finished.connect(lambda: self.__threadDone(thread))
        thread.start()
        self._threads.append(thread)

    def __onDisconnected(self):
        self.progressBar.stop()
        self.progressBar.setVisible(False)
        self._userDisconnecting = False  # Reset flag
        self._client = None
        self._deviceName = None
        self._deviceAddress = None
        self.__setStatus('未连接')
        self.disconnectBtn.setEnabled(False)

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
        self.__setStatus('断开失败', 'error')
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
        self._consecutiveFailures = 0  # Reset failure counter on success
        signalBus.systemInfoReceived.emit(info)

    def __onSystemInfoError(self, error_msg):
        self._consecutiveFailures += 1
        if self._consecutiveFailures >= self._maxConsecutiveFailures:
            # Device may be unresponsive, trigger reconnect
            InfoBar.warning('设备无响应', f'连续 {self._consecutiveFailures} 次命令失败，正在重连...',
                            parent=self.window(), duration=3000,
                            position=InfoBarPosition.TOP_RIGHT)
            self._userDisconnecting = False  # Treat as unexpected disconnect
            self.__onDisconnect()  # Proactively disconnect
        else:
            InfoBar.warning('获取信息失败', error_msg,
                            parent=self.window(), duration=3000,
                            position=InfoBarPosition.TOP_RIGHT)

    # ── Auto-reconnect (exponential backoff) ───────────────────

    def _onUnexpectedDisconnectFromThread(self):
        """Thread-safe wrapper: called from BLE async thread, emits signal to main thread."""
        self._unexpectedDisconnectSignal.emit()

    def __onUnexpectedDisconnect(self):
        """Main-thread handler: connection dropped unexpectedly (not user-initiated)."""
        if self._userDisconnecting or self._reconnecting:
            return

        self._reconnecting = True
        self._reconnectAttempts = 0
        self.__tryReconnect()

    def __tryReconnect(self):
        """Attempt reconnection with exponential backoff (1s, 2s, 4s)."""
        if self._reconnectAttempts >= self._maxReconnectAttempts:
            self._reconnecting = False
            self.__onFullyDisconnected()
            InfoBar.error('重连失败', f'尝试 {self._maxReconnectAttempts} 次后仍无法重连',
                          parent=self.window(), duration=5000,
                          position=InfoBarPosition.TOP_RIGHT)
            return

        delay = 2 ** self._reconnectAttempts  # 1s, 2s, 4s
        self._reconnectAttempts += 1

        self.__setStatus(f'连接已断开，{delay}秒后尝试重连 ({self._reconnectAttempts}/{self._maxReconnectAttempts})...')

        # Use QTimer for delayed reconnection
        QTimer.singleShot(delay * 1000, self.__doReconnect)

    def __doReconnect(self):
        """Execute the reconnection attempt."""
        if not self._lastAddress:
            self._reconnecting = False
            return

        self.__setStatus('正在重连...')
        thread = ConnectThread(self._lastAddress, self._asyncLoopThread, parent=self)
        thread.connected.connect(self.__onReconnectSuccess)
        thread.error.connect(self.__onReconnectFail)
        thread.finished.connect(lambda: self.__threadDone(thread))
        thread.start()
        self._threads.append(thread)

    def __onReconnectSuccess(self, client):
        """Reconnection succeeded."""
        self._reconnecting = False
        self._reconnectAttempts = 0
        self._consecutiveFailures = 0
        self._client = client
        global shared_client
        shared_client = client

        # Re-register unexpected disconnect callback
        client.on_unexpected_disconnect = self._onUnexpectedDisconnectFromThread

        self.__setStatus('已连接（重连成功）', 'success')
        self.disconnectBtn.setEnabled(True)
        signalBus.deviceConnected.emit(self._lastName or '未知设备', self._lastAddress or '')

        # Wait 500ms before fetching system info
        QTimer.singleShot(500, self.__fetchSystemInfo)

        InfoBar.success('重连成功', '戒指已重新连接',
                        parent=self.window(), duration=2000,
                        position=InfoBarPosition.TOP_RIGHT)

    def __onReconnectFail(self, error_msg):
        """Reconnection failed, continue with exponential backoff."""
        self.__tryReconnect()  # Recursive call; __tryReconnect checks attempt limit

    def __onFullyDisconnected(self):
        """Fully disconnected (reconnect exhausted or abandoned)."""
        self._reconnecting = False
        self._client = None
        global shared_client
        shared_client = None
        self.__setStatus('未连接')
        self.disconnectBtn.setEnabled(False)
        signalBus.deviceDisconnected.emit()

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

    def __setStatus(self, text, state=None):
        """Update status label text and color.
        state: None (default), 'success' (green), 'error' (red).
        """
        self.statusLabel.setText(text)
        if state == 'success':
            self.statusLabel.setTextColor(QColor(0, 180, 42), QColor(0, 180, 42))
        elif state == 'error':
            self.statusLabel.setTextColor(QColor(207, 19, 34), QColor(255, 120, 117))
        else:
            self.statusLabel.setTextColor()
