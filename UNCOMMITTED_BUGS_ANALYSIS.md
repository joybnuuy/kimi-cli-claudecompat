# Comprehensive Bug Analysis: Uncommitted Work in Claude Compat Repo

## Summary

After analyzing the uncommitted work (caveman compression feature + MCP compat layer), I've identified **8 distinct bugs** across multiple categories:

1. **Test file bugs** (3 bugs) - Tests that fail due to incorrect expectations or missing imports
2. **Core implementation bugs** (2 bugs) - Logic errors in caveman.py that cause runtime issues
3. **Integration bugs** (2 bugs) - Mismatches between kimisoul.py and the hook/event system
4. **Design/logic issues** (1 bug) - Hardcoded values that don't reflect actual configuration

---

## 1. Test File Bugs (tests/core/test_caveman_compaction.py)

### Bug 1.1: Missing TokenUsage Import
**Location:** Line 166  
**Issue:** `TokenUsage` is used but not imported  
**Error:** `NameError: name 'TokenUsage' is not defined`  
**Fix:** Add `from kosong.chat_provider import TokenUsage` to imports

```python
# Current (broken):
from kimi_cli.wire.types import TextPart, ToolResult

# Fixed:
from kimi_cli.wire.types import TextPart, ToolResult
from kosong.chat_provider import TokenUsage
```

### Bug 1.2: Wrong ToolResult Constructor Usage
**Location:** Line 185  
**Issue:** Test uses `output=` and `is_error=` kwargs but actual `ToolResult` model uses `return_value`  
**Error:** `pydantic_core.ValidationError: 1 validation error for ToolResult: return_value: Field required`  
**Fix:** Check actual ToolResult schema and fix constructor call

```python
# Current (broken):
ToolResult(tool_call_id="call_1", output="long output here", is_error=False)

# Need to check actual model - likely:
ToolResult(tool_call_id="call_1", return_value="long output here", is_error=False)
```

### Bug 1.3: Wrong Code Block Line Count Expectation
**Location:** Line 46-47  
**Issue:** Test expects 3 code block lines but actual code correctly identifies 4  
**Details:** The test text has:
- ```python (opening fence)
- def hello(): (content line 1)
-     pass (content line 2)
- ``` (closing fence)

That's 4 lines, but test expects only 3 and asserts `code_lines[2][0].strip() == "```"` when position 2 is actually "pass".

**Error:** `AssertionError: assert 'pass' == '```'`  
**Fix:** Update assertion to expect 4 lines, not 3

```python
# Current (broken):
assert len(code_lines) == 4  # Opening, 2 content lines, closing
# ...
assert code_lines[2][0].strip() == "```"  # This is actually "pass"

# Fixed:
assert len(code_lines) == 4  # Opening, 2 content lines, closing
assert code_lines[0][0].strip() == "```python"
assert code_lines[1][0].strip() == "def hello():"
assert code_lines[2][0].strip() == "pass"  # Fixed: position 2 is "pass"
assert code_lines[3][0].strip() == "```"    # Fixed: position 3 is closing fence
```

### Bug 1.4: Test Expects Wrong Tool Wiping Behavior (Potentially)
**Location:** Lines 196-198  
**Issue:** Test checks for "[Tool output wiped]" in assistant message, but `wipe_tool_outputs()` only processes messages with `role="tool"`, not assistant messages containing ToolResult parts  
**Question:** Is this test checking the right thing? Let me verify...

Looking at `wipe_tool_outputs()`:
```python
if msg.role == "tool":
    tools_wiped += 1
    # Replace with placeholder...
```

It only matches messages with `role="tool"`, not `role="assistant"` messages that contain `ToolResult` parts. The test creates an assistant message with a ToolResult part, which won't be wiped.

**Fix:** Either:
1. Update test to use `role="tool"` message
2. Or update `wipe_tool_outputs()` to also handle ToolResult parts in assistant messages

---

## 2. Core Implementation Bugs (src/kimi_cli/soul/caveman.py)

### Bug 2.1: Missing Return Value in compress_message()
**Location:** Line 381  
**Issue:** When there are no text parts to compress, returns 4 values instead of 5  
**Code:**
```python
if not text_parts:
    # No text to compress
    return message, 0, 0, 0  # BUG: Missing 5th value (usage)
