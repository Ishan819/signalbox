"""Keep a local Signal cache in sync with Gmail, and label the messages that need you.

sync_once() does one pass: pull recent inbox + sent mail into a local Signal JSON cache
(sent mail builds the relationship graph, same as import-mbox), score the whole cache, and
apply a "Needs-You" Gmail label to newly-seen inbox messages that score in that tier.

watch() repeats that forever on an interval (default 10 minutes), the way a real Gmail live
mode runs: a sync error is reported but never kills the loop, same as the LLM-unavailable
fallback in ask.py — a bad cycle just gets retried next time around.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .gmail_api import GmailClient, ensure_label, message_from_gmail
from .loaders import load_json, save_json
from .models import Identity, Message
from .scorer import Triager

LABEL_NAME = "Needs-You"


def _load_cache(path: Path, name: str, me_address: str) -> tuple[Identity, dict[str, Message]]:
    if path.exists():
        me, msgs = load_json(path)
        me.addresses.add(me_address)
    else:
        me, msgs = Identity(name, {me_address}), []
    return me, {m.id: m for m in msgs}


def _state_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(".labeled.json")


def _load_labeled_ids(path: Path) -> set[str]:
    return set(json.loads(path.read_text())) if path.exists() else set()


def _save_labeled_ids(path: Path, ids: set[str]) -> None:
    path.write_text(json.dumps(sorted(ids)))


def sync_once(client: GmailClient, cache_path: str | Path, name: str = "Me", limit: int = 50,
             query: str = "newer_than:7d", dry_run: bool = False) -> list[Message]:
    """One sync pass. Returns the inbox messages newly labeled "Needs-You" this run."""
    cache_path = Path(cache_path)
    state_path = _state_path(cache_path)

    me_address = client.profile()["emailAddress"]
    me, by_id = _load_cache(cache_path, name, me_address)
    labeled_ids = _load_labeled_ids(state_path)

    fetched_inbox_ids: set[str] = set()
    for gmail_query, is_inbox in ((f"in:inbox {query}", True), (f"in:sent {query}", False)):
        for msg_id in client.list_message_ids(gmail_query, max_results=limit):
            if is_inbox:
                fetched_inbox_ids.add(msg_id)
            if msg_id not in by_id:
                by_id[msg_id] = message_from_gmail(client.get_message(msg_id))

    save_json(cache_path, me, list(by_id.values()))

    ranked = {t.message.id: t for t in Triager(me, list(by_id.values())).triage()}
    newly_labeled: list[Message] = []
    label_id = None
    for msg_id in fetched_inbox_ids - labeled_ids:
        triage = ranked.get(msg_id)
        if triage is None or triage.tier != "needs_you":
            continue
        if not dry_run:
            label_id = label_id or ensure_label(client, LABEL_NAME)
            client.modify_message(msg_id, add_label_ids=[label_id])
        labeled_ids.add(msg_id)
        newly_labeled.append(triage.message)

    if not dry_run:
        _save_labeled_ids(state_path, labeled_ids)
    return newly_labeled


def watch(client_factory, cache_path: str | Path, interval: float = 600, max_iterations: int | None = None,
         on_sync=None, sleep=time.sleep, **sync_kwargs) -> None:
    """Call sync_once every `interval` seconds, forever (or `max_iterations` times, for tests).

    `client_factory` is called fresh each cycle so it can hand back a GmailClient built from a
    just-refreshed access token. `on_sync(newly_labeled, error)` is called after every cycle,
    win or lose, so a caller (the CLI) can report progress without the loop depending on it.
    """
    i = 0
    while max_iterations is None or i < max_iterations:
        error = newly_labeled = None
        try:
            newly_labeled = sync_once(client_factory(), cache_path, **sync_kwargs)
        except Exception as exc:
            error = exc
        if on_sync:
            on_sync(newly_labeled or [], error)
        i += 1
        if max_iterations is None or i < max_iterations:
            sleep(interval)
