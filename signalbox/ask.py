"""Signal 3: is this message asking *you* for something (a reply, a decision, an action)?

Two interchangeable detectors:
  * HeuristicAskDetector -- free, offline, explainable.
  * LLMAskDetector        -- wraps an LLM (Anthropic or Gemini over plain HTTPS, no SDK),
                             blended with the heuristic, cached on disk, and only called for
                             messages that are not already obviously automated (saves cost).
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .models import Identity, Message

QUESTION_START = re.compile(
    r"^(can|could|would|will|do|does|did|are|is|should|when|what|where|how|which|who|any chance|"
    r"have you|are you|r u|u free|you free)\b", re.I)
REQUEST = re.compile(
    r"\b(can you|could you|would you|will you|please|pls|plz|let me know|lmk|need you to|"
    r"send me|confirm|review|approve|sign|reply|get back to me|share your|your thoughts|"
    r"take a look|can u|merge|cover for|need your|waiting on you)\b", re.I)
DEADLINE = re.compile(
    r"\b((?:by|before|until|no later than)\s+(?:eod|end of (?:day|week)|tonight|tomorrow|today|noon|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|next week|\d{1,2}(?::\d\d)?\s*(?:am|pm)?)"
    r"|asap|urgent(?:ly)?|eod|tonight|today)\b", re.I)
SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


@dataclass
class AskResult:
    score: float                       # 0 = nothing for you to do, 1 = clear direct ask
    reasons: list[str] = field(default_factory=list)
    deadline: str | None = None
    summary: str | None = None


def _directedness(m: Message, me: Identity) -> tuple[float, str | None]:
    if any(me.is_me(x) for x in m.mentions):
        return 1.0, "@mentions you"
    direct = m.recipients
    if any(me.is_me(r) for r in direct):
        if len(direct) == 1:
            return 1.0, "sent only to you" if m.channel == "email" else "direct message"
        if len(direct) <= 4:
            return 0.7, "addressed to you + a few others"
        return 0.35, None
    if any(me.is_me(r) for r in m.cc):
        return 0.2, "you're only CC'd"
    return 0.1, None   # broadcast / channel / list


class HeuristicAskDetector:
    name = "heuristic"

    def detect(self, m: Message, me: Identity) -> AskResult:
        reasons: list[str] = []
        sentences = [s.strip() for s in SENTENCE.findall(m.body) if s.strip()]
        directed, why = _directedness(m, me)
        question = next((s for s in sentences if s.endswith("?") and (
            directed >= 0.7 or QUESTION_START.search(s) or re.search(r"\b(you|your|u)\b", s, re.I))), None)
        request = REQUEST.search(m.body)
        deadline = DEADLINE.search(m.text)

        parts = []
        if question:
            parts.append(0.75)
            reasons.append("asks you a direct question")
        if request:
            parts.append(0.6)
            if not question:
                reasons.append(f"makes a request (\"{request.group(0)}\")")
        if deadline:
            parts.append(0.35)
            reasons.append(f"deadline: {deadline.group(0)}")
        strength = 1 - _prod(1 - p for p in parts)

        if why and strength > 0:
            reasons.insert(0, why)
        score = strength * (0.35 + 0.65 * directed)
        return AskResult(score, reasons, deadline.group(0) if deadline else None)


def _prod(xs) -> float:
    out = 1.0
    for x in xs:
        out *= x
    return out


# --------------------------------------------------------------------- LLM
PROMPT = """You triage messages for {name}. Decide if this message needs {name} personally to
reply, decide, or act. Newsletters, notifications, sales outreach and FYIs do not.
Reply with ONLY a JSON object: {{"needs_me": true|false, "confidence": 0.0-1.0,
"ask": "<=12 word summary of what is asked, or null", "deadline": "string or null"}}

From: {sender_name} <{sender}>
To: {to}
Subject: {subject}

{body}"""


def _http_json(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def call_llm(prompt: str, provider: str) -> str:
    if provider == "anthropic":
        data = _http_json(
            "https://api.anthropic.com/v1/messages",
            {"model": os.environ.get("SIGNAL_MODEL", "claude-haiku-5-5"), "max_tokens": 200,
             "messages": [{"role": "user", "content": prompt}]},
            {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
        )
        return "".join(b.get("text", "") for b in data["content"])
    if provider == "gemini":
        model = os.environ.get("SIGNAL_MODEL", "gemini-2.5-flash")
        data = _http_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            {"contents": [{"parts": [{"text": prompt}]}]},
            {"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
        )
        return data["candidates"][0]["content"]["parts"][0]["text"]
    raise ValueError(f"unknown provider {provider!r}")


def parse_json_reply(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"no JSON in model reply: {text[:200]!r}")
    return json.loads(match.group(0))


class LLMAskDetector:
    def __init__(self, provider: str, cache_path: str | Path = ".signal_cache.json", llm=call_llm):
        self.name = f"llm:{provider}"
        self.provider, self.llm = provider, llm
        self.heuristic = HeuristicAskDetector()
        self.cache_path = Path(cache_path)
        self.cache: dict = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}

    def detect(self, m: Message, me: Identity) -> AskResult:
        base = self.heuristic.detect(m, me)
        key = f"{self.provider}:{m.id}"
        if key not in self.cache:
            prompt = PROMPT.format(name=me.name, sender_name=m.sender_name, sender=m.sender,
                                   to=", ".join(m.recipients) or "(channel)", subject=m.subject,
                                   body=m.body[:3000])
            try:
                self.cache[key] = parse_json_reply(self.llm(prompt, self.provider))
            except Exception as exc:          # never let the LLM take triage down
                return AskResult(base.score, base.reasons + [f"(LLM unavailable: {type(exc).__name__})"],
                                 base.deadline)
            self.cache_path.write_text(json.dumps(self.cache, indent=1))
        v = self.cache[key]
        conf = float(v.get("confidence", 0.5))
        llm_score = conf if v.get("needs_me") else (1 - conf) * 0.3
        reasons = list(base.reasons)
        if v.get("ask"):
            reasons.insert(0, f"asks: {v['ask']}")
        return AskResult(0.7 * llm_score + 0.3 * base.score, reasons,
                         v.get("deadline") or base.deadline, v.get("ask"))
