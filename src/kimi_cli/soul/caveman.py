"""
Caveman Context Compressor for kimi-cli
========================================
Line-by-line conversation compression using LLM caveman conversion.

Compresses user/assistant messages to caveman speak (brutally shortened)
while preserving technical accuracy. Only works on text content - tool
outputs are wiped first, then conversation is compressed.

Usage:
    /cavecompress              # Manual trigger (wipes tools + compresses)
    /cavecompress auto on      # Enable auto mode
    /cavecompress auto off     # Disable auto mode

Auto mode triggers every N% context growth (default 20%).

Debug mode: Set KIMI_CAVECOMPRESS_DEBUG=1 to save before/after files
"""

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence, List, Tuple, Optional

import kosong
from kosong.chat_provider import TokenUsage
from kosong.message import Message
from kosong.tooling.empty import EmptyToolset

from kimi_cli.llm import LLM
from kimi_cli.soul.message import system
from kimi_cli.utils.logging import logger
from kimi_cli.wire.types import TextPart, ThinkPart, ContentPart, ToolCall, ToolResult


# Detailed caveman compression prompt based on original caveman-compress
CAVEMAN_COMPRESS_PROMPT = """You are a text compression expert. Convert the provided text to "caveman speak" - brutally shortened while preserving ALL technical accuracy.

## Compression Rules

**DROP:**
- Articles (a, an, the)
- Filler words (just, really, basically, actually, essentially, fundamentally)
- Pleasantries ("Sure!", "I'd be happy to help", "Let me know if you need anything")
- Hedging ("I think", "probably", "maybe", "likely", "should")
- Redundant explanations
- Transition words ("So", "Well", "Now", "Okay", "First", "Next", "Finally")

**KEEP EXACTLY AS-IS (NEVER TOUCH):**
- Code blocks (```fenced or indented)
- Inline code (`backtick content`)
- Internal monologue tags (<thinking>, <think>, <analysis> blocks)
- URLs and links
- File paths (/src/components/...)
- Commands (npm install, git commit)
- Technical terms, library names, API names
- Headings (# Heading text - preserve exact)
- Tables (structure preserved)
- Dates, version numbers, numeric values
- Error messages and stack traces
- Configuration values

**CONVERT TO CAVEMAN:**
- Full sentences → fragments OK
- Passive voice → active voice
- "The reason X is because Y" → "X because Y"
- "I would recommend" → "Use"
- "You should consider" → "Try"
- Long explanations → [thing] [action] [reason] pattern

**OUTPUT FORMAT:**
One line per input line, same order:
index: compressed_text

If line should be completely removed (pure fluff like "That's right!", "Great!", "Sure!"), output:
index: DELETE

## Examples

Input: "The reason your React component is re-rendering is likely because you're creating a new object reference on each render cycle."
Output: React component re-renders because new object ref each render cycle.

Input: "Sure! I'd be happy to help you with that."
Output: DELETE

Input: "I would recommend using useMemo to memoize the object."
Output: Use useMemo to memoize object.

Input: "```jsx\nconst x = 1;\n```"
Output: ```jsx\nconst x = 1;\n```

Input: "Basically, you just need to wrap the inline object in useMemo."
Output: Wrap inline object in useMemo.

Now compress these lines:"""


@dataclass
class LineMapping:
    """Tracks original line info for reconstruction."""
    original_index: int
    original_text: str
    is_code_block: bool
    is_heading: bool
    is_list_item: bool
    indent_level: int


@dataclass
class CavecompressResult:
    """Result of caveman compression."""
    messages: Sequence[Message]
    usage: TokenUsage | None
    lines_processed: int
    lines_removed: int
    lines_compressed: int
    tools_wiped: int

    @property
    def estimated_token_count(self) -> int:
        """Estimate token count of compressed messages."""
        if self.usage is not None:
            return self.usage.output
        return sum(
            len(part.text) // 4 for msg in self.messages for part in msg.content
            if isinstance(part, TextPart)
        )


