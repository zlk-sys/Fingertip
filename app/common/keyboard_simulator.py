# coding: utf-8
"""Cross-platform keyboard simulation for PPT and multimedia control.

Platforms:
  - Windows: ctypes.windll.user32.SendInput (Win32 API)
  - Linux:   xdotool subprocess (requires: sudo apt install xdotool)
  - macOS:   xdotool via brew (brew install xdotool)

Public API:
  next_slide()        -> Right arrow  (PPT next page)
  previous_slide()    -> Left arrow   (PPT previous page)
  toggle_play_pause() -> XF86AudioPlay (media play/pause)
  next_track()        -> XF86AudioNext (media next track)
  previous_track()    -> XF86AudioPrev (media previous track)
  press_key(key)      -> generic single key press
  is_available()      -> bool: whether key injection works on this platform
  get_backend()       -> str: active backend name (for diagnostics)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Optional

__all__ = [
    "next_slide",
    "previous_slide",
    "toggle_play_pause",
    "next_track",
    "previous_track",
    "press_key",
    "hotkey",
    "send_text",
    "press_enter",
    "press_mute",
    "is_available",
    "get_backend",
    "KeyNotSupportedError",
]


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

_IS_WINDOWS = sys.platform == "win32"
_IS_LINUX = sys.platform.startswith("linux")
_IS_MAC = sys.platform == "darwin"


# ---------------------------------------------------------------------------
# Windows backend: ctypes.windll.user32.SendInput
# Only imported on Windows to avoid AttributeError on Linux/macOS.
# ---------------------------------------------------------------------------

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004

# Virtual-Key Codes (Windows)
_VK_LEFT = 0x25
_VK_RIGHT = 0x27
_VK_RETURN = 0x0D
_VK_ESCAPE = 0x1B
_VK_TAB = 0x09
_VK_SPACE = 0x20
_VK_BACK = 0x08
_VK_DELETE = 0x2E
_VK_MEDIA_PLAY_PAUSE = 0xB3
_VK_MEDIA_NEXT_TRACK = 0xB0
_VK_MEDIA_PREV_TRACK = 0xB1
_VK_VOLUME_MUTE = 0xAD
_VK_VOLUME_UP = 0xAF
_VK_VOLUME_DOWN = 0xAE

# Named key -> VK code mapping (Windows)
_VK_MAP = {
    'ctrl': 0x11, 'control': 0x11,
    'alt': 0x12,
    'shift': 0x10,
    'win': 0x5B, 'super': 0x5B,
    'tab': 0x09,
    'enter': 0x0D, 'return': 0x0D,
    'escape': 0x1B, 'esc': 0x1B,
    'space': 0x20,
    'backspace': 0x08,
    'delete': 0x2E,
    'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27,
    'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73,
    'f5': 0x74, 'f6': 0x75, 'f7': 0x76, 'f8': 0x77,
    'f9': 0x78, 'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B,
    'a': 0x41, 'b': 0x42, 'c': 0x43, 'd': 0x44,
    'e': 0x45, 'f': 0x46, 'g': 0x47, 'h': 0x48,
    'i': 0x49, 'j': 0x4A, 'k': 0x4B, 'l': 0x4C,
    'm': 0x4D, 'n': 0x4E, 'o': 0x4F, 'p': 0x50,
    'q': 0x51, 'r': 0x52, 's': 0x53, 't': 0x54,
    'u': 0x55, 'v': 0x56, 'w': 0x57, 'x': 0x58,
    'y': 0x59, 'z': 0x5A,
    '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33,
    '4': 0x34, '5': 0x35, '6': 0x36, '7': 0x37,
    '8': 0x38, '9': 0x39,
}

_windows_send_input = None
_windows_import_error: Optional[Exception] = None

if _IS_WINDOWS:
    try:
        import ctypes
        from ctypes import wintypes

        wintypes.ULONG_PTR = wintypes.WPARAM  # type: ignore[attr-defined]

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
                ("mi", ctypes.c_char * 32),
                ("hi", ctypes.c_char * 24),
            ]

        class _INPUT(ctypes.Structure):
            _fields_ = [
                ("type", wintypes.DWORD),
                ("union", _INPUT_UNION),
            ]

        _send_input = ctypes.windll.user32.SendInput
        _send_input.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
        _send_input.restype = wintypes.UINT
        _windows_send_input = _send_input
    except Exception as _exc:
        _windows_import_error = _exc
        _windows_send_input = None


def _press_windows_vk(vk_code: int) -> None:
    """Inject a key press+release via Win32 SendInput."""
    if _windows_send_input is None:
        raise KeyNotSupportedError(
            "Windows SendInput unavailable"
            + (f": {_windows_import_error}" if _windows_import_error else "")
        )

    import ctypes

    events = (_INPUT * 2)()

    # Key down
    events[0].type = _INPUT_KEYBOARD
    events[0].union.ki.wVk = vk_code
    events[0].union.ki.wScan = 0
    events[0].union.ki.dwFlags = 0
    events[0].union.ki.time = 0
    events[0].union.ki.dwExtraInfo = 0

    # Key up
    events[1].type = _INPUT_KEYBOARD
    events[1].union.ki.wVk = vk_code
    events[1].union.ki.wScan = 0
    events[1].union.ki.dwFlags = _KEYEVENTF_KEYUP
    events[1].union.ki.time = 0
    events[1].union.ki.dwExtraInfo = 0

    _windows_send_input(2, events, ctypes.sizeof(_INPUT))


def _press_windows_unicode(char: str) -> None:
    """Inject a single Unicode character via Win32 SendInput."""
    if _windows_send_input is None:
        raise KeyNotSupportedError(
            "Windows SendInput unavailable"
            + (f": {_windows_import_error}" if _windows_import_error else "")
        )

    import ctypes

    code = ord(char)
    events = (_INPUT * 2)()

    # Key down (Unicode)
    events[0].type = _INPUT_KEYBOARD
    events[0].union.ki.wVk = 0
    events[0].union.ki.wScan = code
    events[0].union.ki.dwFlags = _KEYEVENTF_UNICODE
    events[0].union.ki.time = 0
    events[0].union.ki.dwExtraInfo = 0

    # Key up (Unicode)
    events[1].type = _INPUT_KEYBOARD
    events[1].union.ki.wVk = 0
    events[1].union.ki.wScan = code
    events[1].union.ki.dwFlags = _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP
    events[1].union.ki.time = 0
    events[1].union.ki.dwExtraInfo = 0

    _windows_send_input(2, events, ctypes.sizeof(_INPUT))


def _key_down_windows_vk(vk_code: int) -> None:
    """Inject a key down event via Win32 SendInput."""
    if _windows_send_input is None:
        raise KeyNotSupportedError("Windows SendInput unavailable")

    import ctypes
    event = (_INPUT * 1)()
    event[0].type = _INPUT_KEYBOARD
    event[0].union.ki.wVk = vk_code
    event[0].union.ki.wScan = 0
    event[0].union.ki.dwFlags = 0
    event[0].union.ki.time = 0
    event[0].union.ki.dwExtraInfo = 0
    _windows_send_input(1, event, ctypes.sizeof(_INPUT))


def _key_up_windows_vk(vk_code: int) -> None:
    """Inject a key up event via Win32 SendInput."""
    if _windows_send_input is None:
        raise KeyNotSupportedError("Windows SendInput unavailable")

    import ctypes
    event = (_INPUT * 1)()
    event[0].type = _INPUT_KEYBOARD
    event[0].union.ki.wVk = vk_code
    event[0].union.ki.wScan = 0
    event[0].union.ki.dwFlags = _KEYEVENTF_KEYUP
    event[0].union.ki.time = 0
    event[0].union.ki.dwExtraInfo = 0
    _windows_send_input(1, event, ctypes.sizeof(_INPUT))


# ---------------------------------------------------------------------------
# Linux / macOS backend: xdotool subprocess
# ---------------------------------------------------------------------------

_XDOTOOL_PATH: Optional[str] = shutil.which("xdotool")

# xdotool key name mapping
_XDOTOOL_KEYS = {
    "right": "Right",
    "left": "Left",
    "play_pause": "XF86AudioPlay",
    "next_track": "XF86AudioNext",
    "prev_track": "XF86AudioPrev",
}


def _press_xdotool(key_name: str) -> None:
    """Inject a key press via xdotool subprocess."""
    if not _XDOTOOL_PATH:
        raise KeyNotSupportedError(
            "xdotool not found. Install it:\n"
            "  Linux (Debian/Ubuntu): sudo apt install xdotool\n"
            "  Linux (Arch):          sudo pacman -S xdotool\n"
            "  Linux (Fedora):        sudo dnf install xdotool\n"
            "  macOS:                 brew install xdotool"
        )
    try:
        result = subprocess.run(
            [_XDOTOOL_PATH, "key", key_name],
            check=False,
            timeout=2,
            capture_output=True,
        )
        if result.returncode != 0:
            raise KeyNotSupportedError(
                f"xdotool failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace').strip() or '<no stderr>'}"
            )
    except subprocess.TimeoutExpired as e:
        raise KeyNotSupportedError(f"xdotool timeout: {e}") from e


def _type_xdotool(text: str) -> None:
    """Type a string of text via xdotool subprocess."""
    if not _XDOTOOL_PATH:
        raise KeyNotSupportedError("xdotool not found. Install it first.")
    try:
        result = subprocess.run(
            [_XDOTOOL_PATH, "type", "--clearmodifiers", "--", text],
            check=False,
            timeout=3,
            capture_output=True,
        )
        if result.returncode != 0:
            raise KeyNotSupportedError(
                f"xdotool type failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace').strip() or '<no stderr>'}"
            )
    except subprocess.TimeoutExpired as e:
        raise KeyNotSupportedError(f"xdotool timeout: {e}") from e


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class KeyNotSupportedError(RuntimeError):
    """Raised when key injection is not possible on the current platform."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_backend() -> str:
    """Return the active backend name (for diagnostics/logging)."""
    if _IS_WINDOWS:
        return "windows-sendinput" if _windows_send_input else "windows-unavailable"
    if _XDOTOOL_PATH:
        return f"xdotool ({_XDOTOOL_PATH})"
    return "unavailable"