```
**Impact:** Callers expect 5 return values: `(compressed_message, lines_processed, lines_removed, lines_compressed, usage)`  
**Fix:**
```python
if not text_parts:
    # No text to compress
    return message, 0, 0, 0, None  # Fixed: added usage=None
```

### Bug 2.2: Wrong Return Type Annotation on compress_message()
**Location:** Line 364  
**Issue:** Return type says `TokenUsage | None` but function can return `None` for usage even when there IS text to compress (when `compress_batch()` returns `None` usage)  
**Code:**
```python
async def compress_message(...) -> Tuple[Message, int, int, int, TokenUsage | None]:
```
**Actually:** This is correct - the issue is the inconsistency at line 381 (Bug 2.1) where usage is completely missing.

---

## 3. Integration Bugs (src/kimi_cli/soul/kimisoul.py)

### Bug 3.1: Hook Event Name Mismatch
**Location:** Lines 1127, 1154  
**Issue:** Triggers `PreCavecompress`/`PostCavecompress` hooks but uses `events.pre_compact`/`events.post_compact` which set `hook_event_name` to `PreCompact`/`PostCompact`  
**Code:**
```python
# Line 1127
await self._hook_engine.trigger(
    "PreCavecompress",  # Hook event name
    matcher_value="manual",
    input_data=events.pre_compact(  # But this sets hook_event_name="PreCompact"
        ...
    ),
)
```
**Impact:** If a hook script checks `hook_event_name` in the JSON it receives, it will see `PreCompact` instead of `PreCavecompress`. This is inconsistent.

**Fix Options:**
1. Create new event functions `pre_cavecompress()`/`post_cavecompress()` in events.py
2. Or use `PreCompact`/`PostCompact` hook names (if the intent is to reuse same hooks)

### Bug 3.2: Missing Hook Event Types
**Location:** src/kimi_cli/hooks/config.py  
**Issue:** `PreCavecompress` and `PostCavecompress` are not in the valid `HookEventType` literal  
**Code:**
```python
HookEventType = Literal[
    "PreToolUse",
    "PostToolUse",
    ...
    "PreCompact",  # Only these exist
    "PostCompact",
    ...
]
```
**Impact:** If someone tries to define a hook for `PreCavecompress` in config, it will fail validation.

**Fix:** Add to HookEventType:
```python
HookEventType = Literal[
    ...
    "PreCompact",
    "PostCompact",
    "PreCavecompress",  # Add these
    "PostCavecompress",
    ...
]
```

---

## 4. Design/Logic Issues

### Bug 4.1: Hardcoded Percentage in Slash Command
**Location:** src/kimi_cli/soul/slash.py, line 82  
**Issue:** Message says "every 20%" but actual interval is configurable via `cavecompress_interval`  
**Code:**
```python
wire_send(TextPart(text=f"Cavecompress auto mode enabled. Will trigger every {interval}% context growth."))
# But interval is computed as:
interval = int(soul._runtime.config.loop_control.cavecompress_interval * 100)  # This is correct
```
**Actually:** Looking closer, this IS using the configured value. The bug is in the message format - it shows the integer percentage (e.g., "20") but the message could be clearer.

Wait, re-reading:
```python
interval = int(soul._runtime.config.loop_control.cavecompress_interval * 100)
wire_send(TextPart(text=f"Cavecompress auto mode enabled. Will trigger every {interval}% context growth."))
```

This IS correct - it uses the configured value. Not a bug.

### Bug 4.2: Potential State Inconsistency on Exception
**Location:** kimisoul.py lines 718-735  
**Issue:** If `cavecompress_context()` raises after partial completion, `_last_cavecompress_tokens` may be in inconsistent state  
**Code:**
```python
try:
    result = await self.cavecompress_context()
    self._last_cavecompress_tokens = result.estimated_token_count  # Only set on success
    ...
except Exception as cave_err:
    logger.error(...)
    # Don't raise - let it continue
