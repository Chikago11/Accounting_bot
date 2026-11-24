"""Accounting time shared by buttons, reports and voice input."""

from datetime import datetime
import os
from zoneinfo import ZoneInfo


def timezone_name():
    # Keep the previous voice setting compatible with existing installations.
    return os.getenv("APP_TIMEZONE") or os.getenv("VOICE_TIMEZONE") or "Asia/Bangkok"


def now():
    return datetime.now(ZoneInfo(timezone_name()))
