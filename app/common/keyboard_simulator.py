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

# Virtual-Key Codes (Windows)
_VK_LEFT = 0x25
_VK_RIGHT = 0x27
_VK_MEDIA_PLAY_PAUSE = 0xB3
_VK_MEDIA_NEXT_TRACK = 0xB0
_VK_MEDIA_PREV_TRACK = 0xB1

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