```
**Analysis:** Actually this is handled correctly - the state is only updated on success. If an exception occurs, the old value is preserved, which is the right behavior (we'll try again next step).

---

## 5. Shortsighted Logic / Pattern Hardcoding Issues

### Issue 5.1: Tool Name Translation is Hardcoded List
**Location:** src/kimi_cli/compat/claude_code/hooks.py, lines 49-56  
**Issue:** The `_TOOL_NAME_MAP` is a hardcoded list that only covers 6 tools. If Claude Code adds new tools or kimi adds new tools, this mapping will be incomplete.

```python
_TOOL_NAME_MAP: dict[str, str] = {
    "Bash": "Shell",
    "Write": "WriteFile",
    "Edit": "StrReplaceFile",
    "Read": "ReadFile",
    "WebSearch": "SearchWeb",
    "WebFetch": "FetchURL",
}
```

**Potential symptom:** If a user has a Claude Code hook matcher like `"Grep|Glob"`, it won't be translated even though these are common tools in both systems.

**Fix:** Consider making this configurable or logging warnings for untranslated tool names.

### Issue 5.2: MCP Server Type Detection Uses Hardcoded List
**Location:** src/kimi_cli/compat/claude_code/mcp.py, lines 152-159  
**Issue:** WebSocket detection only checks for `"ws"` and `"websocket"`, but there might be other transport types that Claude Code supports but fastmcp doesn't.

```python
elif server_type == "ws" or server_type == "websocket":
    logger.warning(...)
    return None
```

**Potential symptom:** If Claude Code adds a new transport type (e.g., "grpc", "http2"), this code will treat it as stdio and likely fail.

**Fix:** Consider an explicit allowlist for known-supported types and warn on unknown types.

### Issue 5.3: Project Hash Algorithm is Hardcoded Guess
**Location:** src/kimi_cli/compat/claude_code/memory.py, lines 28-38  
**Issue:** The `_project_hash()` function tries multiple hashing strategies (dash-separated path, MD5) but this is based on guesswork about how Claude Code actually hashes project paths.

```python
def _project_hash(work_dir: Path) -> str:
    abs_path = str(work_dir.resolve())
    return abs_path.replace("/", "-").replace("\\", "-")
```

**Potential symptom:** If Claude Code uses a different hashing algorithm (e.g., SHA256, or a salted hash), the memory directory won't be found.

**Fix:** This is documented as "best effort" in the code, but could benefit from user documentation about how to verify the correct path.

### Issue 5.4: Memory Frontmatter Parsing is Naive
**Location:** src/kimi_cli/compat/claude_code/memory.py, lines 76-92  
**Issue:** The frontmatter parser uses simple line splitting that doesn't handle quoted values with colons, nested structures, or YAML lists properly.

```python
for line in fm_text.splitlines():
    if ":" in line:
        key, _, val = line.partition(":")
        fm[key.strip()] = val.strip()
```

**Potential symptom:** If a memory file has a description like `description: "Note: important"`, the parsing will break on the colon inside the value.

**Fix:** Use a proper YAML parser (like the one in PyYAML) for frontmatter.

### Issue 5.5: Settings.json Deep Merge May Not Handle All Cases
**Location:** src/kimi_cli/compat/claude_code/settings.py, lines 39-49  
**Issue:** The `_deep_merge()` function concatenates lists, but Claude Code might expect list replacement or deduplication.

```python
elif key in merged and isinstance(merged[key], list) and isinstance(val, list):
    merged[key] = merged[key] + val  # Simple concatenation
```

**Potential symptom:** If both global and project settings define hooks for the same event, both will fire (which might be intended) but there's no way to override/remove a global hook from project settings.

**Fix:** Document the merge behavior or consider adding a mechanism for "remove" operations.

---

## Recommended Fix Priority

### Critical (breaks functionality):
1. **Bug 2.1** - Missing return value causes unpacking error
2. **Bug 3.2** - Missing HookEventType entries prevent hook configuration

### High (tests fail):
3. **Bug 1.1** - Missing TokenUsage import
4. **Bug 1.2** - Wrong ToolResult constructor
5. **Bug 1.3** - Wrong code block line count
6. **Bug 1.4** - Test checks wrong behavior for tool wiping

### Medium (inconsistency):
7. **Bug 3.1** - Hook event name mismatch

### Low (potential future issues):
8. **Issues 5.1-5.5** - Shortsighted logic that may miss edge cases

---

## Files to Modify

1. `src/kimi_cli/soul/caveman.py` - Fix Bug 2.1
2. `src/kimi_cli/hooks/config.py` - Fix Bug 3.2
3. `src/kimi_cli/hooks/events.py` - Add pre_cavecompress/post_cavecompress (optional, for Bug 3.1)
4. `tests/core/test_caveman_compaction.py` - Fix Bugs 1.1, 1.2, 1.3, 1.4
5. `src/kimi_cli/soul/kimisoul.py` - Optionally fix Bug 3.1 (use PreCompact/PostCompact instead)
