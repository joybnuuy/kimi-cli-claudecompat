"""Tests for the Claude Code compatibility layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kimi_cli.compat.claude_code.hooks import translate_claude_hooks, translate_plugin_hooks
from kimi_cli.compat.claude_code.instructions import (
    _parse_frontmatter,
    _resolve_includes,
    _strip_html_comments,
    load_claude_instructions,
)
from kimi_cli.compat.claude_code.memory import (
    _parse_memory_frontmatter,
    load_claude_memories,
)
from kimi_cli.compat.claude_code.settings import _deep_merge, load_claude_settings


# ─── Settings ────────────────────────────────────────────────────────────────


class TestDeepMerge:
    def test_scalar_override(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_dict_recursive_merge(self):
        base = {"a": {"x": 1, "y": 2}}
        override = {"a": {"y": 3, "z": 4}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 3, "z": 4}}

    def test_list_concatenation(self):
        base = {"hooks": [1, 2]}
        override = {"hooks": [3, 4]}
        result = _deep_merge(base, override)
        assert result == {"hooks": [1, 2, 3, 4]}

    def test_new_keys_added(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


class TestLoadClaudeSettings:
    def test_no_claude_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.settings.CLAUDE_HOME", tmp_path / ".claude_fake"
        )
        result = load_claude_settings(tmp_path)
        assert result == {}

    def test_project_settings(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.settings.CLAUDE_HOME", tmp_path / ".claude_fake"
        )
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {"PreToolUse": [{"matcher": "Bash", "hooks": []}]}
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        result = load_claude_settings(tmp_path)
        assert result["PreToolUse"] == settings["PreToolUse"]

    def test_invalid_json_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.settings.CLAUDE_HOME", tmp_path / ".claude_fake"
        )
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("not json {{{")
        result = load_claude_settings(tmp_path)
        assert result == {}


# ─── Instructions ────────────────────────────────────────────────────────────


class TestParseFrontmatter:
    def test_no_frontmatter(self):
        fm, body = _parse_frontmatter("Hello world")
        assert fm == {}
        assert body == "Hello world"

    def test_basic_frontmatter(self):
        content = "---\nname: test\ntype: user\n---\nBody text"
        fm, body = _parse_frontmatter(content)
        assert fm["name"] == "test"
        assert fm["type"] == "user"
        assert body == "Body text"

    def test_list_frontmatter(self):
        content = '---\npaths: ["src/**/*.ts", "lib/**"]\n---\nContent'
        fm, body = _parse_frontmatter(content)
        assert fm["paths"] == ["src/**/*.ts", "lib/**"]


class TestStripHtmlComments:
    def test_removes_comments(self):
        text = "before <!-- hidden --> after"
        assert _strip_html_comments(text) == "before  after"

    def test_multiline_comment(self):
        text = "start\n<!-- multi\nline\ncomment -->end"
        assert _strip_html_comments(text) == "start\nend"


class TestResolveIncludes:
    def test_include_file(self, tmp_path: Path):
        included = tmp_path / "extra.md"
        included.write_text("included content")
        content = "@include extra.md"
        result = _resolve_includes(content, tmp_path)
        assert result == "included content"

    def test_include_missing_file(self, tmp_path: Path):
        content = "@include nonexistent.md"
        result = _resolve_includes(content, tmp_path)
        assert result == "@include nonexistent.md"

    def test_depth_limit(self, tmp_path: Path):
        # Create a self-referencing include
        f = tmp_path / "loop.md"
        f.write_text("@include loop.md")
        result = _resolve_includes("@include loop.md", tmp_path, depth=5)
        assert "@include loop.md" in result


class TestLoadClaudeInstructions:
    def test_no_claude_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.instructions.Path",
            type(tmp_path),
        )
        # Patch home() to use tmp_path so we don't pick up real ~/.claude
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: fake_home))
        result = load_claude_instructions(tmp_path / "empty_project")
        assert result is None

    def test_root_claude_md(self, tmp_path: Path):
        (tmp_path / "CLAUDE.md").write_text("# Project rules\nDo this.")
        result = load_claude_instructions(tmp_path)
        assert result is not None
        assert "Project rules" in result
        assert "Do this." in result

    def test_dot_claude_dir(self, tmp_path: Path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.md").write_text("Project-level instructions")
        result = load_claude_instructions(tmp_path)
        assert result is not None
        assert "Project-level instructions" in result

    def test_rules_loaded(self, tmp_path: Path):
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "no-console.md").write_text("Never use console.log")
        result = load_claude_instructions(tmp_path)
        assert result is not None
        assert "Never use console.log" in result

    def test_conditional_rules_annotated(self, tmp_path: Path):
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        rule_content = '---\npaths: ["src/**/*.ts"]\n---\nUse strict types'
        (rules_dir / "types.md").write_text(rule_content)
        result = load_claude_instructions(tmp_path)
        assert result is not None
        assert "Applies to: src/**/*.ts" in result
        assert "Use strict types" in result


# ─── Memory ──────────────────────────────────────────────────────────────────


class TestParseMemoryFrontmatter:
    def test_basic(self):
        content = "---\nname: user_role\ndescription: User is a senior dev\ntype: user\n---\nBody"
        fm, body = _parse_memory_frontmatter(content)
        assert fm["name"] == "user_role"
        assert fm["type"] == "user"
        assert body == "Body"


class TestLoadClaudeMemories:
    def test_no_memory_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.memory.CLAUDE_HOME",
            tmp_path / ".claude_fake",
        )
        result = load_claude_memories(tmp_path)
        assert result is None

    def test_loads_memory_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # Create a fake memory directory structure
        project_hash = str(tmp_path.resolve()).replace("/", "-").replace("\\", "-")
        memory_dir = tmp_path / ".claude_home" / "projects" / project_hash / "memory"
        memory_dir.mkdir(parents=True)

        # Patch CLAUDE_HOME
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.memory.CLAUDE_HOME",
            tmp_path / ".claude_home",
        )

        # Create MEMORY.md index
        (memory_dir / "MEMORY.md").write_text(
            "- [User Role](user_role.md) — Senior engineer\n"
            "- [Testing Pref](feedback_tests.md) — Prefers integration tests\n"
        )

        # Create individual memory files
        (memory_dir / "user_role.md").write_text(
            "---\nname: User Role\ndescription: User role info\ntype: user\n---\n"
            "User is a senior backend engineer with Go expertise."
        )
        (memory_dir / "feedback_tests.md").write_text(
            "---\nname: Testing Preference\ndescription: Test guidance\ntype: feedback\n---\n"
            "Always use integration tests with real database.\n\n"
            "**Why:** Mock tests missed a broken migration last quarter.\n\n"
            "**How to apply:** When writing tests for database-touching code."
        )

        result = load_claude_memories(tmp_path)
        assert result is not None
        assert "User Role" in result
        assert "senior backend engineer" in result
        assert "Testing Preference" in result
        assert "integration tests" in result


# ─── Hooks ───────────────────────────────────────────────────────────────────


class TestTranslateClaudeHooks:
    def test_empty_settings(self):
        result = translate_claude_hooks({})
        assert result == []

    def test_nested_hooks_key(self):
        """Real settings.json format: hooks are nested under a 'hooks' key."""
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/home/user/.claude/hooks/rtk-rewrite.sh",
                            }
                        ],
                    }
                ]
            }
        }
        result = translate_claude_hooks(settings)
        assert len(result) == 1
        assert result[0].event == "PreToolUse"
        assert result[0].matcher == "Bash"
        assert result[0].command == "/home/user/.claude/hooks/rtk-rewrite.sh"

    def test_basic_pre_tool_use(self):
        settings = {
            "PreToolUse": [
                {
                    "matcher": "Bash|Write",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/usr/local/bin/audit.sh",
                            "timeout": 10,
                        }
                    ],
                }
            ]
        }
        result = translate_claude_hooks(settings)
        assert len(result) == 1
        hook = result[0]
        assert hook.event == "PreToolUse"
        assert hook.matcher == "Bash|Write"
        assert hook.command == "/usr/local/bin/audit.sh"
        assert hook.timeout == 10

    def test_multiple_events(self):
        settings = {
            "PreToolUse": [
                {
                    "matcher": "Write",
                    "hooks": [{"type": "command", "command": "pre.sh"}],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "",
                    "hooks": [{"type": "command", "command": "post.sh"}],
                }
            ],
        }
        result = translate_claude_hooks(settings)
        assert len(result) == 2
        events = {h.event for h in result}
        assert events == {"PreToolUse", "PostToolUse"}

    def test_skips_http_hooks(self):
        settings = {
            "PreToolUse": [
                {
                    "matcher": "",
                    "hooks": [
                        {"type": "http", "url": "http://localhost:3000/hook"},
                    ],
                }
            ]
        }
        result = translate_claude_hooks(settings)
        assert result == []

    def test_legacy_pattern_field(self):
        settings = {
            "Stop": [
                {
                    "pattern": "stop-matcher",
                    "hooks": [{"type": "command", "command": "stop.sh"}],
                }
            ]
        }
        result = translate_claude_hooks(settings)
        assert len(result) == 1
        assert result[0].matcher == "stop-matcher"

    def test_timeout_clamping(self):
        settings = {
            "PreToolUse": [
                {
                    "matcher": "",
                    "hooks": [
                        {"type": "command", "command": "slow.sh", "timeout": 9999},
                    ],
                }
            ]
        }
        result = translate_claude_hooks(settings)
        assert result[0].timeout == 600

    def test_multiple_hooks_per_matcher(self):
        settings = {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "first.sh"},
                        {"type": "command", "command": "second.sh"},
                    ],
                }
            ]
        }
        result = translate_claude_hooks(settings)
        assert len(result) == 2
        assert result[0].command == "first.sh"
        assert result[1].command == "second.sh"


class TestTranslatePluginHooks:
    def test_wrapped_format(self):
        hooks_config = {
            "description": "My plugin hooks",
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [{"type": "command", "command": "plugin.sh"}],
                    }
                ]
            },
        }
        result = translate_plugin_hooks(hooks_config)
        assert len(result) == 1
        assert result[0].command == "plugin.sh"

    def test_flat_format(self):
        hooks_config = {
            "PreToolUse": [
                {
                    "matcher": "",
                    "hooks": [{"type": "command", "command": "flat.sh"}],
                }
            ]
        }
        result = translate_plugin_hooks(hooks_config)
        assert len(result) == 1
        assert result[0].command == "flat.sh"
