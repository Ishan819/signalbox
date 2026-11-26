import base64
import json

import pytest

from signalbox.gmail_api import GmailAPIError
from signalbox.gmail_auth import GmailAuthError
from signalbox.gmail_sync import sync_once, watch
from signalbox.loaders import load_json


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).rstrip(b"=").decode()


def _raw(msg_id, sender, subject, body, to="me@x.com", date="Wed, 25 Nov 2026 10:00:00 +0000"):
    return {
        "id": msg_id,
        "threadId": f"t-{msg_id}",
        "internalDate": "1732500000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": to},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date},
            ],
            "body": {"data": _b64url(body)},
        },
    }


class FakeGmailClient:
    def __init__(self, inbox_ids, sent_ids, raws):
        self.inbox_ids, self.sent_ids, self.raws = inbox_ids, sent_ids, raws
        self.labels = []
        self.modified = []
        self.list_calls = 0

    def profile(self):
        return {"emailAddress": "me@x.com"}

    def list_message_ids(self, query, max_results=100):
        self.list_calls += 1
        return (self.inbox_ids if "in:inbox" in query else self.sent_ids)[:max_results]

    def get_message(self, msg_id):
        return self.raws[msg_id]

    def list_labels(self):
        return list(self.labels)

    def create_label(self, name):
        label = {"id": f"L{len(self.labels) + 1}", "name": name}
        self.labels.append(label)
        return label

    def modify_message(self, msg_id, add_label_ids=(), remove_label_ids=()):
        self.modified.append((msg_id, list(add_label_ids)))
        return {}


ASK = _raw("ask1", "priya@x.org", "Quick favor", "Could you review my PR before 5pm today?")
NEWSLETTER = _raw("news1", "digest@news.example", "This week's roundup", "Check out these articles.")


def test_sync_once_labels_needs_you_messages_and_persists_cache(tmp_path):
    client = FakeGmailClient(["ask1", "news1"], [], {"ask1": ASK, "news1": NEWSLETTER})
    cache_path = tmp_path / "cache.json"

    newly_labeled = sync_once(client, cache_path, limit=10)

    assert [m.id for m in newly_labeled] == ["ask1"]
    assert client.modified == [("ask1", [client.labels[0]["id"]])]
    assert client.labels[0]["name"] == "Needs-You"

    me, msgs = load_json(cache_path)
    assert me.addresses == {"me@x.com"}
    assert {m.id for m in msgs} == {"ask1", "news1"}

    state = json.loads(cache_path.with_suffix(".labeled.json").read_text())
    assert state == ["ask1"]


def test_sync_once_does_not_relabel_on_a_later_run(tmp_path):
    client = FakeGmailClient(["ask1", "news1"], [], {"ask1": ASK, "news1": NEWSLETTER})
    cache_path = tmp_path / "cache.json"

    sync_once(client, cache_path, limit=10)
    second_run = sync_once(client, cache_path, limit=10)

    assert second_run == []
    assert len(client.modified) == 1   # not called again


def test_sync_once_dry_run_skips_gmail_writes(tmp_path):
    client = FakeGmailClient(["ask1"], [], {"ask1": ASK})
    cache_path = tmp_path / "cache.json"

    newly_labeled = sync_once(client, cache_path, limit=10, dry_run=True)

    assert [m.id for m in newly_labeled] == ["ask1"]
    assert client.modified == [] and client.labels == []
    assert not cache_path.with_suffix(".labeled.json").exists()


def test_sync_once_fetches_sent_mail_but_never_labels_it(tmp_path):
    sent = _raw("sent1", "me@x.com", "re: Quick favor", "On it, will review by 5pm.", to="priya@x.org")
    client = FakeGmailClient(["ask1"], ["sent1"], {"ask1": ASK, "sent1": sent})
    cache_path = tmp_path / "cache.json"

    sync_once(client, cache_path, limit=10)

    _, msgs = load_json(cache_path)
    assert {m.id for m in msgs} == {"ask1", "sent1"}
    assert all(msg_id != "sent1" for msg_id, _ in client.modified)


def test_watch_runs_sync_once_per_cycle_and_sleeps_between(tmp_path):
    client = FakeGmailClient(["ask1"], [], {"ask1": ASK})
    cache_path = tmp_path / "cache.json"
    cycles, sleeps = [], []

    watch(lambda: client, cache_path, interval=5, max_iterations=3,
         on_sync=lambda labeled, err: cycles.append((labeled, err)), sleep=sleeps.append, limit=10)

    assert len(cycles) == 3
    assert [m.id for m in cycles[0][0]] == ["ask1"]      # labeled on the first cycle
    assert cycles[1][0] == [] and cycles[2][0] == []     # already labeled after that
    assert all(err is None for _, err in cycles)
    assert sleeps == [5, 5]                               # no trailing sleep after the last cycle


