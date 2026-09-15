import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from answer_automation import AutomationWorker, Config


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


def test_token_extraction_shapes():
    config = Config(
        question_url="https://example.test/questions",
        start_url="https://example.test/start",
        submit_url="https://example.test/submit",
        answer_file=Path("answers.json"),
        state_file=Path("state.json"),
        poll_seconds=1.0,
        request_timeout=10.0,
        enable_submission=False,
        once=True,
        token_json_path="data.run.token",
        token_field="runToken",
    )
    worker = AutomationWorker(config)
    assert worker.extract_token({"data": {"run": {"token": "nested-token"}}}) == "nested-token"
    assert worker.extract_token({"run_token": "alternate-token"}) == "alternate-token"


def test_timing_metadata_and_board_factor():
    from authorized_test_client import derive_time_factor, extract_timing

    assert extract_timing({"timeFactor": 0.15}) == (None, 0.15)
    assert extract_timing({"timing": {"allowed_seconds": 12}}) == (12.0, None)
    assert extract_timing({"expires_in": 300}) == (None, None)
    assert derive_time_factor({"list": [{"height": 2000, "secs": 320}, {"height": 1000, "secs": 180}]}) == 0.16


def test_allowlist_normalizes_domains():
    from authorized_test_client import allowed_host
    old = os.environ.get("AUTHORIZED_TEST_DOMAINS")
    os.environ["AUTHORIZED_TEST_DOMAINS"] = "https://team-example.com/test, *.staging.com"
    try:
        assert allowed_host("https://team-example.com/game")
        assert allowed_host("https://api.staging.com:443/game")
    finally:
        if old is None:
            os.environ.pop("AUTHORIZED_TEST_DOMAINS", None)
        else:
            os.environ["AUTHORIZED_TEST_DOMAINS"] = old


if __name__ == "__main__":
    test_decimal_polling_and_invalid_values()
    test_invalid_interval_is_rejected()
    test_state_file_round_trip()
    test_token_extraction_shapes()
    test_allowlist_normalizes_domains()
    print("answer automation tests passed")
