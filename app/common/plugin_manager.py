# coding: utf-8
"""Plugin system for Fingertip.

The PluginManager handles:
- Loading plugins from the plugin directory
- Managing plugin lifecycle (activate/deactivate)
- Dispatching ring button events to active plugins
"""

import asyncio
import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from ..sdk.ring_sound import SensorCommand


# Event type mapping
EVENT_TYPES = {
    'single_press': SensorCommand.KEY_SINGLE_PRESS,
    'double_press': SensorCommand.KEY_DOUBLE_PRESS,
    'double_tap': SensorCommand.DOUBLE_TAP,
}


@dataclass
class PluginInfo:
    """Metadata for a loaded plugin."""
    id: str
    name: str
    description: str
    icon: str
    version: str
    author: str
    path: str  # directory path
    handlers: Dict[str, str] = field(default_factory=dict)  # event -> function name
    module: Any = None  # loaded Python module
    active: bool = False


class PluginManager(QObject):
    """Manages plugin loading, lifecycle, and event dispatch."""

    # Signals
    pluginLoaded = pyqtSignal(str)  # plugin_id
    pluginActivated = pyqtSignal(str)  # plugin_id
    pluginDeactivated = pyqtSignal(str)  # plugin_id

    def __init__(self, plugin_dir: str, parent=None):
        super().__init__(parent)
        self.plugin_dir = plugin_dir
        self.plugins: Dict[str, PluginInfo] = {}
        self._active_plugin_id: Optional[str] = None
        self._client = None  # BLE client for registering handlers

    def load_plugins(self) -> List[str]:
        """Scan plugin directory and load all valid plugins.
        
        Returns list of loaded plugin IDs.
        """
        loaded = []
        if not os.path.isdir(self.plugin_dir):
            return loaded

        for entry in os.listdir(self.plugin_dir):
            plugin_path = os.path.join(self.plugin_dir, entry)
            if not os.path.isdir(plugin_path):
                continue

            config_path = os.path.join(plugin_path, 'plugin.json')
            if not os.path.isfile(config_path):
                continue

            try:
                info = self._load_plugin(config_path, plugin_path)
                if info:
                    self.plugins[info.id] = info
                    loaded.append(info.id)
                    self.pluginLoaded.emit(info.id)
            except Exception as e:
                print(f"[PluginManager] Failed to load plugin from {plugin_path}: {e}")

        return loaded

    def _load_plugin(self, config_path: str, plugin_path: str) -> Optional[PluginInfo]:
        """Load a single plugin from its directory."""
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # Validate required fields
        required = ['id', 'name', 'icon']
        for field in required:
            if field not in config:
                raise ValueError(f"Missing required field: {field}")

        # Load the Python module
        main_path = os.path.join(plugin_path, 'main.py')
        module = None
        if os.path.isfile(main_path):
            module = self._load_module(config['id'], main_path)

        return PluginInfo(
            id=config['id'],
            name=config['name'],
            description=config.get('description', ''),
            icon=config['icon'],
            version=config.get('version', '1.0.0'),
            author=config.get('author', 'Unknown'),
            path=plugin_path,
            handlers=config.get('handlers', {}),
            module=module,
        )

    def _load_module(self, plugin_id: str, main_path: str):
        """Dynamically load a Python module from file path."""
        module_name = f"plugin_{plugin_id}"
        spec = importlib.util.spec_from_file_location(module_name, main_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def get_plugin(self, plugin_id: str) -> Optional[PluginInfo]:
        """Get plugin info by ID."""
        return self.plugins.get(plugin_id)

    def get_all_plugins(self) -> List[PluginInfo]:
        """Get all loaded plugins."""
        return list(self.plugins.values())

    def get_active_plugin(self) -> Optional[PluginInfo]:
        """Get the currently active plugin."""
        if self._active_plugin_id:
            return self.plugins.get(self._active_plugin_id)
        return None

    async def activate_plugin(self, plugin_id: str, client) -> bool:
        """Activate a plugin and register its event handlers.
        
        Args:
            plugin_id: The plugin to activate
            client: The BLE client for registering packet handlers
            
        Returns:
            True if activation succeeded
        """
        plugin = self.plugins.get(plugin_id)
        if not plugin:
            return False

        if plugin.active:
            return True

        if self._active_plugin_id and self._active_plugin_id != plugin_id:
            # Deactivate current plugin first
            await self.deactivate_plugin(self._active_plugin_id)

        self._client = client
        self._active_plugin_id = plugin_id
        plugin.active = True

        # Register event handlers
        for event_name, func_name in plugin.handlers.items():
            if event_name not in EVENT_TYPES:
                continue
            sensor_cmd = EVENT_TYPES[event_name]
            handler = self._create_handler(plugin, func_name)
            if handler:
                client.add_packet_handler(sensor_cmd, handler)

        # Call on_activate if defined
        if plugin.module and hasattr(plugin.module, 'on_activate'):
            try:
                result = plugin.module.on_activate()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                print(f"[PluginManager] on_activate error: {e}")

        self.pluginActivated.emit(plugin_id)
        return True

    async def deactivate_plugin(self, plugin_id: str) -> None:
        """Deactivate a plugin and unregister its event handlers."""
        plugin = self.plugins.get(plugin_id)
        if not plugin or not plugin.active:
            return

        # Unregister event handlers
        if self._client:
            for event_name, func_name in plugin.handlers.items():
                if event_name not in EVENT_TYPES:
                    continue
                sensor_cmd = EVENT_TYPES[event_name]
                handler_name = f"_plugin_handler_{plugin_id}_{func_name}"
                # Try to remove the handler
                if hasattr(self, handler_name):
                    handler = getattr(self, handler_name)
                    try:
                        self._client.remove_packet_handler(sensor_cmd, handler)
                    except (ValueError, KeyError):
                        pass

        # Call on_deactivate if defined
        if plugin.module and hasattr(plugin.module, 'on_deactivate'):
            try:
                result = plugin.module.on_deactivate()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                print(f"[PluginManager] on_deactivate error: {e}")

        plugin.active = False
        self._active_plugin_id = None
        self.pluginDeactivated.emit(plugin_id)

    def _create_handler(self, plugin: PluginInfo, func_name: str):
        """Create a packet handler for a plugin function."""
        if not plugin.module:
            return None
        
        func = getattr(plugin.module, func_name, None)
        if not func:
            return None

        # Store handler reference for later removal
        handler_name = f"_plugin_handler_{plugin.id}_{func_name}"
        
        async def handler(packet):
            try:
                result = func()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                print(f"[PluginManager] Handler error ({plugin.id}.{func_name}): {e}")

        setattr(self, handler_name, handler)
        return handler

    def is_active(self, plugin_id: str) -> bool:
        """Check if a plugin is currently active."""
        plugin = self.plugins.get(plugin_id)
        return plugin.active if plugin else False
