import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def test_decimal_polling_and_invalid_values():
    env = os.environ.copy()
    env.update({
        "QUESTION_URL": "https://example.test/questions",
        "START_URL": "https://example.test/start",
        "SUBMIT_URL": "https://example.test/submit",
        "POLL_SECONDS": "0.1",
        "RUN_ONCE": "true",
    })
    code = "from answer_automation import Config; print(Config.from_env().poll_seconds)"
    result = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.1"


def test_invalid_interval_is_rejected():
    env = os.environ.copy()
    env.update({
        "QUESTION_URL": "https://example.test/questions",
        "START_URL": "https://example.test/start",
        "SUBMIT_URL": "https://example.test/submit",
        "POLL_SECONDS": "0",
    })
    code = "from answer_automation import Config; Config.from_env()"
    result = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "greater than 0" in result.stderr


def test_state_file_round_trip():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "answers.json"
        path.write_text(json.dumps({"q-1": "yes"}), encoding="utf-8")
        assert json.loads(path.read_text()) == {"q-1": "yes"}


if __name__ == "__main__":
    test_decimal_polling_and_invalid_values()
    test_invalid_interval_is_rejected()
    test_state_file_round_trip()
    print("answer automation tests passed")
