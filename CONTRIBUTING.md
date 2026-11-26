# Contributing to Signal

Thanks for helping. Signal is small on purpose, so a few rules keep it that way.

## Ground rules

- **The Python package is standard library only.** `pyproject.toml` has `dependencies = []`, and it stays
  that way. HTTP goes through `urllib` (see `ask.py`, `gmail_api.py`). `pytest` is the only dev
  dependency.
- **The Chrome extension has no build step and no npm packages.** It's plain JavaScript that Chrome loads
  as-is. Tests use Node's built-in `node:test` runner.
- **Every decision needs an explanation.** If you add a signal, it has to add human-readable reasons to its
  result, the way the existing analyzers do.
- **Never invent numbers.** Benchmark tables in the README come from `signalbox eval` output. If your
  change moves them, paste the new output; don't round in your favour. The synthetic benchmark is not a
  real-inbox result, and the README must keep saying so.
- **Your mail stays on your machine.** Nothing may send message content anywhere except the LLM provider
  the user picked with `--llm`. The extension only ever talks to `127.0.0.1` / `localhost`.

## Setup

```bash
git clone https://github.com/Ishan819/signalbox.git && cd signalbox
python3 -m venv .venv && . .venv/bin/activate      # Python 3.10+
pip install pytest
```

## Run the tests

```bash
python -m pytest -q                         # Python suite
node --test extension/tests/*.test.js       # extension matching logic (Node 18+)
python -m signalbox generate && python -m signalbox eval demo_inbox.json   # benchmark, as CI runs it
```

CI runs all three on Python 3.10–3.13. A PR needs all of them green.

Tests never touch the network or a real Gmail account. Use the injectable seams that already exist:
`GmailClient(request=...)`, `_request(urlopen=..., sleep=...)`, `LLMAskDetector(llm=...)`,
`watch(sleep=..., max_iterations=...)`, and `make_server(port=0)` for the HTTP API.

## Making a change

1. Branch off `main`.
2. Keep each commit focused, and write the message in the imperative ("Add …", "Fix …") with a body that
   explains *why*.
3. Add or update tests with the change, not afterwards.
4. Update the README if you change a command, a flag, or the results table.
5. Never commit credentials or personal mail. `gmail_token.json`, `gmail_cache.json`, `*.labeled.json`,
   `*.mbox` and `me.json` are git-ignored; check `git status` before you commit anyway.

## Reporting a bad triage

The most useful bug report is a message that landed in the wrong tier. Include the `why` line from
`signalbox triage --json` and the signal scores. Redact or paraphrase the content; never paste someone
else's private mail into an issue.
