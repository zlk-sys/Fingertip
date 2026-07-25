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
    deviceReconnecting = pyqtSignal()            # auto-reconnect started
    systemInfoReceived = pyqtSignal(object)
    refreshSystemInfoRequested = pyqtSignal()

    # navigation
    switchToMeeting = pyqtSignal()
    switchToMultimedia = pyqtSignal()
    switchToConnect = pyqtSignal()
    switchToDevice = pyqtSignal()
    switchToSensor = pyqtSignal()
    switchToLevel = pyqtSignal()
    switchToDrawing = pyqtSignal()
    switchToGesture = pyqtSignal()
    switchToCoding = pyqtSignal()
    switchToCollab = pyqtSignal()
    switchToGestureMapping = pyqtSignal()
    switchToPlugin = pyqtSignal(str)  # plugin_id

    # mode mutual exclusion
    # mode id includes sensor streams such as drawing and hmm_gesture
    modeStarted = pyqtSignal(str)
    modeStopped = pyqtSignal(str)

signalBus = SignalBus()