class CavemanCompressor:
    """Compresses conversation messages to caveman speak."""

    def is_fluff(self, text: str) -> bool:
        """Check if line is empty (LLM handles actual fluff detection)."""
        return not text.strip()

    def preprocess(self, text: str) -> Tuple[List[str], List[LineMapping]]:
        """
        Split text into one-sentence-per-line format.
        Returns lines and their metadata for reconstruction.
        """
        lines = text.split('\n')
        result_lines = []
        mappings = []
        in_code_block = False

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Detect code blocks
            if stripped.startswith('```'):
                in_code_block = not in_code_block
                result_lines.append(line)
                mappings.append(LineMapping(
                    original_index=i,
                    original_text=line,
                    is_code_block=True,
                    is_heading=False,
                    is_list_item=False,
                    indent_level=len(line) - len(line.lstrip())
                ))
                continue

            if in_code_block:
                result_lines.append(line)
                mappings.append(LineMapping(
                    original_index=i,
                    original_text=line,
                    is_code_block=True,
                    is_heading=False,
                    is_list_item=False,
                    indent_level=len(line) - len(line.lstrip())
                ))
                continue

            # Detect structural elements
            is_heading = bool(re.match(r'^\s*#+\s+', line))
            is_list = bool(re.match(r'^\s*[-*\d]\s+', line))
            indent = len(line) - len(line.lstrip())

            # Skip empty lines but track them
            if not stripped:
                result_lines.append("")
                mappings.append(LineMapping(
                    original_index=i,
                    original_text=line,
                    is_code_block=False,
                    is_heading=False,
                    is_list_item=False,
                    indent_level=indent
                ))
                continue

            # Split long lines into sentences
            if len(stripped) > 60 and not is_heading and not is_list:
                sentences = self._split_sentences(stripped)
                for sent in sentences:
                    result_lines.append(sent)
                    mappings.append(LineMapping(
                        original_index=i,
                        original_text=line,
                        is_code_block=False,
                        is_heading=is_heading,
                        is_list_item=is_list,
                        indent_level=indent
                    ))
            else:
                result_lines.append(stripped)
                mappings.append(LineMapping(
                    original_index=i,
                    original_text=line,
                    is_code_block=False,
                    is_heading=is_heading,
                    is_list_item=is_list,
                    indent_level=indent
                ))

        return result_lines, mappings

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if s.strip()]

    async def compress_batch(
        self,
        lines: List[str],
        mappings: List[LineMapping],
        llm: LLM
    ) -> Tuple[List[Optional[str]], TokenUsage | None]:
        """
        Send lines to LLM for caveman conversion.
        Returns compressed lines and token usage.
        """
        # Filter out code blocks and empty lines for LLM processing
        # NOTE: We send ALL non-code text to LLM - let it decide what's fluff
        to_compress = []
        indices = []

        for i, (line, mapping) in enumerate(zip(lines, mappings)):
            if mapping.is_code_block or not line.strip():
                continue
            to_compress.append((i, line))
            indices.append(i)

        if not to_compress:
            return lines, None

        # Build prompt for LLM with full instructions
        lines_text = "\n".join([f"{i}: {line}" for i, line in to_compress])
        prompt = CAVEMAN_COMPRESS_PROMPT + "\n\n" + lines_text + "\n\nOutput:"

        # Call LLM
        history = [Message(role="user", content=[TextPart(text=prompt)])]

        result = await kosong.generate(
            chat_provider=llm.chat_provider,
            system_prompt="You are a text compression expert specializing in 'caveman speak' - maximum information, minimum words.",
            tools=[],
            history=history,
        )

        # Extract text content from message
        response_text = ""
        for part in result.message.content:
            if isinstance(part, TextPart):
                response_text += part.text

        compressed = self._parse_llm_response(lines, mappings, response_text, indices)
        return compressed, result.usage

    def _parse_llm_response(
        self,
        original_lines: List[str],
        mappings: List[LineMapping],
        response: str,
        indices: List[int]
    ) -> List[Optional[str]]:
        """Parse LLM response and map back to original lines."""
        result = list(original_lines)

        for line in response.strip().split('\n'):
            line = line.strip()
            if not line or ':' not in line:
                continue

            try:
                idx_str, compressed = line.split(':', 1)
                idx = int(idx_str.strip())
                compressed = compressed.strip()

                if idx in indices:
                    pos = indices.index(idx)
                    if compressed.upper() == "DELETE":
                        result[indices[pos]] = None
                    else:
                        result[indices[pos]] = compressed
            except (ValueError, IndexError):
                continue

        return result

    def reconstruct(
        self,
        compressed_lines: List[Optional[str]],
        mappings: List[LineMapping]
    ) -> str:
        """Reconstruct text from compressed lines."""
        if not compressed_lines:
            return ""

        result = []
        current_paragraph = []
        prev_mapping = None

        for line, mapping in zip(compressed_lines, mappings):
            if line is None:
                continue

            # Preserve structural elements as-is
            if mapping.is_code_block or mapping.is_heading or mapping.is_list_item:
                if current_paragraph:
                    result.append(' '.join(current_paragraph))
                    current_paragraph = []

                if not mapping.is_code_block and mapping.indent_level > 0:
                    line = ' ' * mapping.indent_level + line

                result.append(line)
                prev_mapping = mapping
                continue

            # Handle empty lines (paragraph breaks)
            if not line.strip():
                if current_paragraph:
                    result.append(' '.join(current_paragraph))
                    current_paragraph = []
                result.append("")
                prev_mapping = mapping
                continue

            # Check if we should start a new paragraph
            if prev_mapping is not None:
                if (mapping.original_index != prev_mapping.original_index or
                    mapping.indent_level != prev_mapping.indent_level):
                    if current_paragraph:
                        result.append(' '.join(current_paragraph))
                        current_paragraph = []

            current_paragraph.append(line)
            prev_mapping = mapping

        # Flush final paragraph
        if current_paragraph:
            result.append(' '.join(current_paragraph))

        return '\n'.join(result)

    async def compress_message(
        self,
        message: Message,
        llm: LLM
    ) -> Tuple[Message, int, int, int, TokenUsage | None]:
        """
        Compress a single message.
        Returns (compressed_message, lines_processed, lines_removed, lines_compressed, usage).
        """
        # Extract text content
        text_parts = []
        other_parts = []

        think_parts = []

        for part in message.content:
            if isinstance(part, TextPart):
                text_parts.append(part.text)
            elif isinstance(part, ThinkPart):
                think_parts.append(part)
            else:
                other_parts.append(part)

        # Compress text content
        if text_parts:
            original_text = '\n'.join(text_parts)
            lines, mappings = self.preprocess(original_text)

            # Count original non-empty, non-code lines
            lines_processed = sum(
                1 for l, m in zip(lines, mappings)
                if l.strip() and not m.is_code_block
            )

            # Compress (LLM decides what's fluff and marks DELETE)
            compressed_lines, usage = await self.compress_batch(lines, mappings, llm)

            # Count results
            lines_removed = sum(
                1 for l, m in zip(compressed_lines, mappings)
                if l is None and not m.is_code_block
            )
            lines_compressed = sum(
                1 for l, m in zip(compressed_lines, mappings)
                if l is not None and l.strip() and not m.is_code_block
            )

            # Reconstruct
            compressed_text = self.reconstruct(compressed_lines, mappings)
            new_content: List[ContentPart] = [TextPart(text=compressed_text)]
        else:
            # No text to compress
            lines_processed = 0
            lines_removed = 0
            lines_compressed = 0
            usage = None
            new_content = []

        # Compress thinking blocks too
        for think_part in think_parts:
            if think_part.think:
                think_lines, think_mappings = self.preprocess(think_part.think)
                compressed_think_lines, _ = await self.compress_batch(think_lines, think_mappings, llm)
                compressed_think = self.reconstruct(compressed_think_lines, think_mappings)
                new_content.append(ThinkPart(think=compressed_think, encrypted=think_part.encrypted))

        new_content.extend(other_parts)

        return Message(role=message.role, content=new_content), lines_processed, lines_removed, lines_compressed, usage


