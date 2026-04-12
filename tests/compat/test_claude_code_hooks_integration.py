"""Integration tests for Claude Code hook compatibility.

These tests verify the full pipeline:
  Claude Code settings.json → translate_claude_hooks() → HookEngine → actual execution

This is the critical path: we're proving that a hook defined in Claude Code's
format actually fires, receives the correct stdin JSON, and returns the
correct allow/block decision through kimi's engine.

Tool name translation: Claude Code uses different tool names than kimi:
  Bash → Shell, Write → WriteFile, Edit → StrReplaceFile, Read → ReadFile
Hook matchers are automatically translated so they match kimi's tool names.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from kimi_cli.compat.claude_code import load_claude_code_compat
from kimi_cli.compat.claude_code.hooks import translate_claude_hooks
from kimi_cli.hooks.engine import HookEngine


# ─── End-to-end: settings.json → HookEngine → shell execution ───────────────


@pytest.mark.asyncio
async def test_claude_hook_allow_through_engine():
    """A Claude Code PreToolUse hook that allows goes through kimi's engine."""
    settings = {
        "PreToolUse": [
            {
                "matcher": "Bash",  # Claude name → translated to "Shell"
                "hooks": [
                    {"type": "command", "command": "exit 0", "timeout": 5}
                ],
            }
        ]
    }

    hook_defs = translate_claude_hooks(settings)
    engine = HookEngine(hook_defs)

    # Trigger with kimi's tool name
    results = await engine.trigger(
        "PreToolUse",
        matcher_value="Shell",
        input_data={"tool_name": "Shell", "tool_input": {"command": "ls"}},
    )
    assert len(results) == 1
    assert results[0].action == "allow"


@pytest.mark.asyncio
async def test_claude_hook_block_through_engine():
    """A Claude Code PreToolUse hook that blocks (exit 2) works through kimi's engine."""
    settings = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": "echo 'forbidden' >&2; exit 2",
                        "timeout": 5,
                    }
                ],
            }
        ]
    }

    hook_defs = translate_claude_hooks(settings)
    engine = HookEngine(hook_defs)

    results = await engine.trigger(
        "PreToolUse",
        matcher_value="Shell",
        input_data={"tool_name": "Shell", "tool_input": {"command": "rm -rf /"}},
    )
    assert len(results) == 1
    assert results[0].action == "block"
    assert "forbidden" in results[0].reason


@pytest.mark.asyncio
async def test_claude_hook_json_deny_through_engine():
    """A Claude Code hook returning JSON deny decision works through kimi's engine."""
    deny_json = json.dumps({
        "hookSpecificOutput": {
            "permissionDecision": "deny",
            "permissionDecisionReason": "unsafe operation",
        }
    })
    settings = {
        "PreToolUse": [
            {
                "matcher": "Write|Edit",  # → WriteFile|StrReplaceFile
                "hooks": [
                    {
                        "type": "command",
                        "command": f"echo '{deny_json}'",
                        "timeout": 5,
                    }
                ],
            }
        ]
    }

    hook_defs = translate_claude_hooks(settings)
    engine = HookEngine(hook_defs)

    results = await engine.trigger(
        "PreToolUse",
        matcher_value="WriteFile",  # kimi's tool name
        input_data={"tool_name": "WriteFile", "tool_input": {"file_path": "/etc/passwd"}},
    )
    assert len(results) == 1
    assert results[0].action == "block"
    assert "unsafe operation" in results[0].reason


@pytest.mark.asyncio
async def test_claude_hook_matcher_translates_tool_names():
    """Claude Code tool names in matchers are translated to kimi equivalents."""
    settings = {
        "PreToolUse": [
            {
                "matcher": "Bash|Write",  # → Shell|WriteFile
                "hooks": [
                    {"type": "command", "command": "echo matched", "timeout": 5}
                ],
            }
        ]
    }

    hook_defs = translate_claude_hooks(settings)
    engine = HookEngine(hook_defs)

    # "Shell" should match (translated from "Bash")
    results = await engine.trigger(
        "PreToolUse",
        matcher_value="Shell",
        input_data={"tool_name": "Shell"},
    )
    assert len(results) == 1

    # "WriteFile" should match (translated from "Write")
    results = await engine.trigger(
        "PreToolUse",
        matcher_value="WriteFile",
        input_data={"tool_name": "WriteFile"},
    )
    assert len(results) == 1

    # "ReadFile" should NOT match
    results = await engine.trigger(
        "PreToolUse",
        matcher_value="ReadFile",
        input_data={"tool_name": "ReadFile"},
    )
    assert len(results) == 0


