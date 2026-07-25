# coding: utf-8
"""快捷截图 - 快速截取屏幕

痛点：截图快捷键记不住，或者需要打开截图工具
解决：戒指单击全屏截图，双击区域截图（Win+Shift+S）
"""

from app.common.keyboard_simulator import press_key, hotkey


async def on_single_press():
    """单击：全屏截图 (PrintScreen)"""
    # PrintScreen VK code = 0x2C
    press_key(0x2C)
    print("[快捷截图] 全屏截图已保存到剪贴板")


async def on_double_press():
    """双击：区域截图 (Win+Shift+S)"""
    hotkey('win', 'shift', 's')
    print("[快捷截图] 区域截图工具已打开")


async def on_activate():
    print("[快捷截图] 已激活 - 单击全屏，双击区域")


async def on_deactivate():
    print("[快捷截图] 已停用")
