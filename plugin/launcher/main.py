# coding: utf-8
"""快捷启动 - 快速启动应用和功能

痛点：需要快速打开运行对话框或任务视图时，快捷键记不住
解决：戒指单击打开运行对话框(Win+R)，双击打开任务视图(Win+Tab)
"""

from app.common.keyboard_simulator import hotkey


async def on_single_press():
    """单击：打开运行对话框 (Win+R)"""
    hotkey('win', 'r')
    print("[快捷启动] 运行对话框已打开")


async def on_double_press():
    """双击：打开任务视图 (Win+Tab)"""
    hotkey('win', 'tab')
    print("[快捷启动] 任务视图已打开")


async def on_activate():
    print("[快捷启动] 已激活 - 单击运行，双击任务视图")


async def on_deactivate():
    print("[快捷启动] 已停用")
