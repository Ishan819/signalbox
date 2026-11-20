"""Load messages from Signal JSON, an mbox file (Gmail Takeout), or a Slack export."""
from __future__ import annotations

import json
import mailbox
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path

from .models import Identity, Message


# ---------------------------------------------------------------- Signal JSON
def load_json(path: str | Path) -> tuple[Identity, list[Message]]:
    data = json.loads(Path(path).read_text())
    me = Identity(data["me"]["name"], set(data["me"]["addresses"]))
    return me, [Message.from_dict(m) for m in data["messages"]]


def save_json(path: str | Path, me: Identity, messages: list[Message]) -> None:
    payload = {"me": me.to_dict(), "messages": [m.to_dict() for m in messages]}
    Path(path).write_text(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------- mbox
def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _plain_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", "replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", "replace")


def load_mbox(path: str | Path, me: Identity, limit: int | None = None) -> list[Message]:
    """Read a Gmail Takeout / any mbox file. Sent mail in the same file builds the relationship graph."""
    out: list[Message] = []
    for i, msg in enumerate(mailbox.mbox(str(path))):
        if limit and i >= limit:
            break
        name, addr = parseaddr(_decode(msg.get("From")))
        try:
            ts = parsedate_to_datetime(msg.get("Date"))
        except Exception:
            ts = datetime.now(timezone.utc)
        refs = (msg.get("References") or msg.get("In-Reply-To") or "").split()
        thread = msg.get("X-GM-THRID") or (refs[0] if refs else msg.get("Message-ID"))
        out.append(Message(
            id=msg.get("Message-ID") or f"mbox-{i}",
            channel="email",
            sender=addr,
            sender_name=name,
            recipients=[a for _, a in getaddresses(msg.get_all("To", []))],
            cc=[a for _, a in getaddresses(msg.get_all("Cc", []))],
            subject=_decode(msg.get("Subject")),
            body=_plain_body(msg)[:5000],
            timestamp=ts,
            headers={k: _decode(v) for k, v in msg.items() if k.lower() not in {"received", "dkim-signature"}},
            thread_id=thread,
        ))
    return out


# --------------------------------------------------------------- Slack export
_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")


def load_slack_export(directory: str | Path, me: Identity) -> list[Message]:
    """Read a standard Slack workspace export (one folder per channel, one JSON file per day)."""
    root = Path(directory)
    dm_members: dict[str, list[str]] = {}
    for meta in ("dms.json", "mpims.json"):
        if (root / meta).exists():
            for conv in json.loads((root / meta).read_text()):
                dm_members[conv.get("id") or conv.get("name")] = [m.lower() for m in conv.get("members", [])]

    out: list[Message] = []
    for chan_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        members = dm_members.get(chan_dir.name)
        for day in sorted(chan_dir.glob("*.json")):
            for raw in json.loads(day.read_text()):
                if raw.get("type") != "message":
                    continue
                sender = raw.get("user") or raw.get("bot_id") or "unknown"
                text = raw.get("text", "")
                out.append(Message(
                    id=f"{chan_dir.name}-{raw.get('ts')}",
                    channel="slack",
                    sender=sender,
                    sender_name=(raw.get("user_profile") or {}).get("real_name", "") or raw.get("username", ""),
                    recipients=[m for m in (members or []) if m != sender.lower()],
                    subject=f"#{chan_dir.name}" if members is None else "DM",
                    body=text,
                    timestamp=float(raw.get("ts", 0)),
                    is_bot=bool(raw.get("bot_id")) or raw.get("subtype") == "bot_message",
                    mentions=_MENTION.findall(text),
                    thread_id=raw.get("thread_ts"),
                ))
    return out
