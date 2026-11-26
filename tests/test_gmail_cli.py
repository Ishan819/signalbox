import time
from datetime import datetime, timezone

import pytest

from signalbox import gmail_auth, gmail_sync
from signalbox.gmail_api import GmailAPIError
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


def test_gmail_sync_auth_error_exits_cleanly(tmp_path, monkeypatch, capsys):
    token_path = _fresh_token(tmp_path)

    def revoked(client, cache_path, **kw):
        raise gmail_auth.GmailAuthError("Gmail rejected the access token (401). Run `signalbox gmail-auth` again.")

    monkeypatch.setattr(gmail_sync, "sync_once", revoked)
    rc = main(["gmail-sync", "--token", str(token_path), "--cache", str(tmp_path / "c.json")])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_gmail_sync_api_failure_exits_nonzero(tmp_path, monkeypatch, capsys):
    token_path = _fresh_token(tmp_path)

    def down(client, cache_path, **kw):
        raise GmailAPIError("GET profile failed after 4 attempts")

    monkeypatch.setattr(gmail_sync, "sync_once", down)
    rc = main(["gmail-sync", "--token", str(token_path), "--cache", str(tmp_path / "c.json")])
    assert rc == 1 and "sync failed" in capsys.readouterr().err


def test_gmail_sync_reports_partial_problems_as_warnings(tmp_path, monkeypatch, capsys):
    token_path = _fresh_token(tmp_path)

    def partial(client, cache_path, on_problem=None, **kw):
        on_problem("skipped message m9: HTTP 500")
        return []

    monkeypatch.setattr(gmail_sync, "sync_once", partial)
    assert main(["gmail-sync", "--token", str(token_path), "--cache", str(tmp_path / "c.json")]) == 0
    assert "warning: skipped message m9" in capsys.readouterr().err


def test_corrupt_token_file_exits_cleanly(tmp_path, capsys):
    token_path = tmp_path / "token.json"
    token_path.write_text("{oops")
    assert main(["gmail-sync", "--token", str(token_path), "--cache", str(tmp_path / "c.json")]) == 1
    assert "gmail-auth" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--interval", "--limit"])
def test_non_positive_interval_and_limit_are_rejected(tmp_path, flag):
    with pytest.raises(SystemExit):
        main(["gmail-sync", "--token", str(tmp_path / "t.json"), flag, "0"])


def test_import_commands_report_missing_inputs_without_a_traceback(tmp_path, capsys):
    assert main(["import-mbox", str(tmp_path / "typo.mbox"), "--me", "me@x.com",
                 "--out", str(tmp_path / "o.json")]) == 1
    assert main(["import-slack", str(tmp_path / "nope"), "--me", "U1", "--out", str(tmp_path / "o.json")]) == 1
    assert main(["triage", str(tmp_path / "missing.json")]) == 1
    err = capsys.readouterr().err
    assert err.count("error:") == 3 and "Traceback" not in err
    assert not (tmp_path / "typo.mbox").exists()
