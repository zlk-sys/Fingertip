# coding: utf-8
from datetime import datetime, timezone

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout

from qfluentwidgets import (ScrollArea, TitleLabel, SubtitleLabel,
                            BodyLabel, StrongBodyLabel, CaptionLabel,
                            SimpleCardWidget, ProgressBar,
                            HyperlinkButton, IconWidget, InfoBar,
                            InfoBarPosition)
from qfluentwidgets import FluentIcon as FIF

from ..common.style_sheet import StyleSheet
from ..common.signal_bus import signalBus


class AlertBanner(SimpleCardWidget):
    """Alert banner for connection/battery warnings."""

    reconnectClicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('alertBanner')
        self.setBorderRadius(12)
        self.setFixedHeight(56)

        self.iconWidget = IconWidget(FIF.INFO, self)
        self.iconWidget.setFixedSize(20, 20)
        self.messageLabel = BodyLabel('未获取到电量，可能连接异常', self)
        self.reconnectBtn = HyperlinkButton('', '重新连接', self)
        self.reconnectBtn.setObjectName('reconnectBtn')

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(16, 0, 16, 0)
        self.hBoxLayout.setSpacing(10)
        self.hBoxLayout.addWidget(self.iconWidget)
        self.hBoxLayout.addWidget(self.messageLabel)
        self.hBoxLayout.addStretch(1)
        self.hBoxLayout.addWidget(self.reconnectBtn)

        self.reconnectBtn.clicked.connect(self.reconnectClicked.emit)

    def setConnected(self, connected):
        if connected:
            self.setVisible(False)
        else:
            self.setVisible(True)
            self.messageLabel.setText('未连接设备，请前往「连接戒指」页面连接')
            self.reconnectBtn.setVisible(False)

    def setBatteryMissing(self):
        self.setVisible(True)
        self.messageLabel.setText('未获取到电量，可能连接异常')
        self.reconnectBtn.setVisible(True)


class ProgressCard(SimpleCardWidget):
    """Big card with a value and a progress bar."""

    def __init__(self, icon, title, value_text, unit_text, percent, parent=None):
        super().__init__(parent)
        self.setBorderRadius(12)
        self.setFixedHeight(140)

        self.iconWidget = IconWidget(icon, self)
        self.iconWidget.setFixedSize(20, 20)
        self.titleLabel = CaptionLabel(title, self)
        self.titleLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.valueLabel = StrongBodyLabel(value_text, self)
        self.valueLabel.setFont(QFont(self.valueLabel.font().family(), 28, QFont.Bold))
        self.unitLabel = BodyLabel(unit_text, self)
        self.unitLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.progressBar = ProgressBar(self)
        self.progressBar.setValue(int(percent))
        self.progressBar.setTextVisible(False)
        self.progressBar.setFixedHeight(6)

        self.headerLayout = QHBoxLayout()
        self.headerLayout.setSpacing(8)
        self.headerLayout.addWidget(self.iconWidget)
        self.headerLayout.addWidget(self.titleLabel)
        self.headerLayout.addStretch(1)

        self.valueLayout = QHBoxLayout()
        self.valueLayout.setSpacing(6)
        self.valueLayout.setAlignment(Qt.AlignBottom)
        self.valueLayout.addWidget(self.valueLabel)
        self.valueLayout.addWidget(self.unitLabel)
        self.valueLayout.addStretch(1)

        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(20, 16, 20, 16)
        self.vBoxLayout.setSpacing(10)
        self.vBoxLayout.addLayout(self.headerLayout)
        self.vBoxLayout.addStretch(1)
        self.vBoxLayout.addLayout(self.valueLayout)
        self.vBoxLayout.addWidget(self.progressBar)

    def setValue(self, value_text, unit_text, percent):
        self.valueLabel.setText(value_text)
        self.unitLabel.setText(unit_text)
        self.progressBar.setValue(int(percent))


