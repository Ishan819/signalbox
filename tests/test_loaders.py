import json
import warnings

import pytest

from signalbox.loaders import InvalidDataError, load_json, load_mbox, load_slack_export, save_json
from signalbox.models import Identity, Message

ME = Identity("Ishan Mishra", {"me@x.com"})
SLACK_ME = Identity("Ishan Mishra", {"U0ME"})


# ------------------------------------------------------------------- mbox
MBOX_ONE = (
    "From priya@x.org Wed Nov 25 10:00:00 2026\n"
    "From: Priya Shah <priya@x.org>\n"
    "To: me@x.com\n"
    "Subject: Dinner?\n"
    "Date: Wed, 25 Nov 2026 10:00:00 +0000\n"
    "Message-ID: <a1@x.org>\n"
    "\n"
    "Are you free Friday?\n"
    "\n"
)


def test_empty_mbox_gives_no_messages(tmp_path):
    path = tmp_path / "empty.mbox"
    path.write_text("")
    assert load_mbox(path, ME) == []


def test_missing_mbox_raises_and_does_not_create_a_file(tmp_path):
    path = tmp_path / "typo.mbox"
    with pytest.raises(FileNotFoundError):
        load_mbox(path, ME)
    assert not path.exists()   # mailbox.mbox would have silently created it


def test_mbox_path_that_is_a_directory_raises(tmp_path):
    with pytest.raises(IsADirectoryError):
        load_mbox(tmp_path, ME)


def test_mbox_with_missing_and_garbage_headers_still_loads(tmp_path):
    path = tmp_path / "odd.mbox"
    path.write_text(
        MBOX_ONE
        + "From nobody Wed Nov 25 11:00:00 2026\n"
        + "Subject: no from, no date, no id\n"
        + "Date: not a date at all\n"
        + "\n"
        + "body\n"
    )
    msgs = load_mbox(path, ME)
    assert [m.subject for m in msgs] == ["Dinner?", "no from, no date, no id"]
    assert msgs[0].sender == "priya@x.org" and msgs[0].body.startswith("Are you free")
    assert msgs[1].sender == "" and msgs[1].id == "mbox-1" and msgs[1].timestamp is not None


def test_mbox_limit(tmp_path):
    path = tmp_path / "two.mbox"
    path.write_text(MBOX_ONE + MBOX_ONE.replace("a1@x.org", "a2@x.org"))
    assert len(load_mbox(path, ME, limit=1)) == 1


# ------------------------------------------------------------------ Slack
def _slack(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content if isinstance(content, str) else json.dumps(content))
    return root


GOOD_DAY = [{"type": "message", "user": "U0PRIYA", "text": "<@U0ME> can you review?", "ts": "1764064800.000100",
             "user_profile": {"real_name": "Priya Shah"}}]


def test_missing_slack_folder_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_slack_export(tmp_path / "nope", SLACK_ME)


def test_slack_export_given_a_file_raises(tmp_path):
    zipped = tmp_path / "export.zip"
    zipped.write_text("PK...")
    with pytest.raises(NotADirectoryError):
        load_slack_export(zipped, SLACK_ME)


def test_empty_slack_export_gives_no_messages(tmp_path):
    assert load_slack_export(tmp_path, SLACK_ME) == []


def test_malformed_slack_files_are_skipped_with_one_warning(tmp_path):
    root = _slack(tmp_path, {
        "general/2026-11-24.json": GOOD_DAY,
        "general/2026-11-25.json": "{ this is not json",
        "general/2026-11-26.json": {"not": "a list"},
        "dms.json": "[[[ broken",
    })
    with pytest.warns(UserWarning) as caught:
        msgs = load_slack_export(root, SLACK_ME)
    assert len(caught) == 1
    text = str(caught[0].message)
    assert "2026-11-25.json" in text and "2026-11-26.json" in text and "dms.json" in text
    assert len(msgs) == 1 and msgs[0].sender_name == "Priya Shah" and msgs[0].mentions == ["u0me"]


def test_malformed_slack_messages_are_skipped(tmp_path):
    root = _slack(tmp_path, {"general/2026-11-24.json": GOOD_DAY + [
        "not a dict",
        {"type": "message", "user": "U1", "text": "no ts"},
        {"type": "message", "user": "U1", "text": "bad ts", "ts": "yesterday"},
        {"type": "message", "user": "U1", "text": None, "ts": "1764064900.0", "user_profile": "oops"},
        {"type": "channel_join", "user": "U2", "ts": "1764064950.0"},
    ]})
    with pytest.warns(UserWarning, match="2 message"):
        msgs = load_slack_export(root, SLACK_ME)
    assert [m.body for m in msgs] == ["<@U0ME> can you review?", ""]


def test_clean_slack_export_does_not_warn(tmp_path):
    root = _slack(tmp_path, {
        "D0DM/2026-11-24.json": GOOD_DAY,
        "dms.json": [{"id": "D0DM", "members": ["U0ME", "U0PRIYA"]}],
    })
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        msgs = load_slack_export(root, SLACK_ME)
    assert msgs[0].subject == "DM" and msgs[0].recipients == ["u0me"]


# ------------------------------------------------------------ Signal JSON
def test_load_json_rejects_invalid_files_with_the_path_in_the_message(tmp_path):
    cases = {
        "garbage.json": "{nope",
        "wrong-shape.json": json.dumps({"messages": []}),
        "messages-not-list.json": json.dumps({"me": {"name": "a", "addresses": []}, "messages": {}}),
        "bad-message.json": json.dumps({"me": {"name": "a", "addresses": []}, "messages": [{"id": "x"}]}),
    }
    for name, content in cases.items():
        p = tmp_path / name
        p.write_text(content)
        with pytest.raises(InvalidDataError, match=name):
            load_json(p)


def test_save_json_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "inbox.json"
    msgs = [Message(id="m1", channel="email", sender="a@b.com")]
    save_json(path, ME, msgs)
    save_json(path, ME, msgs + [Message(id="m2", channel="email", sender="c@d.com")])
    assert [m.id for m in load_json(path)[1]] == ["m1", "m2"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["inbox.json"]


def test_save_json_failure_keeps_the_old_file(tmp_path):
    path = tmp_path / "inbox.json"
    save_json(path, ME, [Message(id="m1", channel="email", sender="a@b.com")])

    class Unserializable(Message):
        def to_dict(self):
            return {"bad": object()}

    with pytest.raises(TypeError):
        save_json(path, ME, [Unserializable(id="m2", channel="email", sender="x@y.com")])
    assert [m.id for m in load_json(path)[1]] == ["m1"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["inbox.json"]