async def wipe_tool_outputs(messages: Sequence[Message]) -> Tuple[List[Message], int]:
    """
    Wipe tool output blocks from messages.
    Tool messages (role='tool') are replaced with a placeholder.
    Consecutive tool messages are merged into one with a count.
    Returns (wiped_messages, count_of_tools_wiped).
    """
    wiped_messages = []
    tools_wiped = 0
    consecutive_tools = 0

    for msg in messages:
        # Check if this is a tool message by role
        if msg.role == "tool":
            tools_wiped += 1
            consecutive_tools += 1
        else:
            # Flush any pending consecutive tools before adding non-tool message
            if consecutive_tools > 0:
                if consecutive_tools == 1:
                    wiped_messages.append(Message(
                        role="tool",
                        content=[TextPart(text="[Tool output wiped]")],
                    ))
                else:
                    wiped_messages.append(Message(
                        role="tool",
                        content=[TextPart(text=f"[{consecutive_tools} tool outputs wiped]")],
                    ))
                consecutive_tools = 0
            # Keep non-tool messages as-is
            wiped_messages.append(msg)

    # Flush any remaining consecutive tools at end
    if consecutive_tools > 0:
        if consecutive_tools == 1:
            wiped_messages.append(Message(
                role="tool",
                content=[TextPart(text="[Tool output wiped]")],
            ))
        else:
            wiped_messages.append(Message(
                role="tool",
                content=[TextPart(text=f"[{consecutive_tools} tool outputs wiped]")],
            ))

    return wiped_messages, tools_wiped


