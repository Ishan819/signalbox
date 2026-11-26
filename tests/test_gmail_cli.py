import time
from datetime import datetime, timezone

from signalbox import gmail_auth, gmail_sync
from signalbox.__main__ import main
from signalbox.models import Message


def _fresh_token(tmp_path):
    path = tmp_path / "token.json"
    gmail_auth.save_token(path, gmail_auth.Token("AT", "RT", time.time() + 3600, "cid", "secret"))
    return path


def test_gmail_sync_without_a_token_fails_with_a_hint(tmp_path, capsys):
    rc = main(["gmail-sync", "--token", str(tmp_path / "missing.json"),
              "--cache", str(tmp_path / "cache.json")])
    assert rc == 1
    assert "gmail-auth" in capsys.readouterr().err


def test_gmail_sync_one_shot_reports_newly_labeled(tmp_path, monkeypatch, capsys):
    token_path = _fresh_token(tmp_path)
    needs_you = Message(id="m1", channel="email", sender="priya@x.org", sender_name="Priya Shah",
                        subject="Quick favor", body="", timestamp=datetime.now(timezone.utc))

    captured = {}

    def fake_sync_once(client, cache_path, **kw):
        captured["kw"] = kw
        return [needs_you]

    monkeypatch.setattr(gmail_sync, "sync_once", fake_sync_once)

    rc = main(["gmail-sync", "--token", str(token_path), "--cache", str(tmp_path / "cache.json"), "--dry-run"])

    assert rc == 0
    assert captured["kw"]["dry_run"] is True
    out = capsys.readouterr().out
    assert "needs-you" in out and "Priya Shah" in out and "Quick favor" in out


def test_gmail_sync_watch_passes_through_interval(tmp_path, monkeypatch):
    token_path = _fresh_token(tmp_path)
    captured = {}

    def fake_watch(client_factory, cache_path, interval=None, on_sync=None, **kw):
        captured["interval"] = interval
        captured["kw"] = kw
        on_sync([], None)

    monkeypatch.setattr(gmail_sync, "watch", fake_watch)

    rc = main(["gmail-sync", "--token", str(token_path), "--cache", str(tmp_path / "cache.json"),
              "--watch", "--interval", "30"])

    assert rc == 0
    assert captured["interval"] == 30
    assert captured["kw"]["limit"] == 50   # CLI default threaded through
