import base64
import email.message
import io
import urllib.error

import pytest

from signalbox.gmail_api import GmailAPIError, GmailClient, _request, ensure_label, message_from_gmail
from signalbox.gmail_auth import GmailAuthError


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


# ------------------------------------------------------------ hardening
class _Resp:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def _http_error(code, body=b"", retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://gmail", code, "err", headers, io.BytesIO(body))


def _urlopen(*outcomes):
    calls = []

    def fake(req, timeout=None):
        calls.append(req)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return _Resp(outcome)

    fake.calls = calls
    return fake


def test_request_retries_rate_limits_and_server_errors_then_succeeds():
    sleeps = []
    opener = _urlopen(_http_error(429, retry_after=7), _http_error(503), b'{"ok": true}')
    assert _request("GET", "https://gmail/x", "AT", sleep=sleeps.append, urlopen=opener) == {"ok": True}
    assert sleeps == [7.0, 2.0]            # Retry-After honoured, then exponential backoff
    assert len(opener.calls) == 3


def test_request_gives_up_after_retries_with_the_status_in_the_error():
    opener = _urlopen(*[_http_error(500, b"backend error")] * 4)
    with pytest.raises(GmailAPIError, match="HTTP 500 backend error"):
        _request("GET", "https://gmail/x", "AT", retries=3, sleep=lambda s: None, urlopen=opener)
    assert len(opener.calls) == 4


def test_request_retries_network_errors():
    opener = _urlopen(urllib.error.URLError("connection reset"), TimeoutError(), b"{}")
    assert _request("GET", "https://gmail/x", "AT", sleep=lambda s: None, urlopen=opener) == {}


def test_request_401_raises_auth_error_without_retrying():
    opener = _urlopen(_http_error(401))
    with pytest.raises(GmailAuthError, match="gmail-auth"):
        _request("GET", "https://gmail/x", "AT", sleep=lambda s: None, urlopen=opener)
    assert len(opener.calls) == 1


def test_request_does_not_retry_client_errors():
    opener = _urlopen(_http_error(404, b"not found"))
    with pytest.raises(GmailAPIError, match="HTTP 404"):
        _request("GET", "https://gmail/x", "AT", sleep=lambda s: None, urlopen=opener)
    assert len(opener.calls) == 1


def test_request_non_json_body_is_a_clear_error():
    with pytest.raises(GmailAPIError, match="non-JSON"):
        _request("GET", "https://gmail/x", "AT", urlopen=_urlopen(b"<html>captive portal</html>"))


def test_profile_without_email_address_is_an_error():
    client = GmailClient("AT", request=FakeTransport([{}]))
    with pytest.raises(GmailAPIError, match="emailAddress"):
        client.profile()


def test_message_from_gmail_tolerates_broken_parts():
    raw = _gmail_message()
    raw["payload"]["headers"].append({"name": "X-Broken"})            # header with no value
    raw["payload"]["parts"] = [None, {"mimeType": "text/plain", "body": {"data": "!!!not base64!!!"}},
                               {"mimeType": "text/plain", "body": {"data": _b64url("second part")}}]
    del raw["internalDate"]
    raw["payload"]["headers"] = [h for h in raw["payload"]["headers"] if h.get("name") != "Date"]
    m = message_from_gmail(raw)
    assert m.body == "second part" and m.sender == "priya@x.org" and m.timestamp is not None


def test_message_from_gmail_rejects_non_messages():
    for junk in (None, {}, {"payload": {}}, "string"):
        with pytest.raises(ValueError):
            message_from_gmail(junk)