def _save_debug_files(
    before_messages: Sequence[Message],
    after_messages: Sequence[Message],
    session_id: str,
) -> Tuple[str, str]:
    """
    Save before/after comparison files for debugging.
    Returns (before_path, after_path).
    """
    debug_dir = Path.home() / ".kimi" / "cavecompress_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    before_path = debug_dir / f"before-{timestamp}.md"
    after_path = debug_dir / f"after-{timestamp}.md"

    # Format before content
    before_lines = [f"# Before Cavecompress - {timestamp}\n", f"Session: {session_id}\n", "=" * 70 + "\n\n"]
    for i, msg in enumerate(before_messages):
        before_lines.append(f"## Message {i + 1} (role: {msg.role})\n\n")
        for part in msg.content:
            if isinstance(part, TextPart):
                before_lines.append(part.text)
                before_lines.append("\n")
            elif isinstance(part, ThinkPart):
                before_lines.append(f"<thinking>\n{part.think}\n</thinking>\n")
            elif isinstance(part, ToolResult):
                before_lines.append(f"[ToolResult: {part.tool_call_id}]\n")
                if part.output:
                    before_lines.append(part.output[:500])  # Truncate for readability
                    before_lines.append("\n")
        before_lines.append("\n---\n\n")

    # Format after content
    after_lines = [f"# After Cavecompress - {timestamp}\n", f"Session: {session_id}\n", "=" * 70 + "\n\n"]
    for i, msg in enumerate(after_messages):
        after_lines.append(f"## Message {i + 1} (role: {msg.role})\n\n")
        for part in msg.content:
            if isinstance(part, TextPart):
                after_lines.append(part.text)
                after_lines.append("\n")
            elif isinstance(part, ThinkPart):
                after_lines.append(f"<thinking>\n{part.think}\n</thinking>\n")
            elif isinstance(part, ToolResult):
                after_lines.append(f"[ToolResult: {part.tool_call_id}]\n")
        after_lines.append("\n---\n\n")

    # Write files
    before_path.write_text("".join(before_lines), encoding="utf-8")
    after_path.write_text("".join(after_lines), encoding="utf-8")

    logger.info(
        "Cavecompress debug files saved: before={before}, after={after}",
        before=before_path,
        after=after_path,
    )

    return str(before_path), str(after_path)


