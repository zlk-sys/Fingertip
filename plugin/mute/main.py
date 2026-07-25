# coding: utf-8
"""一键静音 - 快速切换系统静音

痛点：开会/看视频时需要快速静音，找音量键很麻烦
解决：戒指单击即可切换静音状态
"""

from app.common.keyboard_simulator import press_mute


async def on_single_press():
    """单击：切换系统静音"""
    press_mute()
    print("[一键静音] 已切换静音状态")


async def on_activate():
    print("[一键静音] 已激活 - 单击切换静音")


async def on_deactivate():
    print("[一键静音] 已停用")
