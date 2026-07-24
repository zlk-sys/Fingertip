# coding: utf-8
from enum import Enum

from qfluentwidgets import StyleSheetBase, Theme, isDarkTheme, qconfig


class StyleSheet(StyleSheetBase, Enum):
    """ Style sheet  """

    HOME_INTERFACE = "home_interface"
    SETTING_INTERFACE = "setting_interface"
    CONNECT_INTERFACE = "connect_interface"
    DEVICE_INFO_INTERFACE = "device_info_interface"
    MEETING_INTERFACE = "meeting_interface"
    MULTIMEDIA_INTERFACE = "multimedia_interface"
    SENSOR_INTERFACE = "sensor_interface"
    LEVEL_INTERFACE = "level_interface"
    DRAWING_INTERFACE = "drawing_interface"

    def path(self, theme=Theme.AUTO):
        theme = qconfig.theme if theme == Theme.AUTO else theme
        return f"app/common/qss/{theme.value.lower()}/{self.value}.qss"
