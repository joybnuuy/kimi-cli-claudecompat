"""Micro-compaction: lightweight, LLM-free clearing of old tool results.

This module implements the time-based micro-compaction strategy from
Claude Code's microCompact.ts, adapted for kimi-cli's message structure.
Unlike full compaction which uses an LLM summarizer, micro-compaction
simply replaces old tool result content with a placeholder — zero API
calls, minimal CPU work.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import NamedTuple

from kosong.message import Message, TextPart, ToolCall

from kimi_cli.utils.logging import logger

TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"

# Tools whose results are safe to clear.  Keep in sync with the compactable
# set in Claude Code's microCompact.ts.
_COMPACTABLE_TOOLS = frozenset({
    "Shell",
    "ReadFile",
    "WriteFile",
    "StrReplaceFile",
    "Grep",
    "Glob",
    "SearchWeb",
    "FetchURL",
})


def _collect_compactable_tool_ids(messages: Sequence[Message]) -> list[str]:
    """Walk messages and collect tool_call IDs whose tool name is compactable."""
    ids: list[str] = []
    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.function.name in _COMPACTABLE_TOOLS:
                    ids.append(tc.id)
    return ids


class MicrocompactResult(NamedTuple):
    messages: Sequence[Message]
    """Messages after micro-compaction (mutated in place)."""

    cleared_count: int = 0
    """Number of tool results whose content was cleared."""

    tokens_saved: int = 0
    """Rough estimate of tokens saved."""


class TimeBasedTrigger(NamedTuple):
    gap_minutes: float
    threshold_minutes: float


def _evaluate_time_based_trigger(
    messages: Sequence[Message],
    last_assistant_time: float | None,
    threshold_minutes: float,
) -> TimeBasedTrigger | None:
    """Check whether enough time has passed since the last assistant message.

    Returns a ``TimeBasedTrigger`` when the gap exceeds the threshold,
    or ``None`` when compaction should not fire.
    """
    if not threshold_minutes or threshold_minutes <= 0:
        return None
    if last_assistant_time is None:
        return None
    gap_minutes = (time.monotonic() - last_assistant_time) / 60.0
    if gap_minutes < threshold_minutes:
        return None
    return TimeBasedTrigger(gap_minutes=gap_minutes, threshold_minutes=threshold_minutes)


def _estimate_cleared_tokens(message: Message) -> int:
    """Rough token estimate for a tool-result message's content."""
    total_chars = 0
    for part in message.content:
        if isinstance(part, TextPart):
            total_chars += len(part.text)
    # ~4 chars per token; pad by 4/3 to be conservative (same as Claude)
    return max(1, int(total_chars * (4 / 3) / 4))


def microcompact_messages(
    messages: Sequence[Message],
    *,
    last_assistant_time: float | None,
    gap_threshold_minutes: float,
    keep_recent: int,
) -> MicrocompactResult:
    """Run time-based micro-compaction on a message list.

    This function **mutates** ``messages`` in place to avoid copy overhead.
    It is safe to call repeatedly: already-cleared results are skipped.

    Args:
        messages: Conversation history (mutated in place).
        last_assistant_time: Monotonic timestamp of the most recent assistant
            message, or ``None`` if there hasn't been one yet.
        gap_threshold_minutes: Minimum gap since last assistant message to
            trigger compaction.
        keep_recent: Number of most-recent compactable tool results to keep.
            Floored at 1.

    Returns:
        MicrocompactResult with the (possibly mutated) message list and stats.
    """
    trigger = _evaluate_time_based_trigger(
        messages, last_assistant_time, gap_threshold_minutes
    )
    if trigger is None:
        return MicrocompactResult(messages=messages)

    compactable_ids = _collect_compactable_tool_ids(messages)
    if not compactable_ids:
        return MicrocompactResult(messages=messages)

    # Floor at 1: keep at least the very last tool result.
    keep_recent = max(1, keep_recent)
    keep_set = set(compactable_ids[-keep_recent:])
    clear_set = set(compactable_ids) - keep_set

    if not clear_set:
        return MicrocompactResult(messages=messages)

    cleared_count = 0
    tokens_saved = 0

    for msg in messages:
        if msg.role != "tool" or msg.tool_call_id not in clear_set:
            continue
        # Skip already-cleared results to avoid double-clearing.
        if (
            len(msg.content) == 1
            and isinstance(msg.content[0], TextPart)
            and msg.content[0].text == TIME_BASED_MC_CLEARED_MESSAGE
        ):
            continue

        tokens_saved += _estimate_cleared_tokens(msg)
        msg.content = [TextPart(text=TIME_BASED_MC_CLEARED_MESSAGE)]
        cleared_count += 1

    if cleared_count == 0:
        return MicrocompactResult(messages=messages)

    logger.info(
        "[MICROCOMPACT] gap {:.1f}min > {:.1f}min, "
        "cleared {} tool result(s) (~{} tokens), kept last {}",
        trigger.gap_minutes,
        trigger.threshold_minutes,
        cleared_count,
        tokens_saved,
        len(keep_set),
    )

    return MicrocompactResult(
        messages=messages,
        cleared_count=cleared_count,
        tokens_saved=tokens_saved,
    )
