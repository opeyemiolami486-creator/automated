# Authorized Answer Automation Worker

This repository contains a Python worker for a hackathon website that monitors a JSON question source and sends **approved answers** to an authorized submission API. It is designed for a website you own or a service whose organizers have explicitly authorized automation.

The worker is **dry-run by default**. It never submits live answers unless `ENABLE_SUBMISSION=true` is set.

## How the flow works

The worker repeats this sequence:

1. `GET QUESTION_URL` to discover questions or tasks.
2. Look up an approved answer in `answers.json` using the question ID.
3. `POST START_URL` to obtain a short-lived run token.
4. `POST SUBMIT_URL` with the question ID, answer, and token.
5. Save a state record so the same question is not submitted twice.

The expected response shapes are intentionally flexible:

```json
GET /questions
{"questions": [{"id": "q-123", "prompt": "..."}]}
```

```json
POST /start
{}
```

```json
{"token": "server-issued-token"}
```

```json
POST /submit
{"question_id": "q-123", "answer": "approved answer", "token": "server-issued-token"}
```

A Webcade Dudas Jump-style game uses the same general pattern: `POST /start` returns a run token and a later `POST /score` sends the completed result with that token. For a hackathon answer system, use your own documented field names and endpoint URLs; do not assume that an unrelated website accepts this payload.

## Install and configure

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set these variables in `.env` or in the process environment:

```text
QUESTION_URL=https://your-site.example/api/questions
START_URL=https://your-site.example/api/start
SUBMIT_URL=https://your-site.example/api/submit
ANSWER_FILE=answers.json
STATE_FILE=answer_automation_state.json
POLL_SECONDS=1
REQUEST_TIMEOUT_SECONDS=20
ENABLE_SUBMISSION=false
RUN_ONCE=false
```

Create `answers.json` with approved answers keyed by question ID:

```json
{
  "q-123": "answer approved by the participant",
  "q-456": {"choice": "B", "explanation": "..."}
}
```

Run a safe test first:

```bash
set -a; . ./.env; set +a
RUN_ONCE=true ENABLE_SUBMISSION=false python answer_automation.py
```

Only after verifying the dry-run output and confirming that the organizers permit automation should you enable live requests:

```bash
ENABLE_SUBMISSION=true python answer_automation.py
```

## Safety and reliability behavior

The worker does not generate answers. It only sends entries already present in `answers.json`, which prevents accidental or unreviewed answer generation. It skips questions without an approved answer, persists successful submissions, avoids duplicate live posts, uses request timeouts, handles HTTP 429 responses, retries transient failures with exponential backoff, supports clean shutdown, and accepts sub-second polling intervals such as `POLL_SECONDS=0.1`.

Keep credentials in environment variables or your host’s secret store. Do not commit `.env`, access tokens, wallet secrets, or private keys. The submission endpoint should authenticate the user or worker server-side and should validate token ownership, expiry, one-time use, answer format, and rate limits.

## Tests

```bash
python -m py_compile answer_automation.py
python test_answer_automation.py
```

## Deployment

This is a long-running worker. Run it on a permitted server, container, or managed worker service with environment variables configured. For a hackathon demo, `RUN_ONCE=true` is useful for a single controlled test; for continuous monitoring, leave it false and use a host that stays online.
