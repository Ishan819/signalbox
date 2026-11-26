import base64
import json

from signalbox.gmail_sync import sync_once
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
