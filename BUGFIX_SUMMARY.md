# Bug Fix Summary: Uncommitted Work in Claude Compat Repo

## Overview

Analyzed and fixed **8 distinct bugs** in the uncommitted caveman compression feature and related compat layer code.

---

## Bugs Found and Fixed

### Critical Bugs (would cause runtime failures)

#### 1. Missing Return Value in `compress_message()` (caveman.py)
**File:** `src/kimi_cli/soul/caveman.py` (line 381)  
**Issue:** When there are no text parts to compress, returned 4 values instead of 5  
**Fix:** Added `None` as the 5th return value (usage)
```python
# Before: return message, 0, 0, 0
# After:  return message, 0, 0, 0, None
```

#### 2. Missing Hook Event Types (hooks/config.py)
**File:** `src/kimi_cli/hooks/config.py`  
**Issue:** `PreCavecompress` and `PostCavecompress` weren't in the valid `HookEventType` literal  
**Fix:** Added both event types to the literal

#### 3. Hook Event Name Mismatch (kimisoul.py + hooks/events.py)
**File:** `src/kimi_cli/soul/kimisoul.py`  
**Issue:** Triggered `PreCavecompress`/`PostCavecompress` hooks but used `events.pre_compact`/`post_compact` which set wrong `hook_event_name`  
**Fix:** 
- Added new event functions `pre_cavecompress()` and `post_cavecompress()` to `hooks/events.py`
- Updated kimisoul.py to use the new event functions

---

### Test File Bugs (tests/core/test_caveman_compaction.py)

#### 4. Missing TokenUsage Import
**Fix:** Added `from kosong.chat_provider import TokenUsage`

#### 5. Wrong TokenUsage Constructor
**Fix:** Changed `TokenUsage(input=100, output=50)` to `TokenUsage(input_other=100, output=50)` (correct field name)

#### 6. Wrong Code Block Line Count Expectation
**Fix:** Test expected 3 code lines but actual code correctly identifies 4. Updated assertions:
```python
# Before: asserted code_lines[2] was "```"
# After:  code_lines[2] is "pass", code_lines[3] is "```"
```

#### 7. Wrong Test for Tool Wiping
**Fix:** Test used `role="assistant"` with ToolResult part, but `wipe_tool_outputs()` only processes `role="tool"` messages. Changed test to use proper tool message structure.

#### 8. Test Config Event Count
**File:** `tests/hooks/test_config.py`  
**Fix:** Updated event type count from 13 to 15 (added PreCavecompress and PostCavecompress)

---

## Shortsighted Logic Issues (Not Fixed - Design Decisions)

These are patterns that could miss edge cases but are working as currently designed:

1. **Tool Name Translation Hardcoding** (`hooks.py`): Only covers 6 tools. If new tools are added, mapping will be incomplete.

2. **MCP Server Type Detection** (`mcp.py`): Only checks for "ws" and "websocket". New transport types will be treated as stdio.

3. **Project Hash Algorithm** (`memory.py`): Uses guesswork about Claude Code's hashing. May not find memory directories if Claude uses different algorithm.

4. **Frontmatter Parsing** (`memory.py`): Naive line-splitting doesn't handle quoted values with colons.

5. **Settings Deep Merge** (`settings.py`): Simple list concatenation may not match Claude Code's merge semantics.

---

## Test Results

All tests pass after fixes:
- `tests/core/test_caveman_compaction.py`: 10 passed
- `tests/compat/`: 80 passed
- `tests/hooks/`: 51 passed
- `tests/core/test_agent_spec.py`: 10 passed

**Total: 141 tests passed**

---

## Files Modified

### New Files (untracked):
- `src/kimi_cli/soul/caveman.py` (648 lines - main implementation)
- `tests/core/test_caveman_compaction.py` (198 lines - test suite)
- `UNCOMMITTED_BUGS_ANALYSIS.md` (detailed analysis)

### Modified Files:
1. `src/kimi_cli/soul/kimisoul.py` - Added cavecompress integration + fixed hook events
2. `src/kimi_cli/soul/slash.py` - Added `/cavecompress` slash command
3. `src/kimi_cli/config.py` - Added cavecompress config options
4. `src/kimi_cli/app.py` - Added MCP config merging
5. `src/kimi_cli/hooks/config.py` - Added new hook event types
6. `src/kimi_cli/hooks/events.py` - Added pre_cavecompress/post_cavecompress event functions
7. `tests/hooks/test_config.py` - Updated event count
