# Signal

**Out of 100 messages, 90 come from bots, AI agents, and automations. Signal finds the 2 that actually need you.**

![Signal UI](docs/signal_ui.png)

## The idea

Most "AI vs. human" filters try to detect AI-*written text*. That approach is unreliable, and it gets the
wrong answer anyway: your sister may draft an email with AI, and that email still needs you. Signal asks a
different question:

> **Does this message need *me*, and is it from someone I actually have a relationship with?**

It combines three independent signals, and every decision comes with an explanation:

| Signal | What it uses | Why it's hard to fake |
|---|---|---|
| **Automation** | `List-Unsubscribe`, `Auto-Submitted`, `Precedence`, bulk-platform headers, `noreply@` senders, Slack `bot_id`, clockwork send cadence, one-way high-volume senders, and **cross-inbox template detection** (MinHash + LSH), which catches "personalized" AI sales agents that send no bulk headers | The checks run on transport metadata and patterns across your whole inbox, not on one message's wording |
| **Relationship** | Built only from **your own behaviour**: who you write to, how often, how recently, which threads you're in, and which orgs your contacts belong to | An agent can't make you have emailed it 14 times |
| **Ask** | Is it directed at you (To vs. CC vs. list, @mention, DM), does it contain a question or request, is there a deadline? Uses heuristics offline, or an **LLM** (Anthropic or Gemini) | The LLM only runs on messages that aren't already obviously automated, which keeps costs low |

```
priority = (1 - automation)^1.5 × (0.12 + 0.53·ask + 0.35·relationship)
```

## Results (synthetic benchmark: 186 inbound messages, 8 actually important)

| signals used | P@2 | R@5 | R@10 | avg precision | flag precision | flag recall |
|---|---|---|---|---|---|---|
| automation only | 0.00 | 0.00 | 0.12 | 0.30 | 0.44 | 0.88 |
| + relationship | 0.00 | 0.12 | 0.62 | 0.47 | 0.57 | 1.00 |
| **+ ask (full)** | **1.00** | **0.62** | **1.00** | **0.99** | **0.89** | **1.00** |
| learned weights (4-fold CV) | 1.00 | 0.62 | 1.00 | 0.99 | 0.67 | 1.00 |

Ties are broken pessimistically, so a configuration can't look good just because of the order of equal scores.

**Takeaways.** Automation signals alone can remove the bots, but they can't tell your sister's "lol look at
this cat" apart from her "are you coming home for Thanksgiving? need to book by Tuesday". You need all three
signals.

**Agents acting for people you know.** "Hi, I'm Priya Shah's AI assistant…" looks like a bot, but it's
really Priya. Signal ties the agent back to Priya in the relationship graph and softens the automation
penalty, so this message still lands in "needs you".

**Learned weights** match the hand-set formula on ranking. They raise more false alarms because the model
weights the rare important class up. On a real inbox with more labels, use `signalbox tune` to pick the cut-off.

**Known failure (kept in on purpose):** a one-off, hand-written AI outreach email with no headers, no
template twins and no disclosure ("Rex Morgan") still gets flagged by the offline heuristics. `--llm`
mode is designed to catch this case.

> ⚠️ This benchmark is synthetic, and the weights were tuned on it. The real test is your own inbox: import it,
> label about 300 messages in the UI, and run `eval`. Report *those* numbers.

## Quick start (no dependencies, Python 3.10+)

```bash
python -m signalbox generate --out demo_inbox.json   # labeled demo inbox
python -m signalbox serve demo_inbox.json            # http://127.0.0.1:8000
python -m signalbox eval demo_inbox.json             # metrics + ablation table
python -m signalbox triage demo_inbox.json --top 10  # terminal view (add --json for scripts)
python -m signalbox digest demo_inbox.json           # short Markdown summary of what needs you
python -m signalbox stats demo_inbox.json            # who floods your inbox (unsubscribe candidates)
python -m signalbox eval demo_inbox.json --learn     # also fit weights on your labels
python -m signalbox tune demo_inbox.json             # suggest a needs-you threshold
python -m pytest -q                                  # tests
```

**Overrides:** `--vip boss@corp.com` or `--vip @corp.com` always surfaces a sender or a whole domain;
`--mute` always buries one. Both work with `triage`, `digest`, `eval` and `serve`.

**In the UI:** <kbd>/</kbd> search, <kbd>j</kbd>/<kbd>k</kbd> move, <kbd>y</kbd> needs me, <kbd>n</kbd> doesn't.

### Use your real data

```bash
# Gmail: takeout.google.com → Mail → .mbox (include Sent so the relationship graph works)
python -m signalbox import-mbox All\ mail.mbox --me you@gmail.com --name "Your Name" --out me.json --limit 3000

# Slack: workspace export folder; --me is your Slack user id (U0123...)
python -m signalbox import-slack ./slack-export --me U0123ABC --name "Your Name" --out slack.json

python -m signalbox serve me.json          # click "needs me" / "doesn't" to label → builds your eval set
python -m signalbox eval me.json
```

### LLM mode

```bash
export ANTHROPIC_API_KEY=...   # or GEMINI_API_KEY=...
python -m signalbox eval me.json --llm anthropic      # or --llm gemini
# optional: SIGNAL_MODEL=<model name> to override the default model
```

Results are cached in `.signal_cache.json`. If the LLM fails, Signal falls back to the heuristics, so
triage never goes down.

## Layout

```
signalbox/
  automation.py    signal 1: headers, bot flags, cadence, MinHash/LSH template detection
  relationship.py  signal 2: graph built from your sent mail + threads, agent → contact linking
  ask.py           signal 3: heuristic + LLM ask detection (Anthropic / Gemini over HTTPS)
  scorer.py        combines the signals → score, tier, one-line explanation
  evaluate.py      P@k, R@k, average precision, flag precision/recall, ablation, threshold tuning
  learn.py         pure-Python logistic regression, k-fold cross-validation
  digest.py        daily summary of what needs you
  stats.py         noisiest senders and which automation signals fire
  synth.py         labeled synthetic inbox with deliberate hard cases
  loaders.py       Signal JSON, mbox (Gmail Takeout), Slack export
  server.py        zero-dependency web UI with one-click labeling
```

## Roadmap

- [x] Learn the weights from labels (logistic regression, cross-validated)
- [x] Link AI assistants back to the contact they act for
- [x] Daily digest, inbox stats, VIP/mute lists, threshold tuning
- [ ] **Gmail live mode**: Gmail API + OAuth, apply a `Needs-You` label every 10 minutes
- [ ] **Chrome extension**: highlight these messages directly inside Gmail and Slack web
- [ ] Benchmark on a real, hand-labeled inbox and publish those numbers
