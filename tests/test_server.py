import json
import threading
import urllib.error
import urllib.request

import pytest

from signalbox.loaders import load_json, save_json
from signalbox.server import make_server
from signalbox.synth import generate


@pytest.fixture
def server(tmp_path):
    data = tmp_path / "inbox.json"
    me, msgs = generate()
    save_json(data, me, msgs)
    srv = make_server(data, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", data
    srv.shutdown()
    srv.server_close()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, dict(resp.headers), json.loads(resp.read())


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def status_of(fn, *args):
    with pytest.raises(urllib.error.HTTPError) as exc:
        fn(*args)
    return exc.value.code


def test_needs_you_feed_lists_only_highlighted_tiers(server):
    base, _ = server
    status, headers, body = get(f"{base}/api/needs-you")
    assert status == 200
    items = body["items"]
    assert items, "the synthetic inbox has messages that need you"
    assert {i["tier"] for i in items} <= {"needs_you", "worth_a_look"}
    assert any(i["tier"] == "needs_you" for i in items)
    assert set(items[0]) == {"id", "thread_id", "sender", "sender_name", "subject", "score", "tier", "why"}
    assert [i["score"] for i in items] == sorted((i["score"] for i in items), reverse=True)


def test_needs_you_feed_is_not_readable_by_other_web_pages(server):
    base, _ = server
    _, headers, _ = get(f"{base}/api/needs-you")
    lower = {k.lower(): v for k, v in headers.items()}
    assert "access-control-allow-origin" not in lower
    assert lower["cache-control"] == "no-store"


def test_needs_you_feed_respects_vip_and_mute(tmp_path):
    data = tmp_path / "inbox.json"
    me, msgs = generate()
    save_json(data, me, msgs)
    top_sender = None
    srv = make_server(data, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _, _, body = get(f"http://127.0.0.1:{srv.server_address[1]}/api/needs-you")
        top_sender = body["items"][0]["sender"]
    finally:
        srv.shutdown()
        srv.server_close()

    muted = make_server(data, port=0, mute={top_sender})
    threading.Thread(target=muted.serve_forever, daemon=True).start()
    try:
        _, _, body = get(f"http://127.0.0.1:{muted.server_address[1]}/api/needs-you")
        assert top_sender not in {i["sender"] for i in body["items"]}
    finally:
        muted.shutdown()
        muted.server_close()


def test_triage_endpoint_and_bad_mode(server):
    base, _ = server
    _, _, body = get(f"{base}/api/triage?mode=headers")
    assert "results" in body and body["modes"] == ["headers", "headers+relationship", "full"]
    assert status_of(get, f"{base}/api/triage?mode=bogus") == 400


def test_unknown_paths_404(server):
    base, _ = server
    assert status_of(get, f"{base}/api/nope") == 404
    assert status_of(post, f"{base}/api/nope", {}) == 404


def test_label_endpoint_saves_and_validates(server):
    base, data = server
    _, msgs = load_json(data)
    target = msgs[0].id
    assert post(f"{base}/api/label", {"id": target, "label": True}) == (200, {"ok": True})
    _, msgs = load_json(data)
    assert next(m for m in msgs if m.id == target).label is True

    assert status_of(post, f"{base}/api/label", {"id": "does-not-exist", "label": True}) == 404
    assert status_of(post, f"{base}/api/label", {"oops": 1}) == 400
