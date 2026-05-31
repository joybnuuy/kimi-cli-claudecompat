"""Configuration for the Claude Code compatibility layer.

Controls which features of the compatibility layer are enabled.
Can be configured via:
- Environment variable: KIMI_CLAUDE_CODE_COMPAT=1 (enable all)
- Environment variable: KIMI_CLAUDE_CODE_COMPAT=0 (disable all)
- kimi config.toml:
    [claude_code]
    enabled = true
    hooks = true
    instructions = true
    memory = true
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from kimi_cli.utils.logging import logger


@dataclass
class ClaudeCodeCompatConfig:
    """Configuration for Claude Code compatibility features."""

    enabled: bool = True
    """Master switch for the entire compatibility layer."""
    hooks: bool = True
    """Load and translate Claude Code hooks from settings.json."""
    instructions: bool = True
    """Load CLAUDE.md and .claude/rules/ as additional project instructions."""
    memory: bool = True
    """Load Claude Code memory files and inject into system prompt."""
    mcp: bool = True
    """Load MCP server configurations from Claude Code settings."""

    @staticmethod
    def from_env_and_config(config_data: dict | None = None) -> ClaudeCodeCompatConfig:
        """Build config from environment variables and optional config dict."""
        cfg = ClaudeCodeCompatConfig()

        # Environment override takes highest precedence
        env_val = os.environ.get("KIMI_CLAUDE_CODE_COMPAT")
        if env_val is not None:
            enabled = env_val.lower() in ("1", "true", "yes", "on")
            cfg.enabled = enabled
            if not enabled:
                cfg.hooks = False
                cfg.instructions = False
                cfg.memory = False
                cfg.mcp = False
                return cfg

        # Config dict overrides
        if config_data and isinstance(config_data, dict):
            if "enabled" in config_data:
                cfg.enabled = bool(config_data["enabled"])
            if not cfg.enabled:
                cfg.hooks = False
                cfg.instructions = False
                cfg.memory = False
                cfg.mcp = False
                return cfg
            if "hooks" in config_data:
                cfg.hooks = bool(config_data["hooks"])
            if "instructions" in config_data:
                cfg.instructions = bool(config_data["instructions"])
            if "memory" in config_data:
                cfg.memory = bool(config_data["memory"])
            if "mcp" in config_data:
                cfg.mcp = bool(config_data["mcp"])

        # Auto-detect: enable only if ~/.claude exists
        claude_home = os.path.expanduser("~/.claude")
        if not os.path.isdir(claude_home) and env_val is None:
            logger.debug("~/.claude not found, Claude Code compat layer disabled")
            cfg.enabled = False
            cfg.hooks = False
            cfg.instructions = False
            cfg.memory = False
            cfg.mcp = False

        return cfg
