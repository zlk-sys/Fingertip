# coding: utf-8
import os
import sys
from enum import Enum

from PyQt5.QtCore import QLocale
from qfluentwidgets import (qconfig, QConfig, ConfigItem, OptionsConfigItem, BoolValidator,
                            OptionsValidator, RangeConfigItem, RangeValidator,
                            Theme, FolderValidator, ConfigSerializer, __version__)


class Language(Enum):
    """ Language enumeration """

    CHINESE_SIMPLIFIED = QLocale(QLocale.Chinese, QLocale.China)
    CHINESE_TRADITIONAL = QLocale(QLocale.Chinese, QLocale.HongKong)
    ENGLISH = QLocale(QLocale.English)
    AUTO = QLocale()


class LanguageSerializer(ConfigSerializer):
    """ Language serializer """

    def serialize(self, language):
        return language.value.name() if language != Language.AUTO else "Auto"

    def deserialize(self, value: str):
        return Language(QLocale(value)) if value != "Auto" else Language.AUTO


def isWin11():
    return sys.platform == 'win32' and sys.getwindowsversion().build >= 22000


class Config(QConfig):
    """ Config of application """

    # folders
    downloadFolder = ConfigItem(
        "Folders", "Download", "app/download", FolderValidator())

    # main window
    micaEnabled = ConfigItem("MainWindow", "MicaEnabled", isWin11(), BoolValidator())
    dpiScale = OptionsConfigItem(
        "MainWindow", "DpiScale", "Auto", OptionsValidator([1, 1.25, 1.5, 1.75, 2, "Auto"]), restart=True)
    language = OptionsConfigItem(
        "MainWindow", "Language", Language.AUTO, OptionsValidator(Language), LanguageSerializer(), restart=True)

    # Material
    blurRadius = RangeConfigItem("Material", "AcrylicBlurRadius", 15, RangeValidator(0, 40))

    # software update
    checkUpdateAtStartUp = ConfigItem("Update", "CheckUpdateAtStartUp", True, BoolValidator())

    # Coding mode
    codingAssistant = OptionsConfigItem(
        "Coding", "Assistant", "claude", OptionsValidator(["claude", "qoder"]))

    # Collab mode (协同模式)
    stepFunApiKey = ConfigItem("Collab", "StepFunApiKey", "")
    collabModel = ConfigItem("Collab", "CollabModel", "step-2-16k")
    collabSystemPrompt = ConfigItem(
        "Collab", "SystemPrompt",
        "你是一个智能助手，请根据用户的语音转写内容给出简洁、有帮助的回答。请直接回答，不要重复用户的问题。")

    # DeepSeek semantic composition
    deepSeekApiKey = ConfigItem("DeepSeek", "ApiKey", "")
    deepSeekModel = ConfigItem("DeepSeek", "Model", "")

    # Plugin system
    enabledPlugins = ConfigItem("Plugins", "EnabledPlugins", [])  # List of enabled plugin IDs


YEAR = 2026
AUTHOR = "Fingertip"
VERSION = __version__
HELP_URL = "https://qfluentwidgets.com"
FEEDBACK_URL = "https://github.com/zhiyiYo/PyQt-Fluent-Widgets/issues"

# Config file path: %LOCALAPPDATA%\Fingertip\config.json
_localAppData = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
_configDir = os.path.join(_localAppData, 'Fingertip')
os.makedirs(_configDir, exist_ok=True)
CONFIG_FILE_PATH = os.path.join(_configDir, 'config.json')

cfg = Config()
cfg.themeMode.value = Theme.AUTO
qconfig.load(CONFIG_FILE_PATH, cfg)
