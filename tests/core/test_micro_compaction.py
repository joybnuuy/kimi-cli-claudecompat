from __future__ import annotations

import time

import pytest
from kosong.message import Message, TextPart, ToolCall

from kimi_cli.soul.micro_compaction import (
    TIME_BASED_MC_CLEARED_MESSAGE,
    _collect_compactable_tool_ids,
    _estimate_cleared_tokens,
    _evaluate_time_based_trigger,
    microcompact_messages,
)


class TestCollectCompactableToolIds:
    def test_collects_only_compactable_tools(self):
        messages = [
            Message(
                role="assistant",
                content=[TextPart(text="ok")],
                tool_calls=[
                    ToolCall(id="tc1", function=ToolCall.FunctionBody(name="Shell", arguments='{}')),
                    ToolCall(id="tc2", function=ToolCall.FunctionBody(name="ReadFile", arguments='{}')),
                    ToolCall(id="tc3", function=ToolCall.FunctionBody(name="UnknownTool", arguments='{}')),
                ],
            ),
        ]
        ids = _collect_compactable_tool_ids(messages)
        assert ids == ["tc1", "tc2"]

    def test_ignores_messages_without_tool_calls(self):
        messages = [
            Message(role="user", content=[TextPart(text="hi")]),
            Message(role="assistant", content=[TextPart(text="hello")]),
        ]
        assert _collect_compactable_tool_ids(messages) == []

    def test_preserves_encounter_order(self):
        messages = [
            Message(
                role="assistant",
                content=[TextPart(text="a")],
                tool_calls=[
                    ToolCall(id="a", function=ToolCall.FunctionBody(name="Grep", arguments='{}')),
                ],
            ),
            Message(
                role="assistant",
                content=[TextPart(text="b")],
                tool_calls=[
                    ToolCall(id="b", function=ToolCall.FunctionBody(name="Glob", arguments='{}')),
                ],
            ),
        ]
        assert _collect_compactable_tool_ids(messages) == ["a", "b"]


class TestEvaluateTimeBasedTrigger:
    def test_no_trigger_without_last_assistant_time(self):
        assert _evaluate_time_based_trigger([], None, 5.0) is None

    def test_no_trigger_when_under_threshold(self):
        last_time = time.monotonic() - 60  # 1 minute ago
        assert _evaluate_time_based_trigger([], last_time, 5.0) is None

    def test_triggers_when_over_threshold(self):
        last_time = time.monotonic() - 600  # 10 minutes ago
        trigger = _evaluate_time_based_trigger([], last_time, 5.0)
        assert trigger is not None
        assert trigger.gap_minutes >= 9.9
        assert trigger.threshold_minutes == 5.0

    def test_no_trigger_when_threshold_is_zero(self):
        last_time = time.monotonic() - 600
        assert _evaluate_time_based_trigger([], last_time, 0.0) is None


class TestEstimateClearedTokens:
    def test_empty_content(self):
        msg = Message(role="tool", content=[], tool_call_id="tc1")
        assert _estimate_cleared_tokens(msg) == 1  # max(1, ...)

    def test_text_only(self):
        msg = Message(role="tool", content=[TextPart(text="a" * 100)], tool_call_id="tc1")
        # 100 chars * 4/3 / 4 = ~33.3 → 33
        assert _estimate_cleared_tokens(msg) == 33

    def test_non_text_parts_ignored(self):
        # Only TextPart contributes to estimation
        msg = Message(
            role="tool",
            content=[TextPart(text="short")],
            tool_call_id="tc1",
        )
        assert _estimate_cleared_tokens(msg) == 1  # 5 * 4/3 / 4 = 1.6 → 1


