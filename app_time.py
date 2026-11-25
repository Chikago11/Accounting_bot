"""Accounting time shared by buttons, reports and voice input."""

from datetime import datetime
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def timezone_name():
    # Keep the previous voice setting compatible with existing installations.
    return os.getenv("APP_TIMEZONE") or os.getenv("VOICE_TIMEZONE") or "Asia/Bangkok"


def now():
    return datetime.now(ZoneInfo(timezone_name()))


def validate_timezone():
    try:
        ZoneInfo(timezone_name())
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(
            "Invalid APP_TIMEZONE/VOICE_TIMEZONE; use an IANA name such as Asia/Bangkok"
        ) from error
