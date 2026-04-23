"""用于向用户发送消息的工具。"""

from typing import Any, Awaitable, Callable

from firefly.agent.tools.base import Tool, tool_parameters
from firefly.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema
from firefly.bus.events import OutboundMessage


@tool_parameters(
    tool_parameters_schema(
        content=StringSchema("The message content to send"),
        channel=StringSchema("Optional: target channel (telegram, discord, etc.)"),
        chat_id=StringSchema("Optional: target chat/user ID"),
        media=ArraySchema(
            StringSchema(""),
            description="Optional: list of file paths to attach (images, audio, documents)",
        ),
        required=["content"],
    )
)
class MessageTool(Tool):
    """在聊天渠道中向用户发送消息的工具。"""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
    ):
        self._send_callback = send_callback
        self._default_channel = default_channel
        self._default_chat_id = default_chat_id
        self._default_message_id = default_message_id
        self._sent_in_turn: bool = False

    def set_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """设置当前消息上下文。"""
        self._default_channel = channel
        self._default_chat_id = chat_id
        self._default_message_id = message_id

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """设置发送消息的回调函数。"""
        self._send_callback = callback

    def start_turn(self) -> None:
        """重置逐轮发送跟踪状态。"""
        self._sent_in_turn = False

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return (
            "Send a message to the user, optionally with file attachments. "
            "This is the ONLY way to deliver files (images, documents, audio, video) to the user. "
            "Use the 'media' parameter with file paths to attach files. "
            "Do NOT use read_file to send files — that only reads content for your own analysis."
        )

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        media: list[str] | None = None,
        **kwargs: Any
    ) -> str:
        from firefly.utils.helpers import strip_think
        content = strip_think(content)
        
        channel = channel or self._default_channel
        chat_id = chat_id or self._default_chat_id
        # 仅在目标仍是同一 channel+chat 时继承默认 message_id。
        # 跨会话发送不得携带原 message_id，因为
        # 某些渠道（如 Feishu）会用它确定目标
        # 会话（通过 Reply API），这会将消息路由
        # 到错误聊天。
        if channel == self._default_channel and chat_id == self._default_chat_id:
            message_id = message_id or self._default_message_id
        else:
            message_id = None

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=media or [],
            metadata={
                "message_id": message_id,
            } if message_id else {},
        )

        try:
            await self._send_callback(msg)
            if channel == self._default_channel and chat_id == self._default_chat_id:
                self._sent_in_turn = True
            media_info = f" with {len(media)} attachments" if media else ""
            return f"Message sent to {channel}:{chat_id}{media_info}"
        except Exception as e:
            return f"Error sending message: {str(e)}"