class TestMicrocompactMessages:
    def _make_history(self) -> list[Message]:
        """Build a history with 5 compactable tool calls and results."""
        messages: list[Message] = []
        for i in range(5):
            messages.append(
                Message(
                    role="assistant",
                    content=[TextPart(text=f"step {i}")],
                    tool_calls=[
                        ToolCall(
                            id=f"tc{i}",
                            function=ToolCall.FunctionBody(name="Shell", arguments='{}'),
                        ),
                    ],
                )
            )
            messages.append(
                Message(
                    role="tool",
                    content=[TextPart(text=f"output {i}")],
                    tool_call_id=f"tc{i}",
                )
            )
        return messages

    def test_no_compaction_when_under_threshold(self):
        history = self._make_history()
        result = microcompact_messages(
            history,
            last_assistant_time=time.monotonic() - 60,
            gap_threshold_minutes=5.0,
            keep_recent=3,
        )
        assert result.cleared_count == 0
        assert all(
            isinstance(m.content[0], TextPart) and m.content[0].text.startswith("output")
            for m in history
            if m.role == "tool"
        )

    def test_no_compaction_without_last_assistant_time(self):
        history = self._make_history()
        result = microcompact_messages(
            history,
            last_assistant_time=None,
            gap_threshold_minutes=5.0,
            keep_recent=3,
        )
        assert result.cleared_count == 0

    def test_clears_old_keeps_recent(self):
        history = self._make_history()
        result = microcompact_messages(
            history,
            last_assistant_time=time.monotonic() - 600,
            gap_threshold_minutes=5.0,
            keep_recent=3,
        )
        # tc0, tc1 cleared; tc2, tc3, tc4 kept
        assert result.cleared_count == 2
        assert result.tokens_saved > 0

        for msg in history:
            if msg.role == "tool":
                if msg.tool_call_id in {"tc0", "tc1"}:
                    assert msg.content == [TextPart(text=TIME_BASED_MC_CLEARED_MESSAGE)]
                else:
                    assert msg.content == [TextPart(text=f"output {msg.tool_call_id[-1]}")]

    def test_skips_already_cleared_results(self):
        history = self._make_history()
        # Pre-clear tc0
        history[1].content = [TextPart(text=TIME_BASED_MC_CLEARED_MESSAGE)]

        result = microcompact_messages(
            history,
            last_assistant_time=time.monotonic() - 600,
            gap_threshold_minutes=5.0,
            keep_recent=3,
        )
        # tc0 already cleared, tc1 cleared → 1 new clear
        assert result.cleared_count == 1

    def test_non_compactable_tools_ignored(self):
        history = [
            Message(
                role="assistant",
                content=[TextPart(text="using custom tool")],
                tool_calls=[
                    ToolCall(
                        id="tc_custom",
                        function=ToolCall.FunctionBody(name="CustomTool", arguments='{}'),
                    ),
                ],
            ),
            Message(
                role="tool",
                content=[TextPart(text="custom output")],
                tool_call_id="tc_custom",
            ),
        ]
        result = microcompact_messages(
            history,
            last_assistant_time=time.monotonic() - 600,
            gap_threshold_minutes=5.0,
            keep_recent=1,
        )
        assert result.cleared_count == 0
        assert history[1].content == [TextPart(text="custom output")]

    def test_keep_recent_floored_at_one(self):
        history = self._make_history()
        result = microcompact_messages(
            history,
            last_assistant_time=time.monotonic() - 600,
            gap_threshold_minutes=5.0,
            keep_recent=0,
        )
        # keep_recent=0 → floored to 1, so tc4 kept, tc0-tc3 cleared
        assert result.cleared_count == 4
        for msg in history:
            if msg.role == "tool" and msg.tool_call_id == "tc4":
                assert msg.content == [TextPart(text="output 4")]

    def test_no_crash_on_mismatched_tool_call_id(self):
        # Tool result without matching assistant tool_call
        history = [
            Message(
                role="assistant",
                content=[TextPart(text="hello")],
            ),
            Message(
                role="tool",
                content=[TextPart(text="orphan output")],
                tool_call_id="tc_orphan",
            ),
        ]
        result = microcompact_messages(
            history,
            last_assistant_time=time.monotonic() - 600,
            gap_threshold_minutes=5.0,
            keep_recent=1,
        )
        assert result.cleared_count == 0

    def test_assistant_messages_unchanged(self):
        history = self._make_history()
        original_assistants = [m for m in history if m.role == "assistant"]
        microcompact_messages(
            history,
            last_assistant_time=time.monotonic() - 600,
            gap_threshold_minutes=5.0,
            keep_recent=3,
        )
        assert [m for m in history if m.role == "assistant"] == original_assistants

    def test_user_messages_unchanged(self):
        history = [
            Message(role="user", content=[TextPart(text="question")]),
            Message(
                role="assistant",
                content=[TextPart(text="answer")],
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        function=ToolCall.FunctionBody(name="Shell", arguments='{}'),
                    ),
                ],
            ),
            Message(role="tool", content=[TextPart(text="output")], tool_call_id="tc1"),
        ]
        microcompact_messages(
            history,
            last_assistant_time=time.monotonic() - 600,
            gap_threshold_minutes=5.0,
            keep_recent=0,
        )
        assert history[0].content == [TextPart(text="question")]
