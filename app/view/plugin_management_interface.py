# coding: utf-8
"""Plugin management interface - shows all available plugins with controls."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (ScrollArea, TitleLabel, BodyLabel, StrongBodyLabel,
                            CaptionLabel, SimpleCardWidget, IconWidget,
                            SwitchButton, ToolTipFilter, InfoBar, InfoBarPosition,
                            qconfig)
from qfluentwidgets import FluentIcon as FIF

from ..common.style_sheet import StyleSheet
from ..common.config import cfg
from ..common.plugin_manager import PluginManager
from .plugin_interface import get_icon


class PluginCard(SimpleCardWidget):
    """Card showing plugin info with enable/disable switch."""

    def __init__(self, plugin_info, parent=None):
        super().__init__(parent)
        self.plugin_info = plugin_info
        self.setBorderRadius(10)
        self.setFixedHeight(80)

        icon = get_icon(plugin_info.icon)
        self.iconWidget = IconWidget(icon, self)
        self.iconWidget.setFixedSize(24, 24)

        self.nameLabel = StrongBodyLabel(plugin_info.name, self)
        self.descLabel = CaptionLabel(plugin_info.description, self)
        self.descLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.versionLabel = CaptionLabel(f'v{plugin_info.version}', self)
        self.versionLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.authorLabel = CaptionLabel(f'by {plugin_info.author}', self)
        self.authorLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        # Enable/disable switch
        self.toggleSwitch = SwitchButton(self)
        self.toggleSwitch.setToolTip(f'开启/关闭 {plugin_info.name}')
        self.toggleSwitch.installEventFilter(ToolTipFilter(self.toggleSwitch))

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(20, 16, 20, 16)
        self.hBoxLayout.setSpacing(16)
        self.hBoxLayout.addWidget(self.iconWidget)
        self.hBoxLayout.addSpacing(4)

        self.textLayout = QVBoxLayout()
        self.textLayout.setSpacing(4)
        self.textLayout.addWidget(self.nameLabel)
        self.textLayout.addWidget(self.descLabel)
        self.textLayout.addStretch(1)
        self.hBoxLayout.addLayout(self.textLayout)
        self.hBoxLayout.addStretch(1)

        # Right side: version/author + toggle switch
        self.rightLayout = QVBoxLayout()
        self.rightLayout.setSpacing(4)
        self.rightLayout.addWidget(self.versionLabel, 0, Qt.AlignRight)
        self.rightLayout.addWidget(self.authorLabel, 0, Qt.AlignRight)
        self.rightLayout.addStretch(1)
        self.hBoxLayout.addLayout(self.rightLayout)
        self.hBoxLayout.addWidget(self.toggleSwitch, 0, Qt.AlignVCenter)


class PluginManagementInterface(ScrollArea):
    """Plugin management page - lists all available plugins with controls."""

    def __init__(self, plugin_manager: PluginManager, parent=None):
        super().__init__(parent=parent)
        self.plugin_manager = plugin_manager
        self.cards = {}  # plugin_id -> PluginCard

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        # Title
        self.titleLabel = TitleLabel('插件管理', self.view)
        self.subtitleLabel = BodyLabel('管理已安装的插件，开启后插件将显示在侧边栏', self.view)

        # Plugin list section
        self.pluginCountLabel = CaptionLabel('', self.view)
        self.pluginCountLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))

        self.__initWidget()
        self.__loadPlugins()

    def __initWidget(self):
        self.setObjectName('pluginManagementInterface')
        self.view.setObjectName('view')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        StyleSheet.CONNECT_INTERFACE.apply(self)

        self.vBoxLayout.setContentsMargins(36, 24, 36, 36)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.pluginCountLabel)

    def __loadPlugins(self):
        """Load and display all plugins."""
        plugins = self.plugin_manager.get_all_plugins()
        enabled_plugins = cfg.get(cfg.enabledPlugins) or []
        self.pluginCountLabel.setText(f'共 {len(plugins)} 个插件')

        for plugin_info in plugins:
            card = PluginCard(plugin_info, self.view)
            # Set initial state from config
            card.toggleSwitch.setChecked(plugin_info.id in enabled_plugins)
            card.toggleSwitch.checkedChanged.connect(
                lambda checked, pid=plugin_info.id: self.__onTogglePlugin(pid, checked)
            )
            self.cards[plugin_info.id] = card
            self.vBoxLayout.addWidget(card)

        if not plugins:
            emptyLabel = CaptionLabel('暂无已安装插件\n\n将插件文件夹放入 plugin/ 目录即可安装', self.view)
            emptyLabel.setAlignment(Qt.AlignCenter)
            emptyLabel.setTextColor(QColor(96, 96, 96), QColor(180, 180, 180))
            self.vBoxLayout.addWidget(emptyLabel)

        self.vBoxLayout.addStretch(1)

    def __onTogglePlugin(self, plugin_id: str, checked: bool):
        """Handle toggle - update config and show restart prompt."""
        enabled_plugins = cfg.get(cfg.enabledPlugins) or []
        
        if checked:
            if plugin_id not in enabled_plugins:
                enabled_plugins.append(plugin_id)
        else:
            if plugin_id in enabled_plugins:
                enabled_plugins.remove(plugin_id)
        
        cfg.set(cfg.enabledPlugins, enabled_plugins)
        qconfig.save()

        # Show restart prompt
        if checked:
            InfoBar.success(
                '插件已开启',
                '请重启应用以在侧边栏显示插件',
                parent=self.window(),
                duration=4000,
                position=InfoBarPosition.TOP_RIGHT
            )
        else:
            InfoBar.info(
                '插件已关闭',
                '请重启应用以从侧边栏移除插件',
                parent=self.window(),
                duration=4000,
                position=InfoBarPosition.TOP_RIGHT
            )