async def compact_with_caveman(
    messages: Sequence[Message],
    llm: LLM,
    preserve_recent: int = 2,
    wipe_tools: bool = True,
) -> CavecompressResult:
    """
    Compact a sequence of messages using caveman compression.

    Args:
        messages: The messages to compact
        llm: The LLM to use for compression
        preserve_recent: Number of most recent messages to preserve
        wipe_tools: Whether to wipe tool outputs before compression

    Returns:
        CavecompressResult with compressed messages and stats
    """
    compressor = CavemanCompressor()

    if not messages or preserve_recent <= 0:
        return CavecompressResult(
            messages=messages,
            usage=None,
            lines_processed=0,
            lines_removed=0,
            lines_compressed=0,
            tools_wiped=0,
        )

    # Step 1: Wipe tool outputs if requested
    working_messages = list(messages)
    tools_wiped = 0
    if wipe_tools:
        working_messages, tools_wiped = await wipe_tool_outputs(working_messages)

    # Step 2: Split into compress and preserve
    history = working_messages
    preserve_start = max(0, len(history) - preserve_recent)

    to_compress = history[:preserve_start]
    to_preserve = history[preserve_start:]

    if not to_compress:
        return CavecompressResult(
            messages=messages,
            usage=None,
            lines_processed=0,
            lines_removed=0,
            lines_compressed=0,
            tools_wiped=tools_wiped,
        )

    # Step 3: Compress each message
    compressed_messages = []
    total_usage: TokenUsage | None = None
    total_processed = 0
    total_removed = 0
    total_compressed = 0

    for msg in to_compress:
        # Only compress user and assistant messages
        if msg.role not in ("user", "assistant"):
            compressed_messages.append(msg)
            continue

        compressed_msg, processed, removed, compressed_count, msg_usage = await compressor.compress_message(msg, llm)
        compressed_messages.append(compressed_msg)

        total_processed += processed
        total_removed += removed
        total_compressed += compressed_count

        # Aggregate token usage
        if msg_usage is not None:
            if total_usage is None:
                total_usage = msg_usage
            else:
                total_usage = TokenUsage(
                    input_other=total_usage.input_other + msg_usage.input_other,
                    output=total_usage.output + msg_usage.output,
                    input_cache_read=total_usage.input_cache_read + msg_usage.input_cache_read,
                    input_cache_creation=total_usage.input_cache_creation + msg_usage.input_cache_creation,
                )

    # Combine
    final_messages = compressed_messages + list(to_preserve)

    # Debug: Save before/after files if enabled
    debug_enabled = os.getenv("KIMI_CAVECOMPRESS_DEBUG", "").lower() in ("1", "true", "yes")
    if debug_enabled:
        try:
            # Get session ID from first message or use timestamp
            session_id = "unknown"
            if messages:
                # Try to extract from message metadata if available
                first_msg = messages[0]
                if hasattr(first_msg, 'id') and first_msg.id:
                    session_id = str(first_msg.id)[:8]

            before_path, after_path = _save_debug_files(
                working_messages,  # After tool wipe, before compression
                final_messages,
                session_id,
            )
            logger.info(
                "Cavecompress debug: saved {before} and {after}",
                before=before_path,
                after=after_path,
            )
        except Exception as e:
            logger.warning("Failed to save cavecompress debug files: {error}", error=e)

    return CavecompressResult(
        messages=final_messages,
        usage=total_usage,
        lines_processed=total_processed,
        lines_removed=total_removed,
        lines_compressed=total_compressed,
        tools_wiped=tools_wiped,
    )


def should_trigger_cavecompress(
    current_tokens: int,
    last_compressed_tokens: int,
    max_context_size: int,
    trigger_ratio: float = 0.2
) -> bool:
    """
    Determine whether cavecompress should trigger.

    Triggers when context has grown by trigger_ratio (default 20%)
    from the last compression point.

    Args:
        current_tokens: Current context token count
        last_compressed_tokens: Token count at last compression
        max_context_size: Maximum context size
        trigger_ratio: Growth ratio to trigger compression (default 0.2 = 20%)

    Returns:
        True if cavecompress should trigger
    """
    if last_compressed_tokens == 0:
        # First compression trigger at 20% of max
        return current_tokens >= max_context_size * trigger_ratio

    # Trigger when grown by 20% from last compression
    growth = current_tokens - last_compressed_tokens
    return growth >= last_compressed_tokens * trigger_ratio
