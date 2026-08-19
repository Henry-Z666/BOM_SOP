from __future__ import annotations

import unittest

from sop_pipeline.desktop.secret_store import (
    load_dashscope_key,
    save_dashscope_key,
    select_dashscope_key,
)


class FakeSettings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def value(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def setValue(self, key: str, value: str) -> None:
        self.values[key] = value

    def remove(self, key: str) -> None:
        self.values.pop(key, None)


class DashScopeSecretStoreTests(unittest.TestCase):
    def test_key_is_persisted_as_user_bound_ciphertext(self) -> None:
        settings = FakeSettings()
        secret = "sk-test-value-that-must-not-be-plaintext"

        save_dashscope_key(settings, secret)

        persisted = next(iter(settings.values.values()))
        self.assertNotIn(secret, persisted)
        self.assertEqual(load_dashscope_key(settings), secret)

    def test_blank_key_removes_saved_secret(self) -> None:
        settings = FakeSettings()
        save_dashscope_key(settings, "sk-existing")

        save_dashscope_key(settings, "")

        self.assertEqual(load_dashscope_key(settings), "")
        self.assertEqual(settings.values, {})

    def test_corrupt_ciphertext_is_discarded(self) -> None:
        settings = FakeSettings()
        settings.values["dashscope_key_dpapi_v1"] = "not-base64"

        self.assertEqual(load_dashscope_key(settings), "")
        self.assertEqual(settings.values, {})

    def test_saved_key_is_reused_until_user_replaces_it(self) -> None:
        self.assertEqual(
            select_dashscope_key("", "sk-saved", "sk-environment"),
            ("sk-saved", False),
        )
        self.assertEqual(
            select_dashscope_key("sk-new", "sk-saved", "sk-environment"),
            ("sk-new", True),
        )
        self.assertEqual(
            select_dashscope_key("", "", "sk-environment"),
            ("sk-environment", False),
        )


if __name__ == "__main__":
    unittest.main()
