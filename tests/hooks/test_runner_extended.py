"""Tests for extended hook output features (updated_input, system_message).

These test the extended HookResult fields and the full JSON output parsing
that Claude Code-compatible hooks produce.
"""

from __future__ import annotations

import json

import pytest

from kimi_cli.hooks.config import HookDef
from kimi_cli.hooks.engine import HookEngine
from kimi_cli.hooks.runner import HookResult, run_hook


# ─── updated_input: hooks can rewrite tool arguments ─────────────────────────


@pytest.mark.asyncio
async def test_updated_input_returned():
    """Hook stdout with updatedInput populates result.updated_input."""
    output = json.dumps({
        "hookSpecificOutput": {
            "permissionDecision": "allow",
            "permissionDecisionReason": "RTK auto-rewrite",
            "updatedInput": {"command": "rtk ls"},
        }
    })
    result = await run_hook(f"echo '{output}'", {"tool_name": "Shell"}, timeout=5)
    assert result.action == "allow"
    assert result.updated_input == {"command": "rtk ls"}
    assert result.reason == "RTK auto-rewrite"


@pytest.mark.asyncio
async def test_updated_input_without_permission_decision():
    """Hook can provide updatedInput without a permissionDecision (ask mode)."""
    output = json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": {"command": "rtk git status"},
        }
    })
    result = await run_hook(f"echo '{output}'", {"tool_name": "Shell"}, timeout=5)
    assert result.action == "allow"
    assert result.updated_input == {"command": "rtk git status"}


@pytest.mark.asyncio
async def test_updated_input_with_deny():
    """Hook can deny AND provide updatedInput (deny takes priority)."""
    output = json.dumps({
        "hookSpecificOutput": {
            "permissionDecision": "deny",
            "permissionDecisionReason": "blocked",
            "updatedInput": {"command": "something"},
        }
    })
    result = await run_hook(f"echo '{output}'", {"tool_name": "Shell"}, timeout=5)
    assert result.action == "block"
    assert result.updated_input == {"command": "something"}


@pytest.mark.asyncio
async def test_updated_input_none_when_absent():
    """Hook without updatedInput leaves result.updated_input as None."""
    output = json.dumps({
        "hookSpecificOutput": {
            "permissionDecision": "allow",
        }
    })
    result = await run_hook(f"echo '{output}'", {"tool_name": "Shell"}, timeout=5)
    assert result.updated_input is None


@pytest.mark.asyncio
async def test_updated_input_none_when_not_dict():
    """Non-dict updatedInput is ignored."""
    output = json.dumps({
        "hookSpecificOutput": {
            "updatedInput": "not a dict",
        }
    })
    result = await run_hook(f"echo '{output}'", {"tool_name": "Shell"}, timeout=5)
    assert result.updated_input is None


# ─── system_message: hooks can inject messages into conversation ─────────────


@pytest.mark.asyncio
async def test_system_message_returned():
    """Hook stdout with systemMessage populates result.system_message."""
    output = json.dumps({
        "systemMessage": "The command was rewritten by RTK for token savings.",
        "hookSpecificOutput": {
            "permissionDecision": "allow",
        },
    })
    result = await run_hook(f"echo '{output}'", {"tool_name": "Shell"}, timeout=5)
    assert result.system_message == "The command was rewritten by RTK for token savings."


@pytest.mark.asyncio
async def test_system_message_empty_when_absent():
    """Hook without systemMessage leaves it empty."""
    result = await run_hook("echo '{}'", {"tool_name": "Shell"}, timeout=5)
    assert result.system_message == ""


# ─── permissionDecision: allow/deny/ask ──────────────────────────────────────


@pytest.mark.asyncio
async def test_permission_allow():
    """permissionDecision=allow results in action=allow."""
    output = json.dumps({
        "hookSpecificOutput": {
            "permissionDecision": "allow",
            "permissionDecisionReason": "auto-approved",
        }
    })
    result = await run_hook(f"echo '{output}'", {"tool_name": "Shell"}, timeout=5)
    assert result.action == "allow"
    assert result.reason == "auto-approved"


@pytest.mark.asyncio
async def test_permission_deny():
    """permissionDecision=deny results in action=block."""
    output = json.dumps({
        "hookSpecificOutput": {
            "permissionDecision": "deny",
            "permissionDecisionReason": "use rg instead",
        }
    })
    result = await run_hook(f"echo '{output}'", {"tool_name": "Shell"}, timeout=5)
    assert result.action == "block"
    assert result.reason == "use rg instead"


@pytest.mark.asyncio
async def test_permission_ask():
    """permissionDecision=ask results in action=allow (defers to normal approval)."""
    output = json.dumps({
        "hookSpecificOutput": {
            "permissionDecision": "ask",
            "updatedInput": {"command": "rtk dangerous-cmd"},
        }
    })
    result = await run_hook(f"echo '{output}'", {"tool_name": "Shell"}, timeout=5)
    assert result.action == "allow"
    assert result.updated_input == {"command": "rtk dangerous-cmd"}


# ─── RTK-style rewrite: realistic end-to-end ─────────────────────────────────


