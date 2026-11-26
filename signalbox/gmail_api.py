"""Gmail API client: list/get messages, manage labels — plain urllib, no google-api-python-client.

Also converts a Gmail message resource (format=full) into Signal's normalized Message, the
same role load_mbox() plays for a Takeout mbox file in loaders.py.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime

from .gmail_auth import GmailAuthError
from .models import Message

API = "https://gmail.googleapis.com/gmail/v1/users/me"
RETRY_STATUSES = {429, 500, 502, 503, 504}   # rate limited or Google having a moment


class GmailAPIError(RuntimeError):
    pass


def _backoff(attempt: int, retry_after: str | None) -> float:
    try:
        return min(float(retry_after), 60.0)
    except (TypeError, ValueError):
        return min(2.0 ** attempt, 30.0)


def _request(method: str, url: str, access_token: str, body: dict | None = None, retries: int = 3,
             sleep=time.sleep, urlopen=urllib.request.urlopen) -> dict:
    """One Gmail API call. Retries 429/5xx and network errors with backoff; a 401 raises
    GmailAuthError straight away, since retrying with the same token can't succeed."""
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"authorization": f"Bearer {access_token}",
                                              "content-type": "application/json"})
        try:
            with urlopen(req, timeout=30) as resp:
                raw = resp.read()
            try:
                return json.loads(raw) if raw else {}
            except ValueError as exc:
                raise GmailAPIError(f"{method} {url} returned non-JSON: {raw[:200]!r}") from exc
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise GmailAuthError("Gmail rejected the access token (401). "
                                     "Run `signalbox gmail-auth` again.") from exc
            if exc.code in RETRY_STATUSES and attempt < retries:
                sleep(_backoff(attempt, exc.headers.get("retry-after") if exc.headers else None))
                continue
            detail = exc.read().decode(errors="replace")[:300] if exc.fp else ""
            raise GmailAPIError(f"{method} {url} -> HTTP {exc.code} {detail}".strip()) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt < retries:
                sleep(_backoff(attempt, None))
                continue
            raise GmailAPIError(f"{method} {url} failed after {retries + 1} attempts: {exc}") from exc
    raise AssertionError("unreachable")


class GmailClient:
    """Thin wrapper over the bits of the Gmail REST API Signal needs. `request` is injectable
    for tests so no real HTTP happens outside of it."""

    def __init__(self, access_token: str, request=_request):
        self.access_token = access_token
        self._request = request

    def profile(self) -> dict:
        data = self._request("GET", f"{API}/profile", self.access_token)
        if not data.get("emailAddress"):
            raise GmailAPIError(f"Gmail profile has no emailAddress: {data!r}")
        return data

    def list_message_ids(self, query: str, max_results: int = 100) -> list[str]:
        ids: list[str] = []
        page_token = None
        while len(ids) < max_results:
            params = {"q": query, "maxResults": min(max_results - len(ids), 500)}
            if page_token:
                params["pageToken"] = page_token
            data = self._request("GET", f"{API}/messages?{urllib.parse.urlencode(params)}", self.access_token)
            ids += [m["id"] for m in data.get("messages", [])]
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return ids[:max_results]

    def get_message(self, msg_id: str) -> dict:
        return self._request("GET", f"{API}/messages/{msg_id}?format=full", self.access_token)

    def list_labels(self) -> list[dict]:
        return self._request("GET", f"{API}/labels", self.access_token).get("labels", [])

    def create_label(self, name: str) -> dict:
        return self._request("POST", f"{API}/labels", self.access_token,
                             {"name": name, "labelListVisibility": "labelShow",
                              "messageListVisibility": "show"})

    def modify_message(self, msg_id: str, add_label_ids=(), remove_label_ids=()) -> dict:
        return self._request("POST", f"{API}/messages/{msg_id}/modify", self.access_token,
                             {"addLabelIds": list(add_label_ids), "removeLabelIds": list(remove_label_ids)})


def ensure_label(client: GmailClient, name: str) -> str:
    """The id of the Gmail label `name`, creating it (visible, not auto-archived) if missing."""
    for label in client.list_labels():
        if label["name"] == name:
            return label["id"]
    return client.create_label(name)["id"]


def _header(headers: list[dict], name: str) -> str:
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


def _b64url_text(data) -> str:
    """Decode a Gmail body part; a corrupt part yields "" instead of failing the message."""
    if not isinstance(data, str) or not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


def _plain_body(payload: dict) -> str:
    queue = [payload]
    while queue:
        part = queue.pop(0)
        if not isinstance(part, dict):
            continue
        if part.get("mimeType") == "text/plain":
            text = _b64url_text((part.get("body") or {}).get("data"))
            if text:
                return text
        queue.extend(part.get("parts") or [])
    return _b64url_text((payload.get("body") or {}).get("data"))


def message_from_gmail(raw: dict) -> Message:
    """Convert a Gmail API message resource (format=full) into Signal's normalized Message."""
    if not isinstance(raw, dict) or not raw.get("id"):
        raise ValueError(f"not a Gmail message resource: {str(raw)[:120]!r}")
    headers = [h for h in (raw.get("payload") or {}).get("headers") or []
               if isinstance(h, dict) and "name" in h and "value" in h]
    addresses = getaddresses([_header(headers, "From")])
    name, addr = addresses[0] if addresses else ("", "")
    try:
        ts = parsedate_to_datetime(_header(headers, "Date"))
    except Exception:
        try:
            ts = datetime.fromtimestamp(int(raw["internalDate"]) / 1000, tz=timezone.utc)
        except (KeyError, TypeError, ValueError):
            ts = datetime.now(timezone.utc)
    return Message(
        id=raw["id"],
        channel="email",
        sender=addr,
        sender_name=name,
        recipients=[a for _, a in getaddresses([_header(headers, "To")])],
        cc=[a for _, a in getaddresses([_header(headers, "Cc")])],
        subject=_header(headers, "Subject"),
        body=_plain_body(raw.get("payload") or {})[:5000],
        timestamp=ts,
        headers={h["name"]: h["value"] for h in headers if h["name"].lower() not in {"received", "dkim-signature"}},
        thread_id=raw.get("threadId"),
    )
