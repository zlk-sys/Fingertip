# coding: utf-8
"""剪贴板助手 - 快速复制粘贴

痛点：频繁使用 Ctrl+C / Ctrl+V 很麻烦
解决：戒指单击复制，双击粘贴
"""

from app.common.keyboard_simulator import hotkey


async def on_single_press():
    """单击：复制选中文本 (Ctrl+C)"""
    hotkey('ctrl', 'c')
    print("[剪贴板助手] 已复制")


async def on_double_press():
    """双击：粘贴 (Ctrl+V)"""
    hotkey('ctrl', 'v')
    print("[剪贴板助手] 已粘贴")


async def on_activate():
    print("[剪贴板助手] 已激活 - 单击复制，双击粘贴")


async def on_deactivate():
    print("[剪贴板助手] 已停用")
