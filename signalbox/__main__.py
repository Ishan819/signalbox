"""Command line: python -m signalbox <command> ..."""
from __future__ import annotations

import argparse
import json
import sys

from .ask import LLMAskDetector
from .evaluate import evaluate, format_table
from .loaders import load_json, load_mbox, load_slack_export, save_json
from .models import Identity
from .scorer import MODES, Triager


def _detector(args):
    return LLMAskDetector(args.llm) if getattr(args, "llm", None) else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="signalbox", description="Find the messages that actually need you.")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="write a labeled synthetic demo inbox")
    g.add_argument("--out", default="demo_inbox.json")
    g.add_argument("--seed", type=int, default=7)

    for name, help_ in [("triage", "print the ranked inbox"), ("eval", "score against labels + ablation"),
                        ("serve", "open the web UI"), ("digest", "short Markdown summary of what needs you"),
                        ("stats", "who floods your inbox and why"),
                        ("tune", "suggest a needs_you threshold from your labels")]:
        s = sub.add_parser(name, help=help_)
        s.add_argument("data", help="Signal JSON file")
        s.add_argument("--llm", choices=["anthropic", "gemini"], help="use an LLM for ask detection")
        s.add_argument("--vip", action="append", default=[], help="always surface this sender or @domain")
        s.add_argument("--mute", action="append", default=[], help="always bury this sender or @domain")
        if name == "triage":
            s.add_argument("--mode", choices=MODES, default="full")
            s.add_argument("--top", type=int, default=15)
            s.add_argument("--json", action="store_true", help="machine-readable output")
        if name == "serve":
            s.add_argument("--port", type=int, default=8000)
        if name == "eval":
            s.add_argument("--learn", action="store_true",
                           help="also fit weights on your labels (reported with 4-fold cross-validation)")

    im = sub.add_parser("import-mbox", help="convert an mbox (e.g. Gmail Takeout) to Signal JSON")
    im.add_argument("mbox")
    im.add_argument("--me", required=True, action="append", help="your address (repeatable)")
    im.add_argument("--name", default="Me")
    im.add_argument("--out", default="inbox.json")
    im.add_argument("--limit", type=int)

    isl = sub.add_parser("import-slack", help="convert a Slack export folder to Signal JSON")
    isl.add_argument("folder")
    isl.add_argument("--me", required=True, action="append", help="your Slack user id (repeatable)")
    isl.add_argument("--name", default="Me")
    isl.add_argument("--out", default="slack.json")

    ga = sub.add_parser("gmail-auth", help="authorize Signal to read and label your Gmail (OAuth)")
    ga.add_argument("--client-id", required=True)
    ga.add_argument("--client-secret", required=True)
    ga.add_argument("--token", default="gmail_token.json")
    ga.add_argument("--port", type=int, default=8765)

    gs = sub.add_parser("gmail-sync", help="apply a Needs-You Gmail label to what needs you (live mode)")
    gs.add_argument("--token", default="gmail_token.json")
    gs.add_argument("--cache", default="gmail_cache.json")
    gs.add_argument("--name", default="Me")
    gs.add_argument("--limit", type=int, default=50, help="max messages to fetch per side (inbox/sent)")
    gs.add_argument("--query", default="newer_than:7d", help="Gmail search filter for what's \"recent\"")
    gs.add_argument("--dry-run", action="store_true", help="score and report, don't touch Gmail labels")
    gs.add_argument("--watch", action="store_true", help="keep syncing every --interval seconds")
    gs.add_argument("--interval", type=int, default=600)

    args = p.parse_args(argv)

    if args.cmd == "generate":
        from .synth import generate
        me, msgs = generate(args.seed)
        save_json(args.out, me, msgs)
        print(f"wrote {len(msgs)} messages to {args.out}")
    elif args.cmd == "triage":
        me, msgs = load_json(args.data)
        ranked = Triager(me, msgs, mode=args.mode, ask_detector=_detector(args),
                         vip=set(args.vip), mute=set(args.mute)).triage()[:args.top]
        if args.json:
            print(json.dumps([t.to_dict() for t in ranked], indent=2))
            return 0
        for t in ranked:
            flag = {"needs_you": "●", "worth_a_look": "○", "low": " "}[t.tier]
            print(f"{flag} {t.score:.2f}  {(t.message.sender_name or t.message.sender)[:22]:<22} "
                  f"{t.message.subject[:32]:<32}  {t.why[:90]}")
    elif args.cmd == "eval":
        me, msgs = load_json(args.data)
        if not any(m.label is not None for m in msgs):
            print("No labels yet — label some messages in `serve` first.", file=sys.stderr)
            return 1
        res = evaluate(me, msgs, ask_detector=_detector(args))
        if args.learn:
            from .evaluate import evaluate_learned
            from .learn import fit_all
            res["learned (4-fold CV)"] = evaluate_learned(me, msgs, ask_detector=_detector(args))
        print(format_table(res))
        if args.learn:
            model = fit_all(Triager(me, msgs, ask_detector=_detector(args)).triage())
            print("\nLearned weights (fit on all labels):\n" + model.explain())
        full = res["full"]
        for t in full["misses"]:
            print(f"  MISSED      {t.score:.2f} {t.message.sender_name}: {t.message.subject}")
        for t in full["false_alarms"]:
            print(f"  FALSE ALARM {t.score:.2f} {t.message.sender_name}: {t.message.subject}  ({t.why[:70]})")
    elif args.cmd == "digest":
        from .digest import build_digest
        me, msgs = load_json(args.data)
        results = Triager(me, msgs, ask_detector=_detector(args), vip=set(args.vip), mute=set(args.mute)).triage()
        print(build_digest(results, me.name))
    elif args.cmd == "tune":
        from .evaluate import best_thresholds
        from .scorer import NEEDS_YOU
        me, msgs = load_json(args.data)
        ranked = [t for t in Triager(me, msgs, ask_detector=_detector(args)).triage() if t.message.label is not None]
        if not any(t.message.label for t in ranked):
            print("Label some messages first (signalbox serve).", file=sys.stderr)
            return 1
        for name, (th, p, r) in best_thresholds([t.score for t in ranked], [bool(t.message.label) for t in ranked]).items():
            print(f"{name:<18} threshold {th:.2f}  precision {p:.2f}  recall {r:.2f}")
        print(f"current            threshold {NEEDS_YOU:.2f}  (signalbox/scorer.py: NEEDS_YOU)")
    elif args.cmd == "stats":
        from .stats import inbox_stats
        me, msgs = load_json(args.data)
        print(inbox_stats(Triager(me, msgs, vip=set(args.vip), mute=set(args.mute)).triage()))
    elif args.cmd == "serve":
        from .server import serve
        serve(args.data, port=args.port, ask_detector=_detector(args), vip=set(args.vip), mute=set(args.mute))
    elif args.cmd == "import-mbox":
        me = Identity(args.name, set(args.me))
        msgs = load_mbox(args.mbox, me, limit=args.limit)
        save_json(args.out, me, msgs)
        print(f"wrote {len(msgs)} messages to {args.out}")
    elif args.cmd == "import-slack":
        me = Identity(args.name, set(args.me))
        msgs = load_slack_export(args.folder, me)
        save_json(args.out, me, msgs)
        print(f"wrote {len(msgs)} messages to {args.out}")
    elif args.cmd == "gmail-auth":
        from . import gmail_auth
        token = gmail_auth.run_oauth_flow(args.client_id, args.client_secret, port=args.port)
        gmail_auth.save_token(args.token, token)
        print(f"Authorized. Token saved to {args.token} -- now run `signalbox gmail-sync --watch`.")
    elif args.cmd == "gmail-sync":
        from . import gmail_auth, gmail_sync
        from .gmail_api import GmailClient
        token = gmail_auth.load_token(args.token)
        if token is None:
            print(f"No token at {args.token}. Run `signalbox gmail-auth` first.", file=sys.stderr)
            return 1

        def client_factory():
            nonlocal token
            access_token, token = gmail_auth.valid_access_token(token)
            gmail_auth.save_token(args.token, token)
            return GmailClient(access_token)

        def report(newly_labeled, error):
            if error:
                print(f"sync failed: {error}", file=sys.stderr)
            for m in newly_labeled:
                print(f"needs-you: {(m.sender_name or m.sender)[:30]:<30} {m.subject[:60]}")

        sync_kwargs = dict(name=args.name, limit=args.limit, query=args.query, dry_run=args.dry_run)
        if args.watch:
            gmail_sync.watch(client_factory, args.cache, interval=args.interval, on_sync=report, **sync_kwargs)
        else:
            report(gmail_sync.sync_once(client_factory(), args.cache, **sync_kwargs), None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