def is_available() -> bool:
    """Whether key simulation actually works on this platform."""
    if _IS_WINDOWS:
        return _windows_send_input is not None
    return _XDOTOOL_PATH is not None


def press_key(key) -> None:
    """Generic single key press.

    Args:
        key: On Windows, an int VK code (e.g. 0x27 for Right).
             On Linux/macOS, an xdotool key name string (e.g. "Right").
    """
    if _IS_WINDOWS:
        if not isinstance(key, int):
            raise TypeError(
                f"On Windows, press_key() expects int VK code, got {type(key).__name__}"
            )
        _press_windows_vk(key)
    else:
        if not isinstance(key, str):
            raise TypeError(
                f"On Linux/macOS, press_key() expects xdotool key name (str), "
                f"got {type(key).__name__}"
            )
        _press_xdotool(key)


def next_slide() -> None:
    """Send Right arrow -> advance to next slide."""
    if _IS_WINDOWS:
        _press_windows_vk(_VK_RIGHT)
    else:
        _press_xdotool(_XDOTOOL_KEYS["right"])


def previous_slide() -> None:
    """Send Left arrow -> go back to previous slide."""
    if _IS_WINDOWS:
        _press_windows_vk(_VK_LEFT)
    else:
        _press_xdotool(_XDOTOOL_KEYS["left"])


