"""Interface-agnostic chat session.

The CLI uses this today; a web UI or Telegram bot can reuse it by supplying their own
``approve`` / ``on_text`` / ``on_tool_call`` callbacks and a different ``channel``.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass, field

from pydantic_ai import (
    AgentStreamEvent,
    DeferredToolRequests,
    DeferredToolResults,
    FunctionToolCallEvent,
    ModelMessage,
    ModelRequest,
    PartDeltaEvent,
    PartStartEvent,
    RunContext,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    UserPromptPart,
)

from .agent import build_agent
from .config import Settings
from .db import Store
from .deps import Deps

ApproveFn = Callable[[ToolCallPart], Awaitable[bool]]
TextFn = Callable[[str], None]
ToolFn = Callable[[ToolCallPart], None]


def _trim_history(messages: list[ModelMessage], max_turns: int) -> list[ModelMessage]:
    """Keep the last ``max_turns`` user turns, cutting only at a user prompt so tool calls stay paired."""
    starts = [
        i
        for i, m in enumerate(messages)
        if isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts)
    ]
    if len(starts) <= max_turns:
        return messages
    return messages[starts[-max_turns] :]


@dataclass
class Session:
    settings: Settings
    store: Store
    channel: str = "cli"
    conversation_id: int | None = None
    messages: list[ModelMessage] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.agent = build_agent(self.settings)
        self.deps = Deps(settings=self.settings, store=self.store)

    # ---- conversation management ------------------------------------------
    def resume_latest(self) -> None:
        conv = self.store.latest_conversation(self.channel)
        if conv is None:
            self.new()
        else:
            self.load(conv)

    def new(self) -> None:
        self.conversation_id = self.store.new_conversation(self.channel)
        self.messages = []

    def load(self, conv_id: int) -> None:
        self.conversation_id = conv_id
        self.messages = self.store.load_messages(conv_id)

    def reload_agent(self) -> None:
        """Rebuild the agent, e.g. after connecting Google or changing the model."""
        self.agent = build_agent(self.settings)

    # ---- chatting -----------------------------------------------------------
    async def ask(
        self,
        prompt: str,
        *,
        approve: ApproveFn,
        on_text: TextFn | None = None,
        on_tool_call: ToolFn | None = None,
    ) -> str:
        if self.conversation_id is None:
            self.new()

        announced: set[str] = set()

        async def handle_events(ctx: RunContext[Deps], events: AsyncIterable[AgentStreamEvent]) -> None:
            async for event in events:
                if on_text and isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                    on_text(event.part.content)
                elif on_text and isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    on_text(event.delta.content_delta)
                elif on_tool_call and isinstance(event, FunctionToolCallEvent):
                    if event.part.tool_call_id not in announced:
                        announced.add(event.part.tool_call_id)
                        on_tool_call(event.part)

        history = _trim_history(self.messages, self.settings.history_turns)
        new: list[ModelMessage] = []
        user_prompt: str | None = prompt
        deferred: DeferredToolResults | None = None

        while True:
            result = await self.agent.run(
                user_prompt,
                message_history=history + new,
                deferred_tool_results=deferred,
                deps=self.deps,
                event_stream_handler=handle_events,
            )
            new += result.new_messages()
            output = result.output
            if not isinstance(output, DeferredToolRequests):
                break
            # Human-in-the-loop: ask for each pending side-effecting call, then continue the run.
            deferred = DeferredToolResults()
            for call in output.approvals:
                ok = await approve(call)
                deferred.approvals[call.tool_call_id] = ok
            user_prompt = None
            if on_text:
                on_text("\n")

        self.messages += new
        assert self.conversation_id is not None
        self.store.save_messages(self.conversation_id, self.messages, title=prompt[:80])
        return output
