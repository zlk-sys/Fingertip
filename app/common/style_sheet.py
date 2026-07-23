# coding: utf-8
from enum import Enum

from qfluentwidgets import StyleSheetBase, Theme, isDarkTheme, qconfig


class StyleSheet(StyleSheetBase, Enum):
    """ Style sheet  """

    HOME_INTERFACE = "home_interface"
    BASIC_INTERFACE = "basic_interface"
    SETTING_INTERFACE = "setting_interface"
    CONNECT_INTERFACE = "connect_interface"

    def path(self, theme=Theme.AUTO):
        theme = qconfig.theme if theme == Theme.AUTO else theme
        return f"app/common/qss/{theme.value.lower()}/{self.value}.qss"
