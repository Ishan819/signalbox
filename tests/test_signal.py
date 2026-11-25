from datetime import datetime, timedelta, timezone

from signalbox.ask import HeuristicAskDetector, LLMAskDetector
from signalbox.automation import AutomationAnalyzer
from signalbox.evaluate import evaluate
from signalbox.models import Identity, Message
from signalbox.relationship import RelationshipGraph
from signalbox.scorer import Triager
from signalbox.synth import generate

ME = Identity("Ishan Mishra", {"me@x.com"})
T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def msg(i, sender, body="", **kw):
    kw.setdefault("recipients", ["me@x.com"])
    kw.setdefault("timestamp", T0 + timedelta(hours=i))
    return Message(id=f"m{i}", channel="email", sender=sender, body=body, **kw)


def test_headers_and_bot_flags_mark_automation():
    msgs = [msg(1, "news@site.com", "hi", headers={"List-Unsubscribe": "<x>"}),
            msg(2, "ci@x.io", "build", headers={"Auto-Submitted": "auto-generated"}),
            msg(3, "u_bot", "standup?", is_bot=True),
            msg(4, "friend@x.org", "dinner tonight?")]
    a = AutomationAnalyzer(ME, msgs)
    scores = [a.analyze(m).score for m in msgs]
    assert all(s > 0.85 for s in scores[:3])
    assert scores[3] < 0.1


def test_templated_ai_outreach_is_detected_without_headers():
    body = ("Hi Ishan, I'm with {co}. I noticed your work on Go services and thought you might be interested. "
            "{co} helps engineers ship faster. Would you have 15 minutes this week for a quick call?")
    cos = ["Nimbus", "Quanta", "Forge", "Lumen"]
    msgs = [msg(i, f"ava@{c.lower()}.io", body.format(co=c)) for i, c in enumerate(cos)]
    a = AutomationAnalyzer(ME, msgs)
    for m in msgs:
        r = a.analyze(m)
        assert r.score > 0.6, r
        assert any("template" in why for why in r.reasons)


def test_relationship_comes_from_your_own_sent_mail():
    msgs = [msg(i, "me@x.com", "ok", recipients=["priya@x.org"]) for i in range(5)]
    msgs += [msg(10, "priya@x.org", "hey"), msg(11, "stranger@y.org", "hey"),
             msg(12, "boss@x.org", "hey")]
    g = RelationshipGraph(ME, msgs)
    assert g.analyze(msgs[5]).score > 0.6
    assert g.analyze(msgs[6]).score == 0
    assert 0 < g.analyze(msgs[7]).score < 0.5     # same org as a contact


def test_ask_detection():
    d = HeuristicAskDetector()
    ask = d.detect(msg(1, "a@b.com", "Could you send me the slides by Friday?"), ME)
    fyi = d.detect(msg(2, "a@b.com", "Thanks, the slides look great."), ME)
    cc = d.detect(msg(3, "a@b.com", "Could you send me the slides?", recipients=["z@b.com"], cc=["me@x.com"]), ME)
    assert ask.score > 0.7 and ask.deadline
    assert fyi.score == 0
    assert cc.score < ask.score


def test_full_mode_puts_human_asks_first():
    msgs = [msg(i, "me@x.com", "ok", recipients=["priya@x.org"]) for i in range(4)]
    msgs += [msg(20, "noreply@bank.com", "Please confirm your details by Friday.", label=False),
             msg(21, "priya@x.org", "lol nice", label=False),
             msg(22, "priya@x.org", "Can you review my PR before 5pm?", label=True)]
    ranked = Triager(ME, msgs).triage()
    assert ranked[0].message.id == "m22"
    assert ranked[0].tier == "needs_you"
    assert ranked[-1].message.sender == "noreply@bank.com"


def test_llm_detector_blends_and_survives_failures(tmp_path):
    good = LLMAskDetector("anthropic", tmp_path / "c.json",
                          llm=lambda p, prov: '{"needs_me": false, "confidence": 0.9, "ask": null}')
    m = msg(1, "rex@talent.io", "Could you hop on a call tomorrow?")
    assert good.detect(m, ME).score < HeuristicAskDetector().detect(m, ME).score

    def boom(p, prov):
        raise TimeoutError
    bad = LLMAskDetector("anthropic", tmp_path / "d.json", llm=boom)
    r = bad.detect(m, ME)
    assert r.score > 0 and "LLM unavailable" in r.reasons[-1]


def test_synthetic_benchmark_ablation_story():
    me, msgs = generate()
    res = evaluate(me, msgs)
    assert res["full"]["avg_precision"] > res["headers+relationship"]["avg_precision"]
    assert res["full"]["avg_precision"] > res["headers"]["avg_precision"]
    assert res["full"]["flag_recall"] == 1.0


def test_vip_and_mute_overrides():
    msgs = [msg(1, "boss@corp.com", "fyi, deck attached"), msg(2, "friend@x.org", "Can you call me tonight?")]
    ranked = {t.message.id: t for t in Triager(ME, msgs, vip={"@corp.com"}, mute={"friend@x.org"}).triage()}
    assert ranked["m1"].tier == "needs_you" and "VIP sender" in ranked["m1"].why
    assert ranked["m2"].tier == "low"


def test_agent_acting_for_a_known_contact_is_linked_back():
    msgs = [msg(i, "me@x.com", "ok", recipients=["priya@x.org"]) for i in range(5)]
    msgs += [msg(9, "priya@x.org", "hey", sender_name="Priya Shah"),
             msg(10, "bot@agents.io", "Hi, I'm Priya Shah's AI assistant. Can you join the call at 4pm?",
                 sender_name="Assistant"),
             msg(11, "ava@sales.io", "Hi, I'm an AI assistant at Nimbus. Can you join a call at 4pm?")]
    ranked = {t.message.id: t for t in Triager(ME, msgs).triage()}
    assert ranked["m10"].relationship.delegate == "Priya Shah"
    assert ranked["m10"].score > 2 * ranked["m11"].score


def test_cli_json_output(tmp_path, capsys):
    import json
    from signalbox.__main__ import main
    data = tmp_path / "inbox.json"
    assert main(["generate", "--out", str(data)]) == 0
    capsys.readouterr()
    assert main(["triage", str(data), "--json", "--top", "3"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 3 and out[0]["tier"] == "needs_you" and "signals" in out[0]


def test_digest_lists_only_what_needs_you():
    from signalbox.digest import build_digest
    me, msgs = generate()
    text = build_digest(Triager(me, msgs).triage(), me.name)
    assert text.startswith("# Hi Ishan,") and "Thanksgiving plans" in text and "Filtered out" in text
    assert "Nimbus" not in text


def test_stats_ranks_noisiest_senders_first():
    from signalbox.stats import inbox_stats
    me, msgs = generate()
    text = inbox_stats(Triager(me, msgs).triage())
    first_sender_line = text.split("unsubscribe / mute candidates):\n")[1].splitlines()[0]
    assert "notifications@github.example.com" in first_sender_line