class StatCard(SimpleCardWidget):
    """Small stat card with icon, value and caption."""

    def __init__(self, icon, value, caption, parent=None):
        super().__init__(parent)
        self.setBorderRadius(12)
        self.setFixedHeight(120)

        self.iconWidget = IconWidget(icon, self)
        self.iconWidget.setFixedSize(20, 20)

        self.valueLabel = StrongBodyLabel(str(value), self)
        self.valueLabel.setFont(QFont(self.valueLabel.font().family(), 28, QFont.Bold))
        self.captionLabel = CaptionLabel(caption, self)
        self.captionLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.hBoxLayout = QHBoxLayout()
        self.hBoxLayout.setSpacing(8)
        self.hBoxLayout.addWidget(self.iconWidget)
        self.hBoxLayout.addStretch(1)

        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(20, 16, 20, 16)
        self.vBoxLayout.setSpacing(6)
        self.vBoxLayout.addLayout(self.hBoxLayout)
        self.vBoxLayout.addStretch(1)
        self.vBoxLayout.addWidget(self.valueLabel)
        self.vBoxLayout.addWidget(self.captionLabel)

    def setValue(self, value):
        self.valueLabel.setText(str(value))


class DetailItemCard(SimpleCardWidget):
    """Card for a single detail key-value pair."""

    def __init__(self, value, caption, parent=None):
        super().__init__(parent)
        self.setBorderRadius(10)
        self.setFixedHeight(76)

        self.valueLabel = BodyLabel(value, self)
        self.valueLabel.setWordWrap(False)
        self.captionLabel = CaptionLabel(caption, self)
        self.captionLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(16, 12, 16, 12)
        self.vBoxLayout.setSpacing(4)
        self.vBoxLayout.addWidget(self.valueLabel)
        self.vBoxLayout.addWidget(self.captionLabel)

    def setValue(self, value):
        self.valueLabel.setText(value)


