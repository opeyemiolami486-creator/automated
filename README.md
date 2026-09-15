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

### Team test-site token compatibility

For an authorized team test website, the worker extracts only a token returned by the configured start endpoint; it does not guess, mint, or bypass authentication. It supports these response shapes by default:

```json
{"token": "..."}
{"run_token": "..."}
{"data": {"token": "..."}}
{"run": {"token": "..."}}
```

If the team API nests the token elsewhere, configure a dotted JSON path and the field name expected by the submission endpoint:

```text
TOKEN_JSON_PATH=data.run.token
TOKEN_FIELD=runToken
```

The worker then sends that exact server-issued value in the configured field. The team should provide the endpoint contract and authentication method; the worker cannot obtain a token from an endpoint that requires missing credentials, browser-only proof, CAPTCHA completion, or an undocumented private flow.

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
python test_mock_api.py
```

`test_mock_api.py` verifies the requirements contract, empty leaderboard,
server-issued tokens, valid score submission, millisecond timestamps, token
replay rejection, missing-field rejection, invalid-token rejection, and
leaderboard ordering.

## Local Webcade-style mock and higher-score test

The repository also includes a local-only mock that mirrors the reference flow:

```text
POST /api/dudas/start
POST /api/dudas/score
GET  /api/dudas/board
```

Start the mock API in one terminal:

```bash
MOCK_PORT=8080 python mock_webcade_api.py
```

In another terminal, run the local score client:

```bash
python mock_score_automation.py --base-url http://127.0.0.1:8080 --increment 1000
```

The client first reads the local top score, requests a fresh one-use run token,
reads `/api/dudas/requirements` to discover the required identity field,
prompts for a wallet address or username when one was not configured,
chooses a realistic reference-style climb height from **1,900 through 2,500**,
calculates a score at least **50,000 above the current local top score**, and submits it to the
localhost mock. You can customize the range with `--min-height` and
`--max-height`, but both values must remain positive and the minimum cannot
exceed the maximum. The `--increment` value cannot be below 50,000. The mock
validates token expiry and one-time use, validates score fields, records the
server-side elapsed run time and millisecond submission timestamp, and returns
the resulting rank. It persists local data in `mock_webcade_state.json`, which
is ignored by Git.

This test path is deliberately restricted to localhost and never sends the
score to Webcade or any public leaderboard.

To provide the identity non-interactively:

```bash
MOCK_IDENTITY=demo-player python mock_score_automation.py \
  --base-url http://127.0.0.1:8080
```

The discovered identity is included in the submission using the field declared
by the requirements response. In the mock API that field is `address`, labelled
“wallet address or username.” A real team site may instead declare `username`,
`wallet`, or another public identifier; the adapter should use the exact field
the team documents.

## Adapting to a team-owned test website

The worker can preserve a different team API’s submission format only when the
team provides its contract. Configure the leaderboard URL, score field, start
endpoint, token JSON path, token field, and any required identity fields from
the team’s documentation. The program must not infer or invent undocumented
fields, bypass a login, or replay a browser credential. A safe integration
should confirm that the team’s start endpoint issues a one-use token and that
the submission endpoint validates it server-side.

At minimum, provide:

```text
GET  leaderboard endpoint and response example
POST run-start endpoint and token response example
POST score endpoint and accepted request example
```

Once those examples are available, the adapter can read the current score,
produce a test value at least 50,000 higher with a separate modified-height
field, and serialize the exact payload shape required by that test site. Until
then, the committed higher-score workflow remains localhost-only.

## Telegram control bot

`telegram_score_bot.py` provides a Telegram interface to an explicitly
authorized team test API or the **local mock API**. It does not send scores to
Webcade. The bot supports:

```text
/start                         show help
/identity <wallet or username> save the public run identity
/status                        read the local leaderboard
/on                            keep running until /off
  /off                           stop automatic runs
  /run                           get a fresh token and submit a local test score
  /schedule <score> <HH:MM:SS> [UTC|LOCAL]  propose a score and exact submission time
  /ack                           acknowledge the proposed scheduled submission
  /cancel                        cancel a pending scheduled submission
  /clear                         remove the saved identity
```

For an authorized site, `/schedule 100000 11:59:59 UTC` creates a proposal without
reading the leaderboard. The bot displays the target score and exact deadline;
reply `/ack` to obtain a fresh server token and schedule the submission, or
`/cancel` to discard it. The deadline uses the bot host's local timezone and is
displayed in `HH:MM:SS` format. Add `UTC` to use Coordinated Universal Time;
omit the timezone or use `LOCAL` for the bot host's local timezone. The bot submits at or just after that wall-clock
time; network latency means no client can guarantee the server receives a
request at the exact same instant. The server remains authoritative about token
validity and minimum elapsed time.

### Run it locally

1. Create a bot with Telegram's official BotFather and copy its token. Never
   commit the token or put it in a public repository.
2. Start the local API in terminal 1:

   ```bash
   cd automated
   set -a; . ./.env; set +a
   python mock_webcade_api.py
   ```

3. Start the Telegram bot in terminal 2:

   ```bash
   cd automated
   set -a; . ./.env; set +a
   python telegram_score_bot.py
   ```

4. Open your Telegram bot and send:

   ```text
   /site https://staging.example.com/game
   /discover
   /inspect
   /identity demo-player
   /on
   ```

The bot asks for the identity through `/identity` before a run. Use only a
public wallet address or a username; never send a seed phrase, private key, or
Telegram bot token in chat. Set `TELEGRAM_ALLOWED_CHAT_ID` after identifying
your private test chat so messages from other chats are ignored.

For a team-owned test website, add only its hostname to
`AUTHORIZED_TEST_DOMAINS` after the team has authorized the integration and
supplied its API contract. Judges can then select it in chat with `/site`, use
`/inspect` to see the declared contract, enter `/identity`, and run `/run`.
The bot should not be pointed at a public competition endpoint.

There is no hard-coded `.com` restriction: any team-provided test hostname can
be onboarded by adding its exact host to `AUTHORIZED_TEST_DOMAINS`, for
example:

```text
AUTHORIZED_TEST_DOMAINS=team-a.com,staging.team-b.net
```

This explicit per-host onboarding is intentional. Accepting every arbitrary
URL would allow a typo or an untrusted chat participant to make the bot send
scores to a public or unrelated service. If the team changes domains, update
the environment variable and restart the bot; no code change is required.

The allowlist compares the URL hostname, not the full URL path. For example,
if the judge sends `/site https://staging.example.com/game`, configure:

```text
AUTHORIZED_TEST_DOMAINS=staging.example.com
```

You may also explicitly allow all subdomains with a wildcard:

```text
AUTHORIZED_TEST_DOMAINS=*.example.com
```

Do not include a path in the allowlist. The bot now accepts plain `.com` hosts,
full `https://` URLs, optional ports, and explicit `*.` subdomain wildcards.
It reports the actual hostname and configured entries when rejecting a site.
Localhost is always allowed for the local mock.

The `/site` URL may include a test path, for example
`https://team-example.com/leaderboard`. Requirements discovery now tries the
path-aware location first (`/leaderboard/requirements`), then common root
locations. The selected path is preserved when the declared leaderboard,
start, and submit endpoints are relative to it. If discovery fails, the bot
reports every URL it tried so the team can provide `REQUIREMENTS_PATH` or the
correct API contract.

`/inspect` now performs detailed contract validation before activation. It
checks the declared endpoint paths, identity field, token field, token JSON
path, score field, height field, leaderboard response shape, and run-token
response shape. `/on` is refused until those checks pass. The start probe only
obtains a token; it never submits a score.

`/discover` performs a read-only scan of the selected page and linked
JavaScript files. It reports likely leaderboard, run-token, and submission URLs
found in strings such as `fetch()` calls. It does not execute the site’s
JavaScript, POST to discovered endpoints, bypass authentication, or infer a
request body. The team must confirm the candidates and publish the requirements
contract before `/on` can submit test scores.

After `/site`, `/inspect`, and `/identity`, send `/on`. The bot then performs
the authorized test run repeatedly at `ACTIVE_INTERVAL_SECONDS` (default
`0.5` seconds) and sends
feedback to Telegram until you send `/off`. An API or validation error pauses
the active mode automatically, and the bot tells you why.

### Reference API compatibility check

The public Dudas Jump API was checked without posting a score. Its leaderboard
response uses `list`, `rank`, `name`, `score`, `height`, `toads`, and `secs`, and
`POST /api/dudas/start` returned a server-issued `{ "token": "..." }`. The
public site does not expose `/api/dudas/requirements`; the client now treats a
404 requirements response as optional and requires an explicit identity field
when a team test API does not publish one. The public score endpoint is not
called by this repository’s test automation.

## Deployment

This is a long-running worker. Railway is configured through `Procfile` and
`railway.toml` to run:

```text
python telegram_score_bot.py
```

### Railway setup

1. In Railway, create a new project and choose **Deploy from GitHub repo**.
2. Select `opeyemiolami486-creator/automated` and deploy the `main` branch.
3. In the service’s **Variables** panel, add:

   ```text
   TELEGRAM_BOT_TOKEN=<BotFather token>
   TELEGRAM_ALLOWED_CHAT_ID=<your private Telegram chat id>
   AUTHORIZED_TEST_DOMAINS=<team test hostname>
ACTIVE_INTERVAL_SECONDS=0.5
MOCK_SCORE_INCREMENT=50000
MOCK_MIN_HEIGHT=1900
MOCK_MAX_HEIGHT=2500
LOG_LEVEL=INFO
```

The client no longer assumes that every run lasts five minutes. After requesting
a run token, it first uses an explicit minimum duration or time factor returned
by the selected test site's start response or requirements contract. If neither
is provided, it derives a conservative seconds-per-height factor from completed
leaderboard runs and waits only for the selected height's allowed duration. A
small `TIMING_SAFETY_MARGIN_SECONDS` (default `0.25`) protects against scheduler
jitter. For a site whose timing contract cannot be discovered automatically,
set `AUTHORIZED_PLAY_DURATION_SECONDS` explicitly; this remains an override for
authorized test sites, not a way to bypass a server's validation.

   Set `MOCK_BASE_URL` only if the team site is using the generic adapter as
   its configured default; judges can otherwise select the site with `/site`.
If the team’s requirements URL is non-standard, also set
   `REQUIREMENTS_PATH` to the actual JSON contract endpoint, for example
   `/dudasjump/play/requirements`. Do not set it to the game page itself such
   as `/dudasjump/play`; if that value is present, the bot normalizes it to
   `/dudasjump/play/requirements`.

4. Confirm the service is a **worker**, not a web service. It does not need a
   public port.
5. Open Railway logs and confirm `Telegram bot active` appears.
6. In Telegram, use `/start`, then `/site`, `/discover`, `/inspect`,
   `/identity`, and `/on`.

Keep all secrets in Railway Variables. Never commit `.env`, the Telegram token,
wallet private keys, seed phrases, or Telegram identity state. Railway may
restart a worker, so the bot keeps its Telegram update offset and chat state in
its local filesystem only for the lifetime of that container; send `/site`,
`/identity`, and `/on` again after a fresh deployment if needed.
