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
                        ("stats", "who floods your inbox and why")]:
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
        print(format_table(res))
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
    elif args.cmd == "stats":
        from .stats import inbox_stats
        me, msgs = load_json(args.data)
        print(inbox_stats(Triager(me, msgs, vip=set(args.vip), mute=set(args.mute)).triage()))
    elif args.cmd == "serve":
        from .server import serve
        serve(args.data, port=args.port, ask_detector=_detector(args))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
