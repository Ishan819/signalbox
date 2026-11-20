"""Signal: find the few messages that actually need you in an inbox full of bots and AI agents."""
from .models import Identity, Message
from .scorer import Triage, Triager

__all__ = ["Identity", "Message", "Triage", "Triager"]
__version__ = "0.1.0"
