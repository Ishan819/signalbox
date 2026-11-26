"""Gmail OAuth: local-loopback installed-app flow (RFC 8252) with PKCE, stdlib only.

No google-auth-oauthlib dependency: we run a tiny HTTP server on localhost to catch the
redirect, and exchange the code for tokens with a plain urllib POST, matching the rest of
Signal's "stdlib only, HTTPS by hand" style (see ask.py).
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = "https://www.googleapis.com/auth/gmail.modify"


@dataclass
class Token:
    access_token: str
    refresh_token: str
    expires_at: float          # unix timestamp
    client_id: str
    client_secret: str

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - 60   # refresh a minute early

    def to_dict(self) -> dict:
        return {"access_token": self.access_token, "refresh_token": self.refresh_token,
                "expires_at": self.expires_at, "client_id": self.client_id,
                "client_secret": self.client_secret}

    @classmethod
    def from_dict(cls, d: dict) -> "Token":
        return cls(**{k: d[k] for k in ("access_token", "refresh_token", "expires_at",
                                        "client_id", "client_secret")})


def load_token(path: str | Path) -> Token | None:
    p = Path(path)
    return Token.from_dict(json.loads(p.read_text())) if p.exists() else None


def save_token(path: str | Path, token: Token) -> None:
    Path(path).write_text(json.dumps(token.to_dict(), indent=2))


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class _CodeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server.auth_code = qs.get("code", [None])[0]
        self.server.auth_error = qs.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        body = ("You're authorized. You can close this tab and return to the terminal."
                if self.server.auth_code else f"Authorization failed: {self.server.auth_error}")
        self.wfile.write(f"<html><body>{body}</body></html>".encode())

    def log_message(self, *args) -> None:   # silence the default stderr access log
        pass


def _http_post_form(url: str, data: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"content-type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def run_oauth_flow(client_id: str, client_secret: str, port: int = 8765,
                   open_browser=webbrowser.open, timeout: float = 300) -> Token:
    """Interactive OAuth: opens a browser for consent, Google redirects to http://localhost:<port>
    with a code, which is exchanged here for an access + refresh token pair."""
    verifier, challenge = _pkce_pair()
    redirect_uri = f"http://localhost:{port}"
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
              "scope": SCOPES, "access_type": "offline", "prompt": "consent",
              "code_challenge": challenge, "code_challenge_method": "S256"}

    server = http.server.HTTPServer(("localhost", port), _CodeHandler)
    server.timeout = 5
    server.auth_code = server.auth_error = None
    open_browser(f"{AUTH_URL}?{urllib.parse.urlencode(params)}")

    deadline = time.time() + timeout
    while server.auth_code is None and server.auth_error is None:
        if time.time() > deadline:
            server.server_close()
            raise TimeoutError("Timed out waiting for Gmail authorization in the browser.")
        server.handle_request()
    server.server_close()

    if server.auth_error:
        raise RuntimeError(f"Gmail authorization failed: {server.auth_error}")

    data = _http_post_form(TOKEN_URL, {"code": server.auth_code, "client_id": client_id,
                                       "client_secret": client_secret, "redirect_uri": redirect_uri,
                                       "grant_type": "authorization_code", "code_verifier": verifier})
    return Token(data["access_token"], data["refresh_token"], time.time() + data["expires_in"],
                client_id, client_secret)


def refresh(token: Token) -> Token:
    data = _http_post_form(TOKEN_URL, {"refresh_token": token.refresh_token, "client_id": token.client_id,
                                       "client_secret": token.client_secret, "grant_type": "refresh_token"})
    return Token(data["access_token"], token.refresh_token, time.time() + data["expires_in"],
                token.client_id, token.client_secret)


def valid_access_token(token: Token, refresh_fn=refresh) -> tuple[str, Token]:
    """A non-expired access token for `token`, refreshing it first if needed."""
    if token.expired:
        token = refresh_fn(token)
    return token.access_token, token
