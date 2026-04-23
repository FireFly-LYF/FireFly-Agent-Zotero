"""Message bus module for decoupled channel-agent communication."""

from firefly.bus.events import InboundMessage, OutboundMessage
from firefly.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
