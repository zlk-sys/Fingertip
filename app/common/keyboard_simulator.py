# coding: utf-8
"""Windows keyboard simulation using SendInput API.

Provides reliable key press simulation for controlling PPT presentations.
Single-click -> next slide (VK_RIGHT), double-click -> previous slide (VK_LEFT).
"""
import ctypes
from ctypes import wintypes

# Win32 constants
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

VK_RIGHT = 0x27
VK_LEFT = 0x25

# SendInput type definitions
wintypes.ULONG_PTR = wintypes.WPARAM


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", _KEYBDINPUT),
        ("mi", ctypes.c_char * 32),  # MOUSEINPUT placeholder
        ("hi", ctypes.c_char * 24),  # HARDWAREINPUT placeholder
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


_send_input = ctypes.windll.user32.SendInput
_send_input.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_send_input.restype = wintypes.UINT


def press_key(vk_code: int) -> None:
    """Press and release a virtual key."""
    events = (_INPUT * 2)()

    # Key down
    events[0].type = INPUT_KEYBOARD
    events[0].union.ki.wVk = vk_code
    events[0].union.ki.wScan = 0
    events[0].union.ki.dwFlags = 0
    events[0].union.ki.time = 0
    events[0].union.ki.dwExtraInfo = 0

    # Key up
    events[1].type = INPUT_KEYBOARD
    events[1].union.ki.wVk = vk_code
    events[1].union.ki.wScan = 0
    events[1].union.ki.dwFlags = KEYEVENTF_KEYUP
    events[1].union.ki.time = 0
    events[1].union.ki.dwExtraInfo = 0

    _send_input(2, events, ctypes.sizeof(_INPUT))


def next_slide() -> None:
    """Send Right arrow key to advance to next slide."""
    press_key(VK_RIGHT)


def previous_slide() -> None:
    """Send Left arrow key to go back to previous slide."""
    press_key(VK_LEFT)