@pytest.mark.asyncio
async def test_rtk_style_rewrite_allow(tmp_path):
    """Simulate RTK exit 0: rewrite command and auto-allow."""
    script = tmp_path / "rtk_allow.sh"
    script.write_text(
        '#!/bin/bash\n'
        'echo \'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
        '"permissionDecision":"allow","permissionDecisionReason":"RTK auto-rewrite",'
        '"updatedInput":{"command":"rtk ls"}}}\'\n'
    )
    script.chmod(0o755)
    result = await run_hook(
        str(script),
        {"tool_name": "Shell", "tool_input": {"command": "ls"}},
        timeout=5,
    )
    assert result.action == "allow"
    assert result.updated_input == {"command": "rtk ls"}
    assert result.reason == "RTK auto-rewrite"


@pytest.mark.asyncio
async def test_rtk_style_rewrite_ask(tmp_path):
    """Simulate RTK exit 3: rewrite command but ask user."""
    script = tmp_path / "rtk_ask.sh"
    script.write_text(
        '#!/bin/bash\n'
        'echo \'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
        '"updatedInput":{"command":"rtk git push"}}}\'\n'
    )
    script.chmod(0o755)
    result = await run_hook(
        str(script),
        {"tool_name": "Shell", "tool_input": {"command": "git push"}},
        timeout=5,
    )
    assert result.action == "allow"
    assert result.updated_input == {"command": "rtk git push"}


# ─── Engine integration: updated_input flows through HookEngine ──────────────


@pytest.mark.asyncio
async def test_engine_returns_updated_input():
    """HookEngine preserves updated_input from hook results."""
    output = json.dumps({
        "hookSpecificOutput": {
            "permissionDecision": "allow",
            "updatedInput": {"command": "rtk git diff"},
        }
    })
    hooks = [
        HookDef(
            event="PreToolUse",
            matcher="Shell",
            command=f"echo '{output}'",
            timeout=5,
        )
    ]
    engine = HookEngine(hooks)
    results = await engine.trigger(
        "PreToolUse",
        matcher_value="Shell",
        input_data={"tool_name": "Shell", "tool_input": {"command": "git diff"}},
    )
    assert len(results) == 1
    assert results[0].updated_input == {"command": "rtk git diff"}


@pytest.mark.asyncio
async def test_engine_multiple_hooks_last_updated_input_wins():
    """When multiple hooks provide updatedInput, all are returned."""
    output1 = json.dumps({
        "hookSpecificOutput": {
            "permissionDecision": "allow",
            "updatedInput": {"command": "hook1-rewrite"},
        }
    })
    output2 = json.dumps({
        "hookSpecificOutput": {
            "permissionDecision": "allow",
            "updatedInput": {"command": "hook2-rewrite"},
        }
    })
    hooks = [
        HookDef(event="PreToolUse", matcher="Shell", command=f"echo '{output1}'", timeout=5),
        HookDef(event="PreToolUse", matcher="Shell", command=f"echo '{output2}'", timeout=5),
    ]
    engine = HookEngine(hooks)
    results = await engine.trigger(
        "PreToolUse",
        matcher_value="Shell",
        input_data={"tool_name": "Shell", "tool_input": {"command": "ls"}},
    )
    assert len(results) == 2
    # Both hooks returned updatedInput
    updated = [r.updated_input for r in results if r.updated_input is not None]
    assert len(updated) == 2


# ─── Engine integration: system_message flows through HookEngine ─────────────


@pytest.mark.asyncio
async def test_engine_returns_system_message():
    """HookEngine preserves system_message from hook results."""
    output = json.dumps({
        "systemMessage": "Rewritten for token savings.",
        "hookSpecificOutput": {
            "permissionDecision": "allow",
        }
    })
    hooks = [
        HookDef(
            event="PreToolUse",
            matcher="Shell",
            command=f"echo '{output}'",
            timeout=5,
        )
    ]
    engine = HookEngine(hooks)
    results = await engine.trigger(
        "PreToolUse",
        matcher_value="Shell",
        input_data={"tool_name": "Shell", "tool_input": {"command": "ls"}},
    )
    assert len(results) == 1
    assert results[0].system_message == "Rewritten for token savings."


# ─── Backward compatibility: existing hooks still work ───────────────────────


@pytest.mark.asyncio
async def test_exit_0_still_allows():
    """Plain exit 0 (no JSON) still works."""
    result = await run_hook("echo ok", {"tool_name": "Shell"}, timeout=5)
    assert result.action == "allow"
    assert result.updated_input is None
    assert result.system_message == ""


@pytest.mark.asyncio
async def test_exit_2_still_blocks():
    """Exit 2 with stderr still blocks."""
    result = await run_hook("echo 'nope' >&2; exit 2", {"tool_name": "Shell"}, timeout=5)
    assert result.action == "block"
    assert "nope" in result.reason
    assert result.updated_input is None


@pytest.mark.asyncio
async def test_timeout_still_allows():
    """Timeout still fail-opens."""
    result = await run_hook("sleep 10", {"tool_name": "Shell"}, timeout=1)
    assert result.action == "allow"
    assert result.timed_out
    assert result.updated_input is None