class DeviceInfoInterface(ScrollArea):
    """Device information page."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        self.titleLabel = TitleLabel('设备', self.view)

        # header
        self.nameLabel = SubtitleLabel('未连接设备', self.view)
        self.nameLabel.setObjectName('deviceNameLabel')
        self.statusLabel = BodyLabel('设备未连接', self.view)
        self.statusLabel.setObjectName('deviceStatusLabel')
        self.refreshBtn = HyperlinkButton('', '刷新数据', self.view)
        self.refreshBtn.setEnabled(False)

        self.headerLayout = QVBoxLayout()
        self.headerLayout.setSpacing(4)
        self.headerLayout.setContentsMargins(0, 0, 0, 0)
        self.headerLayout.addWidget(self.nameLabel)

        self.statusLayout = QHBoxLayout()
        self.statusLayout.setSpacing(8)
        self.statusLayout.setContentsMargins(0, 0, 0, 0)
        self.statusLayout.addWidget(self.statusLabel)
        self.statusLayout.addWidget(self.refreshBtn)
        self.statusLayout.addStretch(1)
        self.headerLayout.addLayout(self.statusLayout)

        # alert banner
        self.alertBanner = AlertBanner(self.view)
        self.alertBanner.setConnected(False)

        # big stat cards
        self.storageCard = ProgressCard(
            FIF.FOLDER, '剩余存储', '--', '/ --', 0, self.view)
        self.batteryCard = ProgressCard(
            FIF.POWER_BUTTON, '电量', '--', '%', 0, self.view)

        self.bigCardsLayout = QGridLayout()
        self.bigCardsLayout.setSpacing(12)
        self.bigCardsLayout.addWidget(self.storageCard, 0, 0)
        self.bigCardsLayout.addWidget(self.batteryCard, 0, 1)

        # detail info
        self.detailSectionLabel = SubtitleLabel('设备详细信息', self.view)
        self.detailSectionLabel.setObjectName('detailSectionLabel')

        self.macCard = DetailItemCard('--', '蓝牙地址', self.view)
        self.firmwareCard = DetailItemCard('--', '固件版本', self.view)
        self.modelCard = DetailItemCard('--', '设备型号', self.view)
        self.snCard = DetailItemCard('--', '序列号(S/N)', self.view)
        self.cpuidCard = DetailItemCard('--', 'CPU ID', self.view)
        self.timeCard = DetailItemCard('--', '系统时间', self.view)
        self.chargingCard = DetailItemCard('--', '充电状态', self.view)

        self.detailGrid = QGridLayout()
        self.detailGrid.setSpacing(12)
        self.detailGrid.addWidget(self.macCard, 0, 0)
        self.detailGrid.addWidget(self.firmwareCard, 0, 1)
        self.detailGrid.addWidget(self.modelCard, 1, 0)
        self.detailGrid.addWidget(self.snCard, 1, 1)
        self.detailGrid.addWidget(self.cpuidCard, 2, 0)
        self.detailGrid.addWidget(self.timeCard, 2, 1)
        self.detailGrid.addWidget(self.chargingCard, 3, 0)

        self.__initWidget()
        self.__connectSignalToSlot()
        self.__reset()

    def __initWidget(self):
        self.setObjectName('deviceInfoInterface')
        self.view.setObjectName('view')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        StyleSheet.DEVICE_INFO_INTERFACE.apply(self)

        self.vBoxLayout.setContentsMargins(36, 24, 36, 36)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addLayout(self.headerLayout)
        self.vBoxLayout.addWidget(self.alertBanner)
        self.vBoxLayout.addLayout(self.bigCardsLayout)
        self.vBoxLayout.addWidget(self.detailSectionLabel)
        self.vBoxLayout.addLayout(self.detailGrid)

    def __connectSignalToSlot(self):
        signalBus.deviceConnected.connect(self.__onDeviceConnected)
        signalBus.deviceDisconnected.connect(self.__onDeviceDisconnected)
        signalBus.deviceReconnecting.connect(self.__onDeviceReconnecting)
        signalBus.systemInfoReceived.connect(self.__onSystemInfoReceived)
        signalBus.systemInfoReceived.connect(self.__onRefreshFinished)
        self.alertBanner.reconnectClicked.connect(self.__onReconnect)
        self.refreshBtn.clicked.connect(self.__onRefreshData)

    def __onDeviceReconnecting(self):
        """Device is reconnecting automatically."""
        self.statusLabel.setText('重连中...')
        self.statusLabel.setProperty('connected', False)
        self.refreshBtn.setEnabled(False)

    def __reset(self):
        self.nameLabel.setText('未连接设备')
        self.statusLabel.setText('设备未连接')
        self.statusLabel.setProperty('connected', False)
        self.alertBanner.setConnected(False)

        self.storageCard.setValue('--', '/ --', 0)
        self.batteryCard.setValue('--', '%', 0)

        self.macCard.setValue('--')
        self.firmwareCard.setValue('--')
        self.modelCard.setValue('--')
        self.snCard.setValue('--')
        self.cpuidCard.setValue('--')
        self.timeCard.setValue('--')
        self.chargingCard.setValue('--')
        self.refreshBtn.setEnabled(False)

    def __onDeviceConnected(self, name, address):
        self.nameLabel.setText(name or '未知设备')
        self.statusLabel.setText('设备已连接')
        self.statusLabel.setProperty('connected', True)
        self.alertBanner.setConnected(True)
        self.refreshBtn.setEnabled(True)
        self.macCard.setValue(address or '--')

    def __onDeviceDisconnected(self):
        self.__reset()

    def __onSystemInfoReceived(self, info):
        if info is None:
            self.alertBanner.setBatteryMissing()
            return

        battery = getattr(info, 'battery_percent', None)
        if battery is None:
            self.alertBanner.setBatteryMissing()
        else:
            self.alertBanner.setConnected(True)
            self.batteryCard.setValue(str(battery), '%', battery)

        total = getattr(info, 'audio_storage_total', 0)
        avail = getattr(info, 'audio_storage_available', 0)
        percent = (avail / total * 100) if total else 0
        mb = 1024 * 1024
        total_mb = total / mb
        avail_mb = avail / mb
        self.storageCard.setValue(
            f'{avail_mb:.1f}', f'/ {total_mb:.1f} MB', min(100, percent))

        self.firmwareCard.setValue(getattr(info, 'firmware_version', '--'))
        self.modelCard.setValue(getattr(info, 'model', '--'))
        self.snCard.setValue(getattr(info, 'sn', '--'))
        self.cpuidCard.setValue(getattr(info, 'cpuid', '--'))

        system_time = getattr(info, 'system_time', 0)
        if system_time:
            try:
                dt = datetime.fromtimestamp(system_time, tz=timezone.utc)
                time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                time_str = str(system_time)
        else:
            time_str = '--'
        self.timeCard.setValue(time_str)

        charging = getattr(info, 'battery_charging', False)
        self.chargingCard.setValue('充电中' if charging else '未充电')

    def __onReconnect(self):
        InfoBar.info('重新连接', '请前往「连接戒指」页面重新连接',
                     parent=self.window(), duration=2000,
                     position=InfoBarPosition.TOP)

    def __onRefreshData(self):
        self.refreshBtn.setEnabled(False)
        self.statusLabel.setText('正在刷新数据...')
        signalBus.refreshSystemInfoRequested.emit()

    def __onRefreshFinished(self):
        self.refreshBtn.setEnabled(True)
        self.statusLabel.setText('设备已连接')
