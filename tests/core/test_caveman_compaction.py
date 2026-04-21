"""Tests for caveman context compression."""

import pytest
from kosong.message import Message

from kosong.chat_provider import TokenUsage

from kimi_cli.soul.caveman import (
    CavemanCompressor,
    CavecompressResult,
    LineMapping,
    should_trigger_cavecompress,
    wipe_tool_outputs,
)
from kimi_cli.wire.types import TextPart, ToolResult


def test_preprocess_splits_sentences():
    """Test that preprocess splits long sentences."""
    compressor = CavemanCompressor()
    text = "First sentence. Second sentence. Third is longer and should still be handled."

    lines, mappings = compressor.preprocess(text)

    # Should split into 3 sentences
    assert len(lines) == 3
    assert lines[0] == "First sentence."
    assert lines[1] == "Second sentence."
    assert lines[2] == "Third is longer and should still be handled."


def test_preprocess_preserves_code_blocks():
    """Test that code blocks are preserved as-is."""
    compressor = CavemanCompressor()
    text = """Some text.
```python
def hello():
    pass
```
More text."""

    lines, mappings = compressor.preprocess(text)

    # Find code block lines
    code_lines = [(l, m) for l, m in zip(lines, mappings) if m.is_code_block]
    assert len(code_lines) == 4  # Opening, 2 content lines, closing
    assert code_lines[0][0].strip() == "```python"
    assert code_lines[1][0].strip() == "def hello():"
    assert code_lines[2][0].strip() == "pass"
    assert code_lines[3][0].strip() == "```"


def test_preprocess_preserves_structure():
    """Test that headings and lists are preserved."""
    compressor = CavemanCompressor()
    text = """# Heading
- List item 1
- List item 2
Normal text that might be long enough to split. It goes on and on."""

    lines, mappings = compressor.preprocess(text)

    # Find structural elements
    headings = [m for m in mappings if m.is_heading]
    lists = [m for m in mappings if m.is_list_item]

    assert len(headings) == 1
    assert len(lists) == 2


def test_is_fluff_detects_empty():
    """Test fluff detection - only empty strings are considered fluff."""
    compressor = CavemanCompressor()

    # Empty strings are fluff
    assert compressor.is_fluff("")
    assert compressor.is_fluff("   ")

    # Non-empty strings are not fluff (LLM handles actual fluff detection)
    assert not compressor.is_fluff("Sure!")
    assert not compressor.is_fluff("That's right!")
    assert not compressor.is_fluff("The function returns an integer.")


def test_reconstruct_groups_paragraphs():
    """Test that reconstruct groups lines back into paragraphs."""
    compressor = CavemanCompressor()

    lines = ["First sentence.", "Second sentence.", "", "New paragraph."]
    mappings = [
        LineMapping(0, "original", False, False, False, 0),
        LineMapping(0, "original", False, False, False, 0),
        LineMapping(1, "", False, False, False, 0),
        LineMapping(2, "original", False, False, False, 0),
    ]

    result = compressor.reconstruct(lines, mappings)

    assert "First sentence. Second sentence." in result
    assert "New paragraph." in result


def test_should_trigger_cavecompress_first_time():
    """Test initial trigger at 20%."""
    # First trigger at 20% of max
    assert should_trigger_cavecompress(
        current_tokens=2000,
        last_compressed_tokens=0,
        max_context_size=10000,
        trigger_ratio=0.2
    )

    # Not triggered before 20%
    assert not should_trigger_cavecompress(
        current_tokens=1500,
        last_compressed_tokens=0,
        max_context_size=10000,
        trigger_ratio=0.2
    )


def test_should_trigger_cavecompress_growth():
    """Test trigger on growth from last compression."""
    # Compressed at 2000 tokens, now at 2400 (20% growth)
    assert should_trigger_cavecompress(
        current_tokens=2400,
        last_compressed_tokens=2000,
        max_context_size=10000,
        trigger_ratio=0.2
    )

    # Compressed at 2000 tokens, now at 2300 (15% growth - not enough)
    assert not should_trigger_cavecompress(
        current_tokens=2300,
        last_compressed_tokens=2000,
        max_context_size=10000,
        trigger_ratio=0.2
    )


def test_cavecompress_result_stats():
    """Test CavecompressResult token estimation."""
    messages = [
        Message(role="user", content=[TextPart(text="Hello world")]),
        Message(role="assistant", content=[TextPart(text="Hi there")]),
    ]

    result = CavecompressResult(
        messages=messages,
        usage=None,
        lines_processed=10,
        lines_removed=2,
        lines_compressed=8,
        tools_wiped=0,
    )

    # Should estimate from text length
    assert result.estimated_token_count > 0


def test_cavecompress_result_with_usage():
    """Test CavecompressResult with actual token usage."""
    messages = [
        Message(role="user", content=[TextPart(text="Hello")]),
    ]

    result = CavecompressResult(
        messages=messages,
        usage=TokenUsage(input_other=100, output=50),
        lines_processed=5,
        lines_removed=1,
        lines_compressed=4,
        tools_wiped=2,
    )

    assert result.estimated_token_count == 50
    assert result.tools_wiped == 2


def test_wipe_tool_outputs():
    """Test that tool outputs are wiped."""
    messages = [
        Message(role="user", content=[TextPart(text="Run command")]),
        Message(
            role="tool",
            content=[TextPart(text="long output here")],
            tool_call_id="call_1",
        ),
    ]

    import asyncio
    wiped, count = asyncio.run(wipe_tool_outputs(messages))

    assert count == 1
    assert len(wiped) == 2
    # Check that tool message was replaced with placeholder
    tool_msg = wiped[1]
    assert tool_msg.role == "tool"
    text_parts = [p for p in tool_msg.content if isinstance(p, TextPart)]
    assert any("[Tool output wiped]" in p.text for p in text_parts)
