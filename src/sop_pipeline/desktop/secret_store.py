from __future__ import annotations

import base64
from typing import Any, Protocol

import win32crypt


_DASHSCOPE_SETTING = "dashscope_key_dpapi_v1"
_DESCRIPTION = "QwenCreoSopAgent DashScope API key"


class SettingsStore(Protocol):
    def value(self, key: str, default: Any = ...) -> Any: ...

    def setValue(self, key: str, value: Any) -> None: ...

    def remove(self, key: str) -> None: ...


def save_dashscope_key(settings: SettingsStore, value: str) -> None:
    secret = str(value).strip()
    if not secret:
        settings.remove(_DASHSCOPE_SETTING)
        return
    protected = win32crypt.CryptProtectData(
        secret.encode("utf-8"),
        _DESCRIPTION,
        None,
        None,
        None,
        0,
    )
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
        _description, cleartext = win32crypt.CryptUnprotectData(
            protected,
            None,
            None,
            None,
            0,
        )
        secret = cleartext.decode("utf-8").strip()
    except (ValueError, UnicodeError, OSError):
        settings.remove(_DASHSCOPE_SETTING)
        return ""
    return secret


def select_dashscope_key(
    entered: str,
    saved: str,
    environment: str,
) -> tuple[str, bool]:
    """Choose the active key and whether it should replace secure storage."""

    entered_value = str(entered).strip()
    if entered_value:
        return entered_value, True
    saved_value = str(saved).strip()
    if saved_value:
        return saved_value, False
    environment_value = str(environment).strip()
    return environment_value, bool(environment_value)
