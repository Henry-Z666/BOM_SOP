from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from typing import Any, Protocol


_DASHSCOPE_SETTING = "dashscope_key_dpapi_v1"
_DESCRIPTION = "CreoSopAgent DashScope API key"


class SettingsStore(Protocol):
    def value(self, key: str, default: Any = ...) -> Any: ...

    def setValue(self, key: str, value: Any) -> None: ...

    def remove(self, key: str) -> None: ...


def save_dashscope_key(settings: SettingsStore, value: str) -> None:
    secret = str(value).strip()
    if not secret:
        settings.remove(_DASHSCOPE_SETTING)
        return
    protected = _protect(secret.encode("utf-8"))
    settings.setValue(
        _DASHSCOPE_SETTING,
        base64.b64encode(protected).decode("ascii"),
    )


def load_dashscope_key(settings: SettingsStore) -> str:
    encoded = str(settings.value(_DASHSCOPE_SETTING, "") or "").strip()
    if not encoded:
        return ""
    try:
        protected = base64.b64decode(encoded, validate=True)
        cleartext = _unprotect(protected)
        return cleartext.decode("utf-8").strip()
    except (ValueError, UnicodeError, OSError):
        settings.remove(_DASHSCOPE_SETTING)
        return ""


def select_dashscope_key(
    entered: str,
    saved: str,
    environment: str,
) -> tuple[str, bool]:
    entered_value = str(entered).strip()
    if entered_value:
        return entered_value, True
    saved_value = str(saved).strip()
    if saved_value:
        return saved_value, False
    return str(environment).strip(), False


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _protect(cleartext: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(cleartext)
    input_blob = _DataBlob(
        len(cleartext),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        _DESCRIPTION,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)


def _unprotect(protected: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(protected)
    input_blob = _DataBlob(
        len(protected),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
