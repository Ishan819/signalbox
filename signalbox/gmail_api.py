"""Gmail API client: list/get messages, manage labels — plain urllib, no google-api-python-client.

Also converts a Gmail message resource (format=full) into Signal's normalized Message, the
same role load_mbox() plays for a Takeout mbox file in loaders.py.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime

from .models import Message

API = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailAPIError(RuntimeError):
    pass


def _request(method: str, url: str, access_token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"authorization": f"Bearer {access_token}",
                                          "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raise GmailAPIError(f"{method} {url} -> {exc.code} {exc.read().decode(errors='replace')}") from exc


class GmailClient:
    """Thin wrapper over the bits of the Gmail REST API Signal needs. `request` is injectable
    for tests so no real HTTP happens outside of it."""

    def __init__(self, access_token: str, request=_request):
        self.access_token = access_token
        self._request = request

    def profile(self) -> dict:
        return self._request("GET", f"{API}/profile", self.access_token)

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


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _plain_body(payload: dict) -> str:
    queue = [payload]
    while queue:
        part = queue.pop(0)
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return _b64url_decode(part["body"]["data"]).decode("utf-8", "replace")
        queue.extend(part.get("parts", []) or [])
    data = payload.get("body", {}).get("data")
    return _b64url_decode(data).decode("utf-8", "replace") if data else ""


def message_from_gmail(raw: dict) -> Message:
    """Convert a Gmail API message resource (format=full) into Signal's normalized Message."""
    headers = raw.get("payload", {}).get("headers", [])
    addresses = getaddresses([_header(headers, "From")])
    name, addr = addresses[0] if addresses else ("", "")
    try:
        ts = parsedate_to_datetime(_header(headers, "Date"))
    except Exception:
        ts = datetime.fromtimestamp(int(raw["internalDate"]) / 1000, tz=timezone.utc)
    return Message(
        id=raw["id"],
        channel="email",
        sender=addr,
        sender_name=name,
        recipients=[a for _, a in getaddresses([_header(headers, "To")])],
        cc=[a for _, a in getaddresses([_header(headers, "Cc")])],
        subject=_header(headers, "Subject"),
        body=_plain_body(raw.get("payload", {}))[:5000],
        timestamp=ts,
        headers={h["name"]: h["value"] for h in headers if h["name"].lower() not in {"received", "dkim-signature"}},
        thread_id=raw.get("threadId"),
    )
