"""Settings from the environment / .env, with one rule: an empty value means "not set".

A line like `DATABASE_URL=   # comment` is read by some .env parsers as the comment text,
and `DATABASE_URL=` as an empty string; both must fall back to the default.
"""
from __future__ import annotations

import os


def env(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("#"):
        return default
    return value
