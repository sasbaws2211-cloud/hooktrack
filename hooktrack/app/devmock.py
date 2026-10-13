"""The "mock" video / publish providers return a fake success without calling anything.

They exist so the app can be developed and tested offline. On a real server they are dangerous: a placeholder
"Video ready" or a fake "published" looks like the real thing. So they are OFF unless the operator sets
HOOKTRACK_ALLOW_MOCK_ADDONS=1 (the test suite does).
"""
import os

MOCK_NOTE = ("The mock (development) provider was selected for this add-on, but mock providers are switched off on "
             "this server. Choose a real provider to use it.")


def allowed() -> bool:
    return os.environ.get("HOOKTRACK_ALLOW_MOCK_ADDONS", "").strip() == "1"


def visible(providers: dict) -> dict:
    return providers if allowed() else {k: v for k, v in providers.items() if k != "mock"}