def toggle_play_pause() -> None:
    """Send Play/Pause media key to toggle playback."""
    if _IS_WINDOWS:
        _press_windows_vk(_VK_MEDIA_PLAY_PAUSE)
    else:
        _press_xdotool(_XDOTOOL_KEYS["play_pause"])


def next_track() -> None:
    """Send Next Track media key."""
    if _IS_WINDOWS:
        _press_windows_vk(_VK_MEDIA_NEXT_TRACK)
    else:
        _press_xdotool(_XDOTOOL_KEYS["next_track"])


def previous_track() -> None:
    """Send Previous Track media key."""
    if _IS_WINDOWS:
        _press_windows_vk(_VK_MEDIA_PREV_TRACK)
    else:
        _press_xdotool(_XDOTOOL_KEYS["prev_track"])


def send_text(text: str) -> None:
    """Type a string of text into the focused window.

    On Windows this injects each character as a Unicode key event, so CJK
    text (e.g. "继续") works regardless of the active keyboard layout.
    """
    if not text:
        return
    if _IS_WINDOWS:
        for char in text:
            _press_windows_unicode(char)
    else:
        _type_xdotool(text)


def press_enter() -> None:
    """Send an Enter / Return key press."""
    if _IS_WINDOWS:
        _press_windows_vk(_VK_RETURN)
    else:
        _press_xdotool("Return")


def press_mute() -> None:
    """Send Volume Mute media key to toggle system mute."""
    if _IS_WINDOWS:
        _press_windows_vk(_VK_VOLUME_MUTE)
    else:
        _press_xdotool("XF86AudioMute")


def hotkey(*keys: str) -> None:
    """Press a combination of keys simultaneously.

    Args:
        keys: Key names in order. Modifier keys (ctrl, alt, shift, win) are
              held down, the last key is pressed, then modifiers are released.
              Examples: hotkey('ctrl', 'c'), hotkey('win', 'd'),
                        hotkey('ctrl', 'shift', 's')

    On Linux/macOS, falls back to xdotool key combination.
    """
    if not keys:
        return

    if _IS_WINDOWS:
        import time
        vk_codes = []
        for k in keys:
            k_lower = k.lower()
            if k_lower in _VK_MAP:
                vk_codes.append(_VK_MAP[k_lower])
            else:
                raise KeyNotSupportedError(f"Unknown key name: {k!r}")

        # Press all keys down in order
        for vk in vk_codes:
            _key_down_windows_vk(vk)
            time.sleep(0.02)

        # Release all keys in reverse order
        for vk in reversed(vk_codes):
            _key_up_windows_vk(vk)
            time.sleep(0.02)
    else:
        # xdotool: combine keys with '+'
        key_names = [k.lower() for k in keys]
        combo = '+'.join(key_names)
        _press_xdotool(combo)