def test_watch_survives_a_failing_cycle_and_keeps_going(tmp_path):
    cache_path = tmp_path / "cache.json"
    calls = []

    def flaky_client_factory():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("token expired")
        return FakeGmailClient(["ask1"], [], {"ask1": ASK})

    cycles = []
    watch(flaky_client_factory, cache_path, interval=1, max_iterations=2,
         on_sync=lambda labeled, err: cycles.append((labeled, err)), sleep=lambda s: None, limit=10)

    assert len(cycles) == 2
    assert isinstance(cycles[0][1], RuntimeError) and cycles[0][0] == []
    assert cycles[1][1] is None and [m.id for m in cycles[1][0]] == ["ask1"]


# ------------------------------------------------------------ hardening
class FlakyClient(FakeGmailClient):
    """FakeGmailClient whose get_message / modify_message fail for chosen ids."""

    def __init__(self, *a, get_errors=None, modify_errors=None, **kw):
        super().__init__(*a, **kw)
        self.get_errors, self.modify_errors = dict(get_errors or {}), dict(modify_errors or {})

    def get_message(self, msg_id):
        if msg_id in self.get_errors:
            raise self.get_errors.pop(msg_id)
        return super().get_message(msg_id)

    def modify_message(self, msg_id, add_label_ids=(), remove_label_ids=()):
        if msg_id in self.modify_errors:
            raise self.modify_errors.pop(msg_id)
        return super().modify_message(msg_id, add_label_ids, remove_label_ids)


ASK2 = _raw("ask2", "sam@x.org", "Sign-off", "Can you approve the budget by Friday?")


def test_one_unfetchable_message_is_skipped_and_retried_next_run(tmp_path):
    client = FlakyClient(["ask1", "ask2"], [], {"ask1": ASK, "ask2": ASK2},
                         get_errors={"ask1": GmailAPIError("HTTP 500 after retries")})
    problems = []
    first = sync_once(client, tmp_path / "c.json", limit=10, on_problem=problems.append)
    assert [m.id for m in first] == ["ask2"]
    assert len(problems) == 1 and "ask1" in problems[0]

    second = sync_once(client, tmp_path / "c.json", limit=10, on_problem=problems.append)
    assert [m.id for m in second] == ["ask1"]          # fetched and labeled on the retry


def test_unparseable_message_is_skipped(tmp_path):
    client = FakeGmailClient(["ask1", "junk"], [], {"ask1": ASK, "junk": {"payload": {}}})
    problems = []
    labeled = sync_once(client, tmp_path / "c.json", limit=10, on_problem=problems.append)
    assert [m.id for m in labeled] == ["ask1"] and "junk" in problems[0]


def test_failed_label_is_not_recorded_so_the_next_run_retries_it(tmp_path):
    client = FlakyClient(["ask1", "ask2"], [], {"ask1": ASK, "ask2": ASK2},
                         modify_errors={"ask1": GmailAPIError("HTTP 503")})
    problems = []
    first = sync_once(client, tmp_path / "c.json", limit=10, on_problem=problems.append)
    assert [m.id for m in first] == ["ask2"] and "couldn't label ask1" in problems[0]
    assert json.loads((tmp_path / "c.labeled.json").read_text()) == ["ask2"]

    second = sync_once(client, tmp_path / "c.json", limit=10)
    assert [m.id for m in second] == ["ask1"]


def test_auth_errors_are_not_swallowed_per_message(tmp_path):
    client = FlakyClient(["ask1"], [], {"ask1": ASK}, get_errors={"ask1": GmailAuthError("401")})
    with pytest.raises(GmailAuthError):
        sync_once(client, tmp_path / "c.json", limit=10)


def test_corrupt_cache_and_state_are_set_aside_and_rebuilt(tmp_path):
    cache = tmp_path / "c.json"
    cache.write_text("{ truncated")
    (tmp_path / "c.labeled.json").write_text('{"not": "a list"}')
    problems = []
    client = FakeGmailClient(["ask1"], [], {"ask1": ASK})

    labeled = sync_once(client, cache, limit=10, on_problem=problems.append)

    assert [m.id for m in labeled] == ["ask1"]
    assert len(problems) == 2 and all("starting fresh" in p for p in problems)
    backups = sorted(p.name for p in tmp_path.iterdir() if ".corrupt-" in p.name)
    assert len(backups) == 2                            # kept for inspection, not deleted
    assert {m.id for m in load_json(cache)[1]} == {"ask1"}


def test_watch_stops_on_auth_error(tmp_path):
    def revoked():
        raise GmailAuthError("token revoked")

    cycles, sleeps = [], []
    with pytest.raises(GmailAuthError):
        watch(revoked, tmp_path / "c.json", interval=1, max_iterations=5,
              on_sync=lambda labeled, err: cycles.append(err), sleep=sleeps.append)
    assert len(cycles) == 1 and isinstance(cycles[0], GmailAuthError)
    assert sleeps == []                                 # didn't keep retrying every interval
