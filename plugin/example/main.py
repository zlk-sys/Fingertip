# coding: utf-8
"""示例插件 - 演示如何使用戒指按钮事件

该插件演示了三种事件的响应方式：
- single_press: 单击戒指按钮
- double_press: 双击戒指按钮
- double_tap: 敲两下桌面（戒指本体双击）

每个处理函数都是异步的（async def），可以执行耗时操作。
"""

import asyncio


async def on_single_press():
    """单击戒指按钮时调用
    
    这里可以添加你的逻辑，例如：
    - 发送键盘快捷键
    - 控制其他应用
    - 触发自动化流程
    """
    print("[示例插件] 检测到单击！")
    # 示例：模拟按下 F5 刷新
    # from app.common.keyboard_simulator import press_key
    # press_key('f5')


async def on_double_press():
    """双击戒指按钮时调用
    
    适合用于触发更重要的操作，例如：
    - 打开/关闭某个功能
    - 切换模式
    - 执行预设命令
    """
    print("[示例插件] 检测到双击！")
    # 示例：模拟按下 Enter
    # from app.common.keyboard_simulator import press_enter
    # press_enter()


async def on_double_tap():
    """敲两下桌面时调用
    
    注意：需要在插件页面开启「敲桌面」功能开关
    适合用于：
    - 快捷操作（如静音、暂停）
    - 紧急停止
    """
    print("[示例插件] 检测到敲桌面！")
    # 示例：模拟播放/暂停
    # from app.common.keyboard_simulator import toggle_play_pause
    # toggle_play_pause()


# 可选：插件激活时调用
async def on_activate():
    """插件模式被开启时调用"""
    print("[示例插件] 插件已激活")


# 可选：插件停用时调用
async def on_deactivate():
    """插件模式被关闭时调用"""
    print("[示例插件] 插件已停用")
