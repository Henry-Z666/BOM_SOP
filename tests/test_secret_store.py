from __future__ import annotations

import unittest

from sop_pipeline.desktop.secret_store import (
    load_dashscope_key,
    save_dashscope_key,
    select_dashscope_key,
)


class MemorySettings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def value(self, key: str, default=""):
        return self.values.get(key, default)

    def setValue(self, key: str, value: str) -> None:
        self.values[key] = value

    def remove(self, key: str) -> None:
        self.values.pop(key, None)


class DashScopeSecretStoreTests(unittest.TestCase):
    def test_key_round_trip_uses_protected_storage(self) -> None:
        settings = MemorySettings()
        save_dashscope_key(settings, "sk-test-secret")

        self.assertNotIn("sk-test-secret", next(iter(settings.values.values())))
        self.assertEqual(load_dashscope_key(settings), "sk-test-secret")

    def test_entered_key_wins_and_is_marked_for_persistence(self) -> None:
        self.assertEqual(
            select_dashscope_key("sk-new", "sk-saved", "sk-env"),
            ("sk-new", True),
        )
        self.assertEqual(
            select_dashscope_key("", "sk-saved", "sk-env"),
            ("sk-saved", False),
        )


if __name__ == "__main__":
    unittest.main()
