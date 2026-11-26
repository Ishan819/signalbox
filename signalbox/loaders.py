"""Load messages from Signal JSON, an mbox file (Gmail Takeout), or a Slack export."""
from __future__ import annotations

import json
import mailbox
import os
import re
import tempfile
import warnings
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path

from .models import Identity, Message


class InvalidDataError(ValueError):
    """An input file exists but isn't what Signal expected. The message names the file."""


# ---------------------------------------------------------------- Signal JSON
def load_json(path: str | Path) -> tuple[Identity, list[Message]]:
    """Read a Signal JSON file. Raises ValueError (naming the file) if it isn't one."""
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        raise InvalidDataError(f"{path} is not valid JSON: {exc}") from exc
    try:
        me = Identity(data["me"]["name"], set(data["me"]["addresses"]))
        raw_messages = data["messages"]
    except (KeyError, TypeError) as exc:
        raise InvalidDataError(f"{path} is not a Signal JSON file (needs \"me\": {{name, addresses}} "
                         f"and \"messages\": [...])") from exc
    if not isinstance(raw_messages, list):
        raise InvalidDataError(f"{path}: \"messages\" must be a list")
    messages = []
    for i, m in enumerate(raw_messages):
        try:
            messages.append(Message.from_dict(m))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise InvalidDataError(f"{path}: message #{i} is malformed ({exc})") from exc
    return me, messages


def atomic_write_text(path: str | Path, text: str) -> None:
    """Write via a temp file + rename, so a crash mid-write never leaves a truncated file."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent or ".", prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_json(path: str | Path, me: Identity, messages: list[Message]) -> None:
    payload = {"me": me.to_dict(), "messages": [m.to_dict() for m in messages]}
    atomic_write_text(path, json.dumps(payload, indent=2))


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
    """Read a Gmail Takeout / any mbox file. Sent mail in the same file builds the relationship graph.

    An empty file gives []. A missing path raises FileNotFoundError (mailbox.mbox would otherwise
    silently create an empty file there). A message that can't be parsed is skipped with a warning.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no mbox file at {path}")
    if not path.is_file():
        raise IsADirectoryError(f"{path} is a directory, not an mbox file")
    out: list[Message] = []
    skipped = 0
    for i, msg in enumerate(mailbox.mbox(str(path), create=False)):
        if limit and i >= limit:
            break
        try:
            out.append(_mbox_message(msg, i))
        except Exception:
            skipped += 1
    if skipped:
        warnings.warn(f"{path}: skipped {skipped} message(s) that couldn't be parsed", stacklevel=2)
    return out


def _mbox_message(msg, i: int) -> Message:
    name, addr = parseaddr(_decode(msg.get("From")))
    try:
        ts = parsedate_to_datetime(msg.get("Date"))
    except Exception:
        ts = None
    if ts is None:
        ts = datetime.now(timezone.utc)
    refs = str(msg.get("References") or msg.get("In-Reply-To") or "").split()
    thread = msg.get("X-GM-THRID") or (refs[0] if refs else msg.get("Message-ID"))
    return Message(
        id=str(msg.get("Message-ID") or f"mbox-{i}"),
        channel="email",
        sender=addr,
        sender_name=name,
        recipients=[a for _, a in getaddresses([str(v) for v in msg.get_all("To", [])])],
        cc=[a for _, a in getaddresses([str(v) for v in msg.get_all("Cc", [])])],
        subject=_decode(msg.get("Subject")),
        body=_plain_body(msg)[:5000],
        timestamp=ts,
        headers={k: _decode(v) for k, v in msg.items() if k.lower() not in {"received", "dkim-signature"}},
        thread_id=str(thread) if thread else None,
    )


# --------------------------------------------------------------- Slack export
_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")


def _read_json_list(path: Path, problems: list[str]) -> list:
    """A Slack export JSON file that should hold a list; anything else is noted and treated as []."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        problems.append(f"{path.name}: unreadable ({type(exc).__name__})")
        return []
    if not isinstance(data, list):
        problems.append(f"{path.name}: expected a JSON list, got {type(data).__name__}")
        return []
    return data


def load_slack_export(directory: str | Path, me: Identity) -> list[Message]:
    """Read a standard Slack workspace export (one folder per channel, one JSON file per day).

    Exports are often hand-edited or truncated, so a bad day file, a broken dms.json, or a
    message with no usable timestamp is skipped (and summarized in one warning) rather than
    failing the whole import. A missing folder raises FileNotFoundError.
    """
    root = Path(directory)
    if not root.exists():
        raise FileNotFoundError(f"no Slack export folder at {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is a file; point import-slack at the unzipped export folder")

    problems: list[str] = []
    dm_members: dict[str, list[str]] = {}
    for meta in ("dms.json", "mpims.json"):
        if (root / meta).exists():
            for conv in _read_json_list(root / meta, problems):
                if not isinstance(conv, dict) or not (conv.get("id") or conv.get("name")):
                    continue
                members = conv.get("members") if isinstance(conv.get("members"), list) else []
                dm_members[conv.get("id") or conv.get("name")] = [str(m).lower() for m in members]

    out: list[Message] = []
    bad_messages = 0
    for chan_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        members = dm_members.get(chan_dir.name)
        for day in sorted(chan_dir.glob("*.json")):
            for raw in _read_json_list(day, problems):
                if not isinstance(raw, dict) or raw.get("type") != "message":
                    continue
                try:
                    ts = float(raw["ts"])
                except (KeyError, TypeError, ValueError):
                    bad_messages += 1
                    continue
                sender = str(raw.get("user") or raw.get("bot_id") or "unknown")
                text = raw.get("text")
                text = text if isinstance(text, str) else ""
                profile = raw.get("user_profile") if isinstance(raw.get("user_profile"), dict) else {}
                out.append(Message(
                    id=f"{chan_dir.name}-{raw['ts']}",
                    channel="slack",
                    sender=sender,
                    sender_name=str(profile.get("real_name") or raw.get("username") or ""),
                    recipients=[m for m in (members or []) if m != sender.lower()],
                    subject=f"#{chan_dir.name}" if members is None else "DM",
                    body=text,
                    timestamp=ts,
                    is_bot=bool(raw.get("bot_id")) or raw.get("subtype") == "bot_message",
                    mentions=_MENTION.findall(text),
                    thread_id=str(raw["thread_ts"]) if raw.get("thread_ts") else None,
                ))
    if bad_messages:
        problems.append(f"{bad_messages} message(s) without a valid ts")
    if problems:
        warnings.warn(f"{root}: skipped malformed parts of the Slack export: " + "; ".join(problems[:10])
                      + (f" (+{len(problems) - 10} more)" if len(problems) > 10 else ""), stacklevel=2)
    return out
