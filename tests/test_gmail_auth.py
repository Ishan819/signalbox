import email.message
import io
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from signalbox import gmail_auth
from signalbox.gmail_auth import Token


def test_token_round_trips_through_disk(tmp_path):
    token = Token("AT", "RT", time.time() + 3600, "cid", "secret")
    path = tmp_path / "token.json"
    gmail_auth.save_token(path, token)
    loaded = gmail_auth.load_token(path)
    assert loaded == token


def test_load_token_missing_file_returns_none(tmp_path):
    assert gmail_auth.load_token(tmp_path / "nope.json") is None


def test_valid_access_token_refreshes_when_expired():
    expired = Token("old", "RT", time.time() - 10, "cid", "secret")
    fresh = Token("new", "RT", time.time() + 3600, "cid", "secret")
    access_token, token = gmail_auth.valid_access_token(expired, refresh_fn=lambda t: fresh)
    assert access_token == "new" and token is fresh


def test_valid_access_token_skips_refresh_when_still_fresh():
    good = Token("AT", "RT", time.time() + 3600, "cid", "secret")

    def boom(_):
        raise AssertionError("should not refresh a token that isn't expired")

    access_token, token = gmail_auth.valid_access_token(good, refresh_fn=boom)
    assert access_token == "AT" and token is good


def test_oauth_flow_exchanges_code_for_tokens(monkeypatch):
    def fake_browser(url):
        redirect_uri = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["redirect_uri"][0]
        threading.Thread(target=lambda: urllib.request.urlopen(f"{redirect_uri}?code=abc123"),
                         daemon=True).start()

    monkeypatch.setattr(gmail_auth, "_http_post_form", lambda url, data: {
        "access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
    })

    token = gmail_auth.run_oauth_flow("cid", "secret", port=8799, open_browser=fake_browser, timeout=5)
    assert token.access_token == "AT" and token.refresh_token == "RT" and token.client_id == "cid"


def test_oauth_flow_raises_on_authorization_error(monkeypatch):
    def fake_browser(url):
        redirect_uri = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["redirect_uri"][0]
        threading.Thread(target=lambda: urllib.request.urlopen(f"{redirect_uri}?error=access_denied"),
                         daemon=True).start()

    try:
        gmail_auth.run_oauth_flow("cid", "secret", port=8798, open_browser=fake_browser, timeout=5)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "access_denied" in str(exc)


def test_corrupt_token_file_raises_auth_error(tmp_path):
    path = tmp_path / "token.json"
    for content in ("{not json", '{"access_token": "only this"}', "[]"):
        path.write_text(content)
        with pytest.raises(gmail_auth.GmailAuthError, match="gmail-auth"):
            gmail_auth.load_token(path)


def test_refresh_rejected_by_google_raises_auth_error(monkeypatch):
    def rejected(url, data):
        raise urllib.error.HTTPError(url, 400, "Bad Request", email.message.Message(),
                                     io.BytesIO(b'{"error": "invalid_grant"}'))

    monkeypatch.setattr(gmail_auth, "_http_post_form", rejected)
    with pytest.raises(gmail_auth.GmailAuthError, match="gmail-auth"):
        gmail_auth.refresh(Token("old", "RT", 0, "cid", "secret"))
