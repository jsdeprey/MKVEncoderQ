"""Discover VLC without loading native VLC code into the Encoder process."""
from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import struct
from pathlib import Path


def compatible_windows_vlc(folder: Path, machine: int | None = None) -> bool:
    if machine is None:
        machine = (0x14c if struct.calcsize("P") == 4 else
                   0xaa64 if platform.machine().lower() in {"arm64", "aarch64"} else 0x8664)
    try:
        if not (folder / "libvlccore.dll").is_file() or not (folder / "plugins").is_dir():
            return False
        with (folder / "libvlc.dll").open("rb") as dll:
            if dll.read(2) != b"MZ":
                return False
            dll.seek(0x3c)
            offset = struct.unpack("<I", dll.read(4))[0]
            if offset < 64 or offset > 1_048_576:
                return False
            dll.seek(offset)
            return dll.read(4) == b"PE\0\0" and struct.unpack("<H", dll.read(2))[0] == machine
    except (OSError, ValueError, struct.error):
        return False


def windows_vlc_candidates():
    from .config import application_directory
    yield application_directory() / "VLC"
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r"Software\VideoLAN\VLC", 0, winreg.KEY_READ | view) as key:
                        try:
                            yield Path(winreg.QueryValueEx(key, "InstallDir")[0])
                        except OSError:
                            yield Path(winreg.QueryValueEx(key, "")[0]).parent
                except OSError:
                    pass
    except ImportError:
        pass
    for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        if os.environ.get(variable):
            yield Path(os.environ[variable]) / "VideoLAN" / "VLC"
    executable = shutil.which("vlc")
    if executable:
        yield Path(executable).parent


def find_windows_vlc():
    return next((folder for folder in windows_vlc_candidates()
                 if compatible_windows_vlc(folder)), None)


def comparison_available() -> bool:
    try:
        if importlib.util.find_spec("vlc") is None:
            return False
        if os.name == "nt":
            return find_windows_vlc() is not None
        from ctypes.util import find_library
        return bool(find_library("vlc"))
    except (ImportError, ValueError, OSError):
        return False
