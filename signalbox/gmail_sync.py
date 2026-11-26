"""Keep a local Signal cache in sync with Gmail, and label the messages that need you.

sync_once() does one pass: pull recent inbox + sent mail into a local Signal JSON cache
(sent mail builds the relationship graph, same as import-mbox), score the whole cache, and
apply a "Needs-You" Gmail label to newly-seen inbox messages that score in that tier.

watch() repeats that forever on an interval (default 10 minutes), the way a real Gmail live
mode runs: a sync error is reported but never kills the loop, same as the LLM-unavailable
fallback in ask.py — a bad cycle just gets retried next time around. The one exception is
GmailAuthError (revoked or rejected credentials): no amount of retrying fixes that, so watch()
stops and tells you to run gmail-auth again.

Problems that only affect part of a sync (one message that won't fetch or parse, one label that
won't apply, a corrupt cache file) are reported through `on_problem` and the rest carries on.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from .gmail_api import GmailAPIError, GmailClient, ensure_label, message_from_gmail
from .gmail_auth import GmailAuthError
from .loaders import atomic_write_text, load_json, save_json
from .models import Identity, Message
from .scorer import Triager

LABEL_NAME = "Needs-You"


def _set_aside(path: Path, on_problem, why: str) -> None:
    """Move a corrupt state file out of the way (kept for inspection) so the sync can rebuild it."""
    backup = path.with_name(f"{path.name}.corrupt-{datetime.now():%Y%m%d-%H%M%S}")
    path.replace(backup)
    _report(on_problem, f"{path} was unreadable ({why}); moved to {backup.name} and starting fresh")


def _report(on_problem, text: str) -> None:
    if on_problem:
        on_problem(text)


def _load_cache(path: Path, name: str, me_address: str, on_problem=None) -> tuple[Identity, dict[str, Message]]:
    if path.exists():
        try:
            me, msgs = load_json(path)
            me.addresses.add(me_address)
            return me, {m.id: m for m in msgs}
        except ValueError as exc:
            _set_aside(path, on_problem, str(exc))
    return Identity(name, {me_address}), {}


def _state_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(".labeled.json")


def _load_labeled_ids(path: Path, on_problem=None) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
            raise ValueError("expected a JSON list of message ids")
        return set(data)
    except ValueError as exc:
        _set_aside(path, on_problem, str(exc))
        return set()


def _save_labeled_ids(path: Path, ids: set[str]) -> None:
    atomic_write_text(path, json.dumps(sorted(ids)))


def sync_once(client: GmailClient, cache_path: str | Path, name: str = "Me", limit: int = 50,
             query: str = "newer_than:7d", dry_run: bool = False, on_problem=None) -> list[Message]:
    """One sync pass. Returns the inbox messages newly labeled "Needs-You" this run.

    Raises GmailAuthError / GmailAPIError only when nothing useful can be done (bad credentials,
    or Gmail unreachable for the profile/list calls). Per-message failures go to `on_problem`.
    """
    cache_path = Path(cache_path)
    state_path = _state_path(cache_path)

    me_address = client.profile()["emailAddress"]
    me, by_id = _load_cache(cache_path, name, me_address, on_problem)
    labeled_ids = _load_labeled_ids(state_path, on_problem)

    fetched_inbox_ids: set[str] = set()
    for gmail_query, is_inbox in ((f"in:inbox {query}", True), (f"in:sent {query}", False)):
        for msg_id in client.list_message_ids(gmail_query, max_results=limit):
            if msg_id not in by_id:
                try:
                    by_id[msg_id] = message_from_gmail(client.get_message(msg_id))
                except GmailAuthError:
                    raise
                except (GmailAPIError, ValueError, KeyError, TypeError) as exc:
                    _report(on_problem, f"skipped message {msg_id}: {exc}")
                    continue          # not cached, so the next sync tries it again
            if is_inbox:
                fetched_inbox_ids.add(msg_id)

    save_json(cache_path, me, list(by_id.values()))

    ranked = {t.message.id: t for t in Triager(me, list(by_id.values())).triage()}
    newly_labeled: list[Message] = []
    label_id = None
    for msg_id in sorted(fetched_inbox_ids - labeled_ids):
        triage = ranked.get(msg_id)
        if triage is None or triage.tier != "needs_you":
            continue
        if not dry_run:
            try:
                label_id = label_id or ensure_label(client, LABEL_NAME)
                client.modify_message(msg_id, add_label_ids=[label_id])
            except GmailAuthError:
                raise
            except GmailAPIError as exc:
                _report(on_problem, f"couldn't label {msg_id} ({triage.message.subject!r}): {exc}")
                continue          # not recorded as labeled, so the next sync retries it
            labeled_ids.add(msg_id)
            _save_labeled_ids(state_path, labeled_ids)   # after each label: a crash can't cause a relabel
        newly_labeled.append(triage.message)
    return newly_labeled


def watch(client_factory, cache_path: str | Path, interval: float = 600, max_iterations: int | None = None,
         on_sync=None, sleep=time.sleep, **sync_kwargs) -> None:
    """Call sync_once every `interval` seconds, forever (or `max_iterations` times, for tests).

    `client_factory` is called fresh each cycle so it can hand back a GmailClient built from a
    just-refreshed access token. `on_sync(newly_labeled, error)` is called after every cycle,
    win or lose, so a caller (the CLI) can report progress without the loop depending on it.
    A GmailAuthError is reported and then re-raised: the loop stops, because retrying with
    revoked credentials every 10 minutes would never succeed.
    """
    i = 0
    while max_iterations is None or i < max_iterations:
        error = newly_labeled = None
        try:
            newly_labeled = sync_once(client_factory(), cache_path, **sync_kwargs)
        except GmailAuthError as exc:
            if on_sync:
                on_sync([], exc)
            raise
        except Exception as exc:
            error = exc
        if on_sync:
            on_sync(newly_labeled or [], error)
        i += 1
        if max_iterations is None or i < max_iterations:
            sleep(interval)
