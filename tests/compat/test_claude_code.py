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
    _sanitize_path,
    load_claude_memories,
)
from kimi_cli.compat.claude_code.mcp import (
    _convert_claude_mcp_server_to_fastmcp,
    convert_claude_mcp_to_fastmcp,
    load_claude_mcp_settings,
    load_claude_mcp_config,
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


class TestSanitizePath:
    def test_replaces_non_alphanumeric(self):
        assert _sanitize_path("/home/user/my-project") == "-home-user-my-project"
        assert _sanitize_path("/path/with_underscore") == "-path-with-underscore"
        assert _sanitize_path("C:\\Users\\project") == "C--Users-project"

    def test_long_path_gets_hash_suffix(self):
        long_path = "/home/user/" + "a" * 250
        result = _sanitize_path(long_path)
        assert len(result) > 200
        # '-home-user-' is 11 chars, so 189 'a's fit before the 200-char limit
        assert result.startswith("-home-user-" + "a" * 189 + "-")

    def test_djb2_hash_deterministic(self):
        from kimi_cli.compat.claude_code.memory import _djb2_hash

        assert _djb2_hash("hello") == _djb2_hash("hello")
        assert _djb2_hash("hello") != _djb2_hash("world")


class TestFindMemoryDir:
    def test_returns_none_when_no_projects_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.memory.CLAUDE_HOME",
            tmp_path / ".claude_fake",
        )
        from kimi_cli.compat.claude_code.memory import _find_memory_dir

        assert _find_memory_dir(tmp_path) is None

    def test_finds_exact_match(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.memory.CLAUDE_HOME",
            tmp_path / ".claude_home",
        )
        from kimi_cli.compat.claude_code.memory import _find_memory_dir

        slug = _sanitize_path(str(tmp_path.resolve()))
        mem_dir = tmp_path / ".claude_home" / "projects" / slug / "memory"
        mem_dir.mkdir(parents=True)
        assert _find_memory_dir(tmp_path) == mem_dir

    def test_does_not_return_other_projects_memory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Ensure we don't accidentally return another project's memory dir."""
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.memory.CLAUDE_HOME",
            tmp_path / ".claude_home",
        )
        from kimi_cli.compat.claude_code.memory import _find_memory_dir

        # Create another project's memory
        other = tmp_path / ".claude_home" / "projects" / "some-other-project" / "memory"
        other.mkdir(parents=True)
        (other / "MEMORY.md").write_text("")

        # Look for current project — should not find the other one
        assert _find_memory_dir(tmp_path) is None

    def test_loads_memory_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # Create a fake memory directory structure
        project_hash = _sanitize_path(str(tmp_path.resolve()))
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
        assert result[0].matcher == "Shell"  # translated from Bash
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
        assert hook.matcher == "Shell|WriteFile"  # translated from Bash|Write
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


# ─── MCP Settings ────────────────────────────────────────────────────────────


class TestConvertClaudeMcpServer:
    """Tests for _convert_claude_mcp_server_to_fastmcp function."""

    def test_http_server_conversion(self):
        config = {
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {"Authorization": "Bearer token"},
        }
        result = _convert_claude_mcp_server_to_fastmcp("github", config)
        assert result == {
            "url": "https://api.githubcopilot.com/mcp/",
            "transport": "http",
            "headers": {"Authorization": "Bearer token"},
        }

    def test_http_server_with_auth(self):
        config = {
            "type": "http",
            "url": "https://mcp.example.com/",
            "headers": {"X-API-Key": "secret"},
            "auth": "oauth",
        }
        result = _convert_claude_mcp_server_to_fastmcp("example", config)
        assert result["transport"] == "http"
        assert result["auth"] == "oauth"

    def test_sse_server_conversion(self):
        config = {
            "type": "sse",
            "url": "https://mcp.example.com/sse",
            "headers": {"Accept": "text/event-stream"},
        }
        result = _convert_claude_mcp_server_to_fastmcp("sse-server", config)
        assert result == {
            "url": "https://mcp.example.com/sse",
            "transport": "sse",
            "headers": {"Accept": "text/event-stream"},
        }

    def test_stdio_server_conversion(self):
        config = {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
            "env": {"FOO": "bar"},
        }
        result = _convert_claude_mcp_server_to_fastmcp("filesystem", config)
        assert result == {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
            "env": {"FOO": "bar"},
        }

    def test_stdio_server_minimal(self):
        config = {"type": "stdio", "command": "python", "args": ["server.py"]}
        result = _convert_claude_mcp_server_to_fastmcp("minimal", config)
        assert result == {"command": "python", "args": ["server.py"]}

    def test_default_to_stdio(self):
        """When type is missing, default to stdio."""
        config = {"command": "python", "args": ["server.py"]}
        result = _convert_claude_mcp_server_to_fastmcp("default-stdio", config)
        assert result["command"] == "python"
        assert "transport" not in result  # stdio doesn't need transport key

    def test_websocket_returns_none(self, caplog):
        """WebSocket transport returns None with warning (not supported by fastmcp)."""
        config = {"type": "ws", "url": "wss://example.com/mcp"}
        result = _convert_claude_mcp_server_to_fastmcp("ws-server", config)
        assert result is None


class TestConvertClaudeMcpToFastmcp:
    """Tests for convert_claude_mcp_to_fastmcp function."""

    def test_empty_config(self):
        result = convert_claude_mcp_to_fastmcp({})
        assert result == {"mcpServers": {}}

    def test_none_config(self):
        result = convert_claude_mcp_to_fastmcp(None)  # type: ignore[arg-type]
        assert result == {"mcpServers": {}}

    def test_full_config_conversion(self):
        claude_mcp = {
            "mcpServers": {
                "github": {
                    "type": "http",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "headers": {"Authorization": "Bearer token"},
                },
                "filesystem": {
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                },
            }
        }
        result = convert_claude_mcp_to_fastmcp(claude_mcp)
        assert "mcpServers" in result
        assert "github" in result["mcpServers"]
        assert "filesystem" in result["mcpServers"]
        assert result["mcpServers"]["github"]["transport"] == "http"
        assert result["mcpServers"]["filesystem"]["command"] == "npx"


class TestLoadClaudeMcpSettings:
    """Tests for load_claude_mcp_settings function."""

    def test_no_mcp_settings(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """When no settings files exist, return empty dict."""
        # Mock CLAUDE_HOME
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.mcp.CLAUDE_HOME", tmp_path / ".claude_fake"
        )
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.settings.CLAUDE_HOME", tmp_path / ".claude_fake"
        )
        # Also mock the home directory for ~/.claude.json
        fake_claude_json = tmp_path / "nonexistent_claude.json"
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.mcp._load_claude_json",
            lambda path=None: None
        )
        result = load_claude_mcp_settings(tmp_path)
        assert result == {}

    def test_loads_from_global_settings(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Load MCP settings from ~/.claude/settings.json."""
        claude_home = tmp_path / ".claude"
        claude_home.mkdir()
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.mcp.CLAUDE_HOME", claude_home
        )

        settings = {"mcpServers": {"server1": {"type": "http", "url": "https://example.com"}}}
        (claude_home / "settings.json").write_text(json.dumps(settings))

        result = load_claude_mcp_settings(tmp_path)
        assert "mcpServers" in result
        assert "server1" in result["mcpServers"]

    def test_merges_layers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """MCP settings from different layers are merged."""
        # Use separate directories for global and project to avoid conflicts
        global_home = tmp_path / "global_claude"
        global_home.mkdir()
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.mcp.CLAUDE_HOME", global_home
        )

        # Global settings
        global_settings = {"mcpServers": {"global-server": {"type": "http", "url": "https://global.com"}}}
        (global_home / "settings.json").write_text(json.dumps(global_settings))

        # Project settings
        project_claude = tmp_path / ".claude"
        project_claude.mkdir()
        project_settings = {"mcpServers": {"project-server": {"type": "stdio", "command": "python"}}}
        (project_claude / "settings.json").write_text(json.dumps(project_settings))

        result = load_claude_mcp_settings(tmp_path)
        assert "global-server" in result["mcpServers"]
        assert "project-server" in result["mcpServers"]

    def test_project_overrides_global(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Project settings override global settings for same server."""
        global_home = tmp_path / "global_claude"
        global_home.mkdir()
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.mcp.CLAUDE_HOME", global_home
        )

        # Global settings
        global_settings = {"mcpServers": {"shared": {"type": "http", "url": "https://global.com"}}}
        (global_home / "settings.json").write_text(json.dumps(global_settings))

        # Project settings with same server name
        project_claude = tmp_path / ".claude"
        project_claude.mkdir()
        project_settings = {"mcpServers": {"shared": {"type": "http", "url": "https://project.com"}}}
        (project_claude / "settings.json").write_text(json.dumps(project_settings))

        result = load_claude_mcp_settings(tmp_path)
        # Project URL should win
        assert result["mcpServers"]["shared"]["url"] == "https://project.com"


class TestLoadClaudeMcpFromClaudeJson:
    """Tests for loading MCP from ~/.claude.json (primary location)."""

    def test_loads_from_claude_json_global(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Load global MCP settings from ~/.claude.json."""
        # Mock CLAUDE_HOME to avoid interference
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.mcp.CLAUDE_HOME", tmp_path / ".claude_fake"
        )
        
        # Mock _load_claude_json to return test data
        def mock_load_claude_json(path=None):
            return {
                "mcpServers": {
                    "openviking": {
                        "type": "stdio",
                        "command": "python3",
                        "args": ["/path/to/server.py"]
                    }
                }
            }
        
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.mcp._load_claude_json",
            mock_load_claude_json
        )
        
        result = load_claude_mcp_settings(tmp_path)
        assert "mcpServers" in result
        assert "openviking" in result["mcpServers"]
        assert result["mcpServers"]["openviking"]["type"] == "stdio"

    def test_loads_project_specific_from_claude_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Load project-specific MCP settings from ~/.claude.json."""
        work_dir_str = str(tmp_path)
        
        def mock_load_claude_json(path=None):
            return {
                "mcpServers": {
                    "global-server": {"type": "http", "url": "https://global.com"}
                },
                "projects": {
                    work_dir_str: {
                        "mcpServers": {
                            "project-server": {"type": "stdio", "command": "python"}
                        }
                    }
                }
            }
        
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.mcp._load_claude_json",
            mock_load_claude_json
        )
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.mcp.CLAUDE_HOME", tmp_path / ".claude_fake"
        )
        
        result = load_claude_mcp_settings(tmp_path)
        assert "global-server" in result["mcpServers"]
        assert "project-server" in result["mcpServers"]


class TestLoadClaudeMcpConfig:
    """Tests for load_claude_mcp_config function."""

    def test_returns_list_of_configs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Returns list of config dicts compatible with fastmcp.MCPConfig."""
        claude_home = tmp_path / ".claude"
        claude_home.mkdir()
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.mcp.CLAUDE_HOME", claude_home
        )
        # Also mock ~/.claude.json to not interfere
        monkeypatch.setattr(
            "kimi_cli.compat.claude_code.mcp._load_claude_json",
            lambda path=None: None
        )

        settings = {"mcpServers": {"server1": {"type": "http", "url": "https://example.com"}}}
        (claude_home / "settings.json").write_text(json.dumps(settings))

        result = load_claude_mcp_config(tmp_path)
        assert isinstance(result, list)
        assert len(result) == 1
        assert "mcpServers" in result[0]
