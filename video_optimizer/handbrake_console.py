from __future__ import annotations

import ctypes
import os


CREATE_NEW_CONSOLE = 0x00000010
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0
ATTACH_PARENT_PROCESS = -1
STD_INPUT_HANDLE = -10
KEY_EVENT = 0x0001


def hidden_console_startupinfo():
    """Return startup information that creates HandBrake's console invisibly."""
    if os.name != "nt":
        return None
    import subprocess
    info = subprocess.STARTUPINFO()
    info.dwFlags |= STARTF_USESHOWWINDOW
    info.wShowWindow = SW_HIDE
    return info


class _Char(ctypes.Union):
    _fields_ = [("UnicodeChar", ctypes.c_wchar), ("AsciiChar", ctypes.c_char)]


class _KeyEvent(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", ctypes.c_int), ("wRepeatCount", ctypes.c_ushort),
        ("wVirtualKeyCode", ctypes.c_ushort), ("wVirtualScanCode", ctypes.c_ushort),
        ("uChar", _Char), ("dwControlKeyState", ctypes.c_ulong),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("KeyEvent", _KeyEvent), ("padding", ctypes.c_byte * 16)]


class _InputRecord(ctypes.Structure):
    _fields_ = [("EventType", ctypes.c_ushort), ("Event", _InputUnion)]


def send_console_key(process_id: int, key: str) -> bool:
    """Write one native keypress to a child process's Windows console."""
    if os.name != "nt" or len(key) != 1:
        return False
    kernel32 = ctypes.windll.kernel32
    # A windowed Python process has no console. Temporarily attach to the
    # hidden HandBrake console, write a key-down/key-up pair, then detach.
    kernel32.FreeConsole()
    if not kernel32.AttachConsole(process_id):
        return False
    try:
        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        records = (_InputRecord * 2)()
        virtual = ord(key.upper())
        for index, down in enumerate((True, False)):
            records[index].EventType = KEY_EVENT
            records[index].Event.KeyEvent = _KeyEvent(
                int(down), 1, virtual, 0, _Char(UnicodeChar=key), 0
            )
        written = ctypes.c_ulong()
        return bool(kernel32.WriteConsoleInputW(
            handle, records, 2, ctypes.byref(written)
        ) and written.value == 2)
    finally:
        kernel32.FreeConsole()
        # Restore a parent console for source/CLI runs when one exists.
        kernel32.AttachConsole(ATTACH_PARENT_PROCESS)
