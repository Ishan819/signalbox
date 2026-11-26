# CLAUDE.md

## What Signal does

Signal (`signalbox`) ranks an inbox (email or Slack) by whether each message needs *you*. It does not
try to detect AI-written text. It combines three explainable signals:

- **automation** (`automation.py`): headers, bot flags, send cadence, MinHash/LSH template detection
  across the whole inbox
- **relationship** (`relationship.py`): a graph built only from the mail you sent
- **ask** (`ask.py`): is it directed at you, is there a question, request or deadline? Heuristic
  offline, or an LLM with `--llm`

`scorer.py` combines them into a score, a tier (`needs_you` / `worth_a_look` / `low`) and a one-line
reason. Inputs are an mbox, a Slack export, or live Gmail (`gmail-sync`). Outputs are the CLI, the web
UI, a `Needs-You` Gmail label, and the Chrome extension that highlights rows in Gmail web.

## Commands

```bash
python -m pytest -q                                   # Python tests
node --test extension/tests/*.test.js                 # extension tests (Node's built-in runner)
python -m signalbox generate --out demo_inbox.json    # labeled synthetic inbox
python -m signalbox eval demo_inbox.json --learn      # benchmark table, incl. learned weights (4-fold CV)
python -m signalbox serve demo_inbox.json             # web UI + /api on http://127.0.0.1:8000
python -m signalbox gmail-sync --dry-run              # score live Gmail without touching labels (needs gmail-auth first)
```

## Layout

```
signalbox/
  __main__.py      CLI (argparse); every subcommand is dispatched in _run()
  models.py        Message, Identity
  loaders.py       Signal JSON (atomic save), mbox, Slack export; InvalidDataError
  automation.py    signal 1
  relationship.py  signal 2
  ask.py           signal 3 (heuristic + LLM over plain HTTPS)
  scorer.py        Triager: combine, VIP/mute, tiers (NEEDS_YOU, WORTH_A_LOOK), explanations
  evaluate.py      metrics, ablation, threshold tuning
  learn.py         pure-Python logistic regression
  digest.py        daily summary
  stats.py         noisy-sender report
  synth.py         synthetic benchmark inbox
  server.py        make_server()/serve(): web UI, /api/triage, /api/label, /api/needs-you
  gmail_auth.py    OAuth (loopback + PKCE), token refresh, GmailAuthError
  gmail_api.py     Gmail REST client with retries; Gmail message -> Message
  gmail_sync.py    sync_once() and watch(): cache, score, apply Needs-You label
extension/         Chrome extension, Manifest V3, plain JS, no build step
  match.js         pure id-matching logic (the only part with tests)
  tests/           node:test tests for match.js
tests/             pytest suite
docs/              screenshot used by the README
```

## Rules

- **Standard library only** for the Python package. `dependencies = []` in `pyproject.toml` stays empty;
  HTTP goes through `urllib`. `pytest` is the only dev dependency. The extension uses no npm packages.
- **Python 3.10+.** CI runs 3.10–3.13, so don't use syntax or stdlib APIs newer than 3.10.
- **Every feature gets a test.** Tests never hit the network or a real account; use the existing seams
  (`GmailClient(request=...)`, `_request(urlopen=..., sleep=...)`, `LLMAskDetector(llm=...)`,
  `watch(sleep=..., max_iterations=...)`, `make_server(port=0)`).
- **Update the README results table whenever benchmark numbers change.** Copy them from
  `python -m signalbox eval demo_inbox.json --learn` output.
- **Never invent numbers.** Every figure in the README or a commit message must come from real command
  output. The benchmark is synthetic; never present it as real-inbox results.
- **Commit messages end with:**

  ```
  Co-Authored-By: Claude <noreply@anthropic.com>
  ```

- Never commit credentials or mail: `gmail_token.json`, `gmail_cache.json`, `*.labeled.json`,
  `*.corrupt-*`, `*.mbox`, `me.json` and `slack.json` are git-ignored. Check before committing.
