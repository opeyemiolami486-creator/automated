# Automated Local Leaderboard Monitor

A local-only Python monitor that checks an authorized leaderboard every 0.5 seconds during the UTC 22:00:00–23:59:59 window and submits test scores to `http://127.0.0.1:12`.

It does not submit to or modify any real leaderboard.

## Setup

```bash
python3 -m pip install aiohttp
python3 leaderboard_monitor.py
```

Set `LEADERBOARD_URL` to the organizers' authorized read-only JSON endpoint. If the local service expects a path, change `LOCAL_SUBMIT_URL`, for example:

```python
LOCAL_SUBMIT_URL = "http://127.0.0.1:12/score"
```
