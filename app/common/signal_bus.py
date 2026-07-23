# coding: utf-8
from PyQt5.QtCore import QObject, pyqtSignal


class SignalBus(QObject):
    """ Signal bus """

    micaEnableChanged = pyqtSignal(bool)
    switchToSampleCard = pyqtSignal(str, int)
    supportSignal = pyqtSignal()

    # device connection
    deviceConnected = pyqtSignal(str, str)      # name, address
    deviceDisconnected = pyqtSignal()
    systemInfoReceived = pyqtSignal(object)
    refreshSystemInfoRequested = pyqtSignal()

signalBus = SignalBus()
