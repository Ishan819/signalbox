"""Generate a realistic, labeled demo inbox: ~200 inbound messages, a handful that really need you.

Includes the hard cases on purpose:
  * personalized AI sales agents (no bulk headers, they ask you questions by name)
  * bots that @mention you and say "action required by Friday"
  * a real person whose message is AI-polished (should still be flagged)
  * real people with no ask (FYI) and group asks on a mailing list (should not be flagged)
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from .models import Identity, Message

ME = Identity("Ishan Mishra", {"ishan@mail.example.com", "u_ishan"})
NOW = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)

PEOPLE = {
    "priya": ("Priya Shah", "priya.shah@example.com", 12),
    "kim": ("Prof. Daniel Kim", "dkim@cs.university.example.edu", 6),
    "neha": ("Neha Mishra", "neha.mishra@gmail.com", 20),
    "jordan": ("Jordan Lee", "jordan.lee@recruiting.example.com", 2),
    "alex": ("Alex Rivera", "u_alex", 9),
    "omar": ("Omar Haddad", "omar.h@example.com", 4),
}


class _Builder:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.msgs: list[Message] = []
        self.n = 0

    def ts(self, days_back_max: float = 14, days_back_min: float = 0) -> datetime:
        return NOW - timedelta(days=self.rng.uniform(days_back_min, days_back_max))

    def add(self, **kw) -> Message:
        self.n += 1
        kw.setdefault("id", f"m{self.n:04d}")
        kw.setdefault("channel", "email")
        kw.setdefault("recipients", ["ishan@mail.example.com"])
        kw.setdefault("timestamp", self.ts())
        m = Message(**kw)
        self.msgs.append(m)
        return m


def generate(seed: int = 7) -> tuple[Identity, list[Message]]:
    b = _Builder(seed)
    r = b.rng

    # ---------------------------------------------------------- my sent mail
    for key, (name, addr, n_sent) in PEOPLE.items():
        for i in range(n_sent):
            b.add(sender="u_ishan" if addr.startswith("u_") else "ishan@mail.example.com",
                  sender_name=ME.name, recipients=[addr],
                  channel="slack" if addr.startswith("u_") else "email",
                  subject=f"re: {key} {i}", body=r.choice(["sounds good!", "on it", "thanks!", "see you then"]),
                  timestamp=b.ts(60, 0.5), label=None, thread_id=f"t-{key}-{i}")
    b.add(sender="ishan@mail.example.com", sender_name=ME.name, recipients=["acm-officers@clubs.example.edu"],
          subject="Hackathon logistics", body="Here's the room list.", thread_id="t-acm", timestamp=b.ts(20, 10))

    # ---------------------------------------------- the messages that matter
    b.add(sender="priya.shah@example.com", sender_name="Priya Shah", subject="auth bug before demo",
          body="Hey! Can you push the fix for the auth bug before our demo? Need it by 6pm today, "
               "otherwise we demo the old build.", label=True, timestamp=NOW - timedelta(hours=3))
    b.add(sender="dkim@cs.university.example.edu", sender_name="Prof. Daniel Kim", subject="Office hours Thursday",
          body="Hi Ishan, could you confirm whether you can cover TA office hours on Thursday? "
               "Please reply by Wednesday so I can update the schedule. Thanks, Daniel", label=True,
          timestamp=NOW - timedelta(days=1, hours=2))
    b.add(sender="jordan.lee@recruiting.example.com", sender_name="Jordan Lee", subject="Next steps",
          body="Hi Ishan, thanks again for your time. Could you share your availability for the next round "
               "by Friday? Two or three slots is perfect.", label=True, timestamp=NOW - timedelta(hours=20))
    b.add(sender="sam.patel@cs.university.example.edu", sender_name="Sam Patel", subject="Redis project help",
          body="Hi Ishan, Prof. Kim said you built a Redis clone in Go. Any chance you're free tomorrow to help "
               "me debug my RESP parser? Totally fine if not.", label=True, timestamp=NOW - timedelta(hours=9))
    b.add(channel="slack", sender="u_alex", sender_name="Alex Rivera", recipients=["u_ishan"], subject="DM",
          body="yo are we still submitting tonight? i need your API part merged by 11pm", label=True,
          timestamp=NOW - timedelta(hours=1))
    b.add(sender="neha.mishra@gmail.com", sender_name="Neha Mishra", subject="Thanksgiving plans",
          body="Dear Ishan, I hope this message finds you well. Could you please let me know whether you will "
               "be home for Thanksgiving? Mom needs to book the tickets by Tuesday. Warm regards, Neha",
          label=True, timestamp=NOW - timedelta(days=2))
    b.add(channel="slack", sender="priya.shah@example.com", sender_name="Priya Shah", recipients=[],
          mentions=["u_ishan"], subject="#hackathon-team",
          body="<@u_ishan> can you review my PR when you get a chance? It touches your cache code.",
          label=True, timestamp=NOW - timedelta(hours=5))

    # ------------------------------------------- humans, but nothing needed
    for body in ["lol look at this cat https://example.com/cat", "Happy Diwali!! miss you",
                 "Mom says hi. No need to call back."]:
        b.add(sender="neha.mishra@gmail.com", sender_name="Neha Mishra", subject="", body=body, label=False)
    b.add(sender="priya.shah@example.com", sender_name="Priya Shah", subject="re: slides",
          body="Thanks! The slides look great.", label=False)
    b.add(sender="omar.h@example.com", sender_name="Omar Haddad", subject="fyi", body="Saw your talk was posted "
          "online, nice job.", label=False)
    for i in range(6):
        b.add(sender=r.choice(["maya@clubs.example.edu", "leo@clubs.example.edu", "acm-board@clubs.example.edu"]),
              sender_name=r.choice(["Maya", "Leo", "ACM Board"]), recipients=["acm-members@clubs.example.edu"],
              subject=r.choice(["Volunteers for Saturday?", "Pizza order", "Workshop recap"]),
              body=r.choice(["Can anyone volunteer to run check-in on Saturday?", "Does anyone want pineapple?",
                             "Thanks everyone who came, slides are up."]),
              headers={"List-Id": "<acm-members.clubs.example.edu>"}, label=False)
    b.add(sender="riley.chen@example.com", sender_name="Riley Chen", subject="Your blog post",
          body="Just wanted to say your post on Go concurrency was really clear.", label=False)

    # ----------------------------------------------------- AI sales agents
    companies = ["Nimbus", "Quanta", "Forge", "Lumen", "Tidal", "Vertex", "Orbit", "Cobalt", "Helix", "Strata",
                 "Pylon", "Arcadia", "Beacon", "Krypt", "Mosaic", "Zenith", "Ember", "Atlas"]
    topics = ["your Redis clone", "test automation", "your hackathon projects", "backend scaling", "Go services"]
    agent_names = ["Ava", "Max", "Nova", "Leo", "Iris", "Kai"]
    for i, co in enumerate(companies):
        agent = r.choice(agent_names)
        disclose = "I'm an AI assistant at " if i % 3 == 0 else "I'm with "
        b.add(sender=f"{agent.lower()}@{co.lower()}.example.io", sender_name=f"{agent} from {co}",
              subject=r.choice([f"Quick question, Ishan", f"{co} x Ishan", "Idea for you"]),
              body=f"Hi Ishan, {disclose}{co}. I noticed you have been working on {r.choice(topics)} and thought "
                   f"you might be interested. {co} helps engineers ship faster with less toil. Would you have "
                   f"15 minutes this week for a quick call to see if it is a fit? Best, {agent}", label=False)
    for i in range(8):
        co = r.choice(companies)
        b.add(sender=f"{r.choice(agent_names).lower()}@{co.lower()}.example.io", sender_name=co,
              subject="Re: Quick question, Ishan",
              body=f"Hi Ishan, just bumping this to the top of your inbox in case it got buried. "
                   f"Would Thursday or Friday work for a quick 15 minute chat? Best", label=False)

    # A one-off, hand-tuned AI outreach message: no headers, no template twins, no disclosure.
    # The offline heuristics WILL flag this one (a known limit); the LLM ask-detector is what fixes it.
    b.add(sender="rex@foundry-talent.example.io", sender_name="Rex Morgan", subject="Loved your DA Hacks work",
          body="Hi Ishan, I came across the DA Hacks site you helped run and the Redis clone on your GitHub. "
               "We're putting together a small fellowship for student builders. Could you hop on a call "
               "tomorrow so I can walk you through the program?", label=False)

    # ------------------------------------------------- notifications/bots
    for i in range(30):
        repo = r.choice(["redis-go", "testkit", "da-hacks-site"])
        kind = r.choice(["mention", "review_requested", "ci_activity", "subscribed"])
        b.add(sender="notifications@github.example.com", sender_name="GitHub",
              subject=f"[{repo}] PR #{r.randint(10, 99)}: {r.choice(['fix ttl', 'add LPUSH', 'bump deps'])}",
              body=f"@ishan {r.choice(['mentioned you', 'requested your review', 'pushed 2 commits'])} on this "
                   f"pull request. Reply to this email directly or view it on GitHub.",
              headers={"List-Id": f"<{repo}.github.example.com>", "X-GitHub-Reason": kind,
                       "List-Unsubscribe": "<https://github.example.com/unsub>"}, label=False)
    for i in range(15):
        b.add(sender="builds@ci.example.com", sender_name="CI", subject=f"Build #{400 + i} "
              f"{r.choice(['failed', 'passed', 'fixed'])}", body="Your pipeline finished. See logs for details.",
              headers={"Auto-Submitted": "auto-generated"}, label=False,
              timestamp=NOW - timedelta(hours=i * 22 + r.uniform(0, 1)))
    for i in range(12):
        b.add(sender="calendar-notification@calendar.example.com", sender_name="Calendar",
              subject=f"Reminder: CS 146 lecture @ 10am", body="This is an automated reminder for your event.",
              headers={"Auto-Submitted": "auto-generated"}, label=False,
              timestamp=NOW - timedelta(days=i, hours=8))
    newsletters = [("TLDR", "dan@tldrnewsletter.example.com"), ("Medium Daily Digest", "noreply@medium.example.com"),
                   ("LinkedIn", "messages-noreply@linkedin.example.com"), ("Handshake", "jobs@handshake.example.com"),
                   ("Devpost", "hello@devpost.example.com")]
    for i in range(40):
        name, addr = r.choice(newsletters)
        b.add(sender=addr, sender_name=name,
              subject=r.choice(["Your weekly roundup", "You appeared in 12 searches this week",
                                "New jobs that match your profile", "Hackathons near you"]),
              body=f"Hi Ishan, here's what's new this week. {r.choice(['Don’t miss out!', 'Read more inside.'])} "
                   f"Unsubscribe or manage preferences.",
              headers={"List-Unsubscribe": "<mailto:unsub@example.com>", "Precedence": "bulk",
                       "Feedback-ID": "1:news:example"}, label=False)
    for i in range(3):
        b.add(sender="no-reply@accounts.example.com", sender_name="Account Security",
              subject="ACTION REQUIRED: verify your account",
              body="Ishan, please confirm your recovery email by Friday or your account access may be limited.",
              label=False)
    for i in range(10):
        b.add(sender="notifications@canvas.example.edu", sender_name="Canvas",
              subject=r.choice(["New announcement: CS 146", "Assignment graded", "Due date approaching"]),
              body="A new item was posted in your course. Do not reply to this email.",
              headers={"Auto-Submitted": "auto-generated"}, label=False)

    # ------------------------------------------------------- Slack bots
    for i in range(10):
        b.add(channel="slack", sender="b_standup", sender_name="Standup Bot", recipients=["u_ishan"], subject="DM",
              body="<@u_ishan> what did you work on yesterday? Please reply by 10am.", is_bot=True,
              mentions=["u_ishan"], label=False, timestamp=NOW - timedelta(days=i, hours=-1))
    for i in range(8):
        b.add(channel="slack", sender="b_jira", sender_name="Jira", recipients=[], subject="#hackathon-team",
              body=f"<@u_ishan> you were assigned HACK-{100 + i}: {r.choice(['Fix login', 'Add tests', 'Docs'])}",
              is_bot=True, mentions=["u_ishan"], label=False)
    for i in range(6):
        b.add(channel="slack", sender="b_ai_recap", sender_name="AI Recap", recipients=["u_ishan"], subject="DM",
              body="Here's your AI summary of #general: 14 new messages, 2 threads you might care about. "
                   "Can I help you draft replies?", is_bot=True, label=False,
              timestamp=NOW - timedelta(days=i, hours=-2))
    for i in range(5):
        b.add(channel="slack", sender=r.choice(["u_maya", "u_leo", "u_sara"]),
              sender_name=r.choice(["Maya", "Leo", "Sara"]), recipients=[], subject="#general",
              body=r.choice(["anyone know a good taco place?", "reminder that the room changed",
                             "who's coming to the hackathon?"]), label=False)

    b.msgs.sort(key=lambda m: m.timestamp)
    return ME, b.msgs
