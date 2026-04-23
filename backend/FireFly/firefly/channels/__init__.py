"""Chat channels module with plugin architecture."""

from firefly.channels.base import BaseChannel
from firefly.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
