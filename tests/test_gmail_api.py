import base64

from signalbox.gmail_api import GmailClient, ensure_label, message_from_gmail


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).rstrip(b"=").decode()


def _gmail_message(body="hi there", mime="multipart/alternative"):
    return {
        "id": "abc123",
        "threadId": "t1",
        "internalDate": "1732500000000",
        "payload": {
            "mimeType": mime,
            "headers": [
                {"name": "From", "value": "Priya Shah <priya@x.org>"},
                {"name": "To", "value": "me@x.com"},
                {"name": "Cc", "value": "other@x.org"},
                {"name": "Subject", "value": "Hello"},
                {"name": "Date", "value": "Wed, 25 Nov 2026 10:00:00 +0000"},
            ],
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64url("<b>hi there</b>")}},
                {"mimeType": "text/plain", "body": {"data": _b64url(body)}},
            ],
        },
    }


def test_message_from_gmail_parses_headers_and_plain_body():
    m = message_from_gmail(_gmail_message())
    assert m.id == "abc123" and m.thread_id == "t1"
    assert m.sender == "priya@x.org" and m.sender_name == "Priya Shah"
    assert m.recipients == ["me@x.com"] and m.cc == ["other@x.org"]
    assert m.subject == "Hello" and m.body == "hi there"
    assert m.timestamp.year == 2026 and m.timestamp.month == 11


def test_message_from_gmail_falls_back_to_internal_date_without_date_header():
    raw = _gmail_message()
    raw["payload"]["headers"] = [h for h in raw["payload"]["headers"] if h["name"] != "Date"]
    m = message_from_gmail(raw)
    assert m.timestamp.year == 2024   # 1732500000000 ms


def test_message_from_gmail_handles_non_multipart_body():
    raw = _gmail_message(mime="text/plain")
    raw["payload"] = {**raw["payload"], "body": {"data": _b64url("just this")}, "parts": []}
    m = message_from_gmail(raw)
    assert m.body == "just this"


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, method, url, access_token, body=None):
        self.calls.append((method, url, body))
        return self.responses.pop(0)


def test_list_message_ids_follows_page_token():
    transport = FakeTransport([
        {"messages": [{"id": "1"}, {"id": "2"}], "nextPageToken": "p2"},
        {"messages": [{"id": "3"}]},
    ])
    client = GmailClient("AT", request=transport)
    assert client.list_message_ids("in:inbox", max_results=10) == ["1", "2", "3"]
    assert "pageToken=p2" in transport.calls[1][1]


def test_list_message_ids_stops_at_max_results():
    transport = FakeTransport([{"messages": [{"id": "1"}, {"id": "2"}], "nextPageToken": "p2"}])
    client = GmailClient("AT", request=transport)
    assert client.list_message_ids("in:inbox", max_results=1) == ["1"]
    assert len(transport.calls) == 1


def test_ensure_label_reuses_existing_label():
    transport = FakeTransport([{"labels": [{"id": "L1", "name": "Needs-You"}]}])
    client = GmailClient("AT", request=transport)
    assert ensure_label(client, "Needs-You") == "L1"
    assert len(transport.calls) == 1   # no create call


def test_ensure_label_creates_missing_label():
    transport = FakeTransport([{"labels": []}, {"id": "L2", "name": "Needs-You"}])
    client = GmailClient("AT", request=transport)
    assert ensure_label(client, "Needs-You") == "L2"
    assert transport.calls[1][0] == "POST"


def test_modify_message_sends_add_and_remove_label_ids():
    transport = FakeTransport([{}])
    client = GmailClient("AT", request=transport)
    client.modify_message("m1", add_label_ids=["L1"])
    method, url, body = transport.calls[0]
    assert method == "POST" and url.endswith("/messages/m1/modify")
    assert body == {"addLabelIds": ["L1"], "removeLabelIds": []}
