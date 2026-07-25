# coding: utf-8
"""窗口管家 - 快速管理窗口

痛点：需要快速隐藏窗口或切换应用时，鼠标操作太慢
解决：戒指单击显示桌面，双击切换窗口
"""

from app.common.keyboard_simulator import hotkey


async def on_single_press():
    """单击：显示桌面 (Win+D)"""
    hotkey('win', 'd')
    print("[窗口管家] 已显示桌面")


async def on_double_press():
    """双击：切换窗口 (Alt+Tab)"""
    hotkey('alt', 'tab')
    print("[窗口管家] 已切换窗口")


async def on_activate():
    print("[窗口管家] 已激活 - 单击显示桌面，双击切换窗口")


async def on_deactivate():
    print("[窗口管家] 已停用")
