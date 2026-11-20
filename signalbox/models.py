"""Core data types shared across Signal."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def _parse_ts(value) -> datetime:
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, (int, float)):
        ts = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


@dataclass
class Message:
    """One message from any channel (email, Slack, ...), normalized."""

    id: str
    channel: str                                   # "email" | "slack" | anything else
    sender: str                                    # normalized address or user id
    sender_name: str = ""
    recipients: list[str] = field(default_factory=list)   # direct recipients (To / DM members)
    cc: list[str] = field(default_factory=list)
    subject: str = ""
    body: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    headers: dict[str, str] = field(default_factory=dict)  # lower-cased header names
    is_bot: bool = False                           # platform says so (e.g. Slack bot_id)
    mentions: list[str] = field(default_factory=list)
    thread_id: str | None = None
    label: bool | None = None                      # ground truth: does this need me?

    def __post_init__(self) -> None:
        self.timestamp = _parse_ts(self.timestamp)
        self.sender = self.sender.strip().lower()
        self.recipients = [r.strip().lower() for r in self.recipients]
        self.cc = [r.strip().lower() for r in self.cc]
        self.mentions = [m.strip().lower() for m in self.mentions]
        self.headers = {k.lower(): str(v) for k, v in self.headers.items()}

    @property
    def text(self) -> str:
        return f"{self.subject}\n{self.body}".strip()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Identity:
    """Who "me" is: every address / user id that belongs to the inbox owner."""

    name: str
    addresses: set[str]

    def __post_init__(self) -> None:
        self.addresses = {a.strip().lower() for a in self.addresses}

    def is_me(self, who: str) -> bool:
        return who.strip().lower() in self.addresses

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name else ""

    def to_dict(self) -> dict:
        return {"name": self.name, "addresses": sorted(self.addresses)}
