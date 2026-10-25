"""Where the app keeps its files, and the user's saved settings.

Settings live in settings.json. The API key is stored encrypted with Windows DPAPI, which ties it to this Windows
user account on this computer: another user, or the same file copied to another PC, cannot read it.

    installed app:      settings and usage log in %APPDATA%\\MeetMiner, transcripts in Documents\\MeetMiner
    run from a checkout: everything stays in the checkout's .local folder, which git ignores
    anywhere:           set MEETMINER_HOME to use another folder (the tests do)
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path

from .version import APP_NAME

PACKAGE_DIR = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))  # True inside the installed (PyInstaller) app


def resource_dir() -> Path:
    """Read-only files shipped inside the package (icon, default config). The same path installed or not."""
    return PACKAGE_DIR


def _checkout_root() -> Path | None:
    """The repository folder when running from a source checkout (src/meetminer/ inside it)."""
    root = PACKAGE_DIR.parents[1]
    return root if (root / "pyproject.toml").is_file() else None


def data_dir() -> Path:
    """Where this user's settings and logs live."""
    home = os.environ.get("MEETMINER_HOME")
    checkout = None if FROZEN else _checkout_root()
    if home:
        path = Path(home)
    elif checkout:
        path = checkout / ".local"  # keeps development data (and any real key) out of the tracked files
    else:
        path = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def documents_dir() -> Path:
    """The user's real Documents folder (it may be redirected to OneDrive)."""
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

        guid = GUID()
        ctypes.windll.ole32.CLSIDFromString("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}", ctypes.byref(guid))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
            path = Path(out.value)
            ctypes.windll.ole32.CoTaskMemFree(out)
            return path
    except (AttributeError, OSError):
        pass
    return Path.home() / "Documents"


def default_transcripts_dir() -> Path:
    return documents_dir() / APP_NAME / "Transcripts"


# --------------------------------------------------------------------------- Windows DPAPI


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _take(blob: _Blob) -> bytes:
    data = ctypes.string_at(blob.pbData, blob.cbData)
    ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(ctypes.addressof(blob.pbData.contents)))
    return data


def protect(data: bytes) -> bytes:
    src, _keep = _blob(data)
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), APP_NAME, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Windows could not encrypt the key")
    return _take(out)


def unprotect(data: bytes) -> bytes:
    src, _keep = _blob(data)
    out = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Windows could not decrypt the key")
    return _take(out)


# --------------------------------------------------------------------------- settings.json


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_user_settings() -> dict:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8-sig"))  # -sig: a file edited in Windows PowerShell has a BOM
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update_user_settings(**changes) -> None:
    """Change some settings and keep the rest. A value of None removes that setting."""
    values = load_user_settings()
    for key, value in changes.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    path = settings_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(values, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def get_api_key() -> str | None:
    stored = load_user_settings().get("api_key")
    if not isinstance(stored, str) or ":" not in stored:
        return None
    kind, _, payload = stored.partition(":")
    try:
        raw = base64.b64decode(payload)
        return (unprotect(raw) if kind == "dpapi" else raw).decode("utf-8") or None
    except (OSError, ValueError):
        return None  # e.g. the settings file was copied from another user or computer


def set_api_key(key: str | None) -> None:
    key = (key or "").strip()
    if not key:
        update_user_settings(api_key=None)
        return
    try:
        stored = "dpapi:" + base64.b64encode(protect(key.encode("utf-8"))).decode("ascii")
    except (AttributeError, OSError):  # not Windows: keep it working, but say so in the file
        stored = "plain:" + base64.b64encode(key.encode("utf-8")).decode("ascii")
    update_user_settings(api_key=stored)


def key_hint(key: str) -> str:
    """Enough of a key to recognise it, never the whole thing."""
    return f"{key[:7]}…{key[-4:]}" if len(key) > 16 else "saved"