@pytest.mark.asyncio
async def test_claude_hook_receives_correct_stdin():
    """Claude Code hook receives the expected JSON payload on stdin."""
    settings = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": """python3 -c "import sys,json; d=json.load(sys.stdin); print(d['tool_name'])" """,
                        "timeout": 5,
                    }
                ],
            }
        ]
    }

    hook_defs = translate_claude_hooks(settings)
    engine = HookEngine(hook_defs)

    results = await engine.trigger(
        "PreToolUse",
        matcher_value="Shell",
        input_data={"tool_name": "Shell", "tool_input": {"command": "echo hi"}},
    )
    assert len(results) == 1
    assert results[0].stdout.strip() == "Shell"


# ─── Script-based hook: realistic Claude Code hook script ────────────────────


@pytest.mark.asyncio
async def test_claude_hook_script_blocks_dangerous_commands():
    """A realistic Claude Code hook script that blocks rm -rf works in kimi."""
    with tempfile.TemporaryDirectory() as tmpdir:
        script = Path(tmpdir) / "guard.sh"
        script.write_text(
            "#!/bin/bash\n"
            "# Claude Code hook that blocks dangerous shell commands\n"
            "CMD=$(python3 -c \"import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))\")\n"
            'if echo "$CMD" | grep -q "rm -rf"; then\n'
            '  echo "Blocked dangerous command: $CMD" >&2\n'
            "  exit 2\n"
            "fi\n"
            "exit 0\n"
        )
        script.chmod(0o755)

        settings = {
            "PreToolUse": [
                {
                    "matcher": "Bash",  # → Shell
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(script),
                            "timeout": 5,
                        }
                    ],
                }
            ]
        }

        hook_defs = translate_claude_hooks(settings)
        engine = HookEngine(hook_defs, cwd=tmpdir)

        # Safe command → allow
        results = await engine.trigger(
            "PreToolUse",
            matcher_value="Shell",
            input_data={"tool_name": "Shell", "tool_input": {"command": "ls -la"}},
        )
        assert all(r.action == "allow" for r in results)

        # Dangerous command → block
        results = await engine.trigger(
            "PreToolUse",
            matcher_value="Shell",
            input_data={"tool_name": "Shell", "tool_input": {"command": "rm -rf /"}},
        )
        assert any(r.action == "block" for r in results)
        assert "rm -rf" in results[0].reason


# ─── Multiple events from one settings.json ──────────────────────────────────


@pytest.mark.asyncio
async def test_claude_multiple_event_hooks_coexist():
    """Multiple Claude Code hook events in one settings.json all work."""
    settings = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": "echo pre", "timeout": 5}],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": "echo post", "timeout": 5}],
            }
        ],
        "Stop": [
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": "echo stop", "timeout": 5}],
            }
        ],
    }

    hook_defs = translate_claude_hooks(settings)
    engine = HookEngine(hook_defs)

    pre = await engine.trigger("PreToolUse", matcher_value="Shell", input_data={})
    assert len(pre) == 1
    assert pre[0].stdout.strip() == "pre"

    post = await engine.trigger("PostToolUse", matcher_value="Shell", input_data={})
    assert len(post) == 1
    assert post[0].stdout.strip() == "post"

    stop = await engine.trigger("Stop", matcher_value="", input_data={})
    assert len(stop) == 1
    assert stop[0].stdout.strip() == "stop"


# ─── add_hooks: Claude hooks added at runtime ────────────────────────────────


@pytest.mark.asyncio
async def test_claude_hooks_added_to_existing_engine():
    """Claude hooks added via add_hooks() work alongside native kimi hooks."""
    from kimi_cli.hooks.config import HookDef

    # Start with a native kimi hook
    native_hook = HookDef(event="PreToolUse", matcher="Shell", command="echo native", timeout=5)
    engine = HookEngine([native_hook])

    # Add Claude Code hooks (uses Claude name "Bash" → translated to "Shell")
    claude_settings = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": "echo claude", "timeout": 5}],
            }
        ]
    }
    claude_hooks = translate_claude_hooks(claude_settings)
    engine.add_hooks(claude_hooks)

    # Both should fire for "Shell"
    results = await engine.trigger("PreToolUse", matcher_value="Shell", input_data={})
    assert len(results) == 2
    outputs = {r.stdout.strip() for r in results}
    assert outputs == {"native", "claude"}


# ─── Full pipeline: load_claude_code_compat → engine ─────────────────────────


@pytest.mark.asyncio
async def test_full_pipeline_settings_to_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Full pipeline: write settings.json, load compat, feed hooks to engine."""
    fake_claude_home = tmp_path / ".claude_home"
    fake_claude_home.mkdir()
    monkeypatch.setattr(
        "kimi_cli.compat.claude_code.settings.CLAUDE_HOME", fake_claude_home
    )
    monkeypatch.setattr(
        "kimi_cli.compat.claude_code.memory.CLAUDE_HOME", fake_claude_home
    )

    project = tmp_path / "myproject"
    project.mkdir()
    project_claude = project / ".claude"
    project_claude.mkdir()
    (project_claude / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo project-hook-fired",
                                    "timeout": 5,
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )

    monkeypatch.setenv("KIMI_CLAUDE_CODE_COMPAT", "1")

    compat = load_claude_code_compat(project)
    assert compat.enabled
    assert len(compat.extra_hooks) == 1
    assert compat.extra_hooks[0].matcher == "Shell"  # translated from Bash

    # Feed into engine and trigger with kimi's tool name
    engine = HookEngine(compat.extra_hooks)
    results = await engine.trigger(
        "PreToolUse",
        matcher_value="Shell",
        input_data={"tool_name": "Shell", "tool_input": {"command": "echo hello"}},
    )
    assert len(results) == 1
    assert results[0].stdout.strip() == "project-hook-fired"


# ─── Wire callbacks work with translated hooks ───────────────────────────────


@pytest.mark.asyncio
async def test_claude_hooks_fire_wire_callbacks():
    """Wire callbacks (on_triggered, on_resolved) fire for Claude Code hooks."""
    triggered: list[tuple[str, str, int]] = []
    resolved: list[tuple[str, str, str]] = []

    settings = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": "exit 0", "timeout": 5}],
            }
        ]
    }

    hook_defs = translate_claude_hooks(settings)
    engine = HookEngine(
        hook_defs,
        on_triggered=lambda e, t, c: triggered.append((e, t, c)),
        on_resolved=lambda e, t, a, r, d: resolved.append((e, t, a)),
    )

    await engine.trigger("PreToolUse", matcher_value="Shell", input_data={})

    assert len(triggered) == 1
    assert triggered[0] == ("PreToolUse", "Shell", 1)
    assert len(resolved) == 1
    assert resolved[0] == ("PreToolUse", "Shell", "allow")
