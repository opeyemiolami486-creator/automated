import asyncio
import os
from datetime import datetime, timedelta

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

from telegram_score_bot import parse_deadline


def test_exact_clock_format():
    deadline = parse_deadline("11:59:59")
    assert deadline.tzinfo is not None
    assert deadline.strftime("%H:%M:%S") == "11:59:59"


def test_invalid_clock_format():
    try:
        parse_deadline("11:59")
    except ValueError as exc:
        assert "HH:MM:SS" in str(exc)
    else:
        raise AssertionError("invalid clock format was accepted")


if __name__ == "__main__":
    test_exact_clock_format()
    test_invalid_clock_format()
    print("scheduling tests passed")
