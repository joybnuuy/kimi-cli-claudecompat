"""Claude Code compatibility layer for kimi-cli.

Bridges Claude Code's memory system, hooks, CLAUDE.md instructions, and
settings into kimi's existing infrastructure, so users migrating from
Claude Code can reuse their configuration and memory without changes.

Usage:
    from kimi_cli.compat.claude_code import load_claude_code_compat

    compat = load_claude_code_compat(work_dir)
    # compat.extra_instructions -> str to append to AGENTS.md content
    # compat.extra_hooks -> list[HookDef] to add to hook engine
    # compat.memory_prompt -> str to inject into system prompt
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from kimi_cli.hooks.config import HookDef
from kimi_cli.utils.logging import logger

from .config import ClaudeCodeCompatConfig
from .hooks import translate_claude_hooks
from .instructions import load_claude_instructions
from .memory import load_claude_memories
from .settings import load_claude_settings


@dataclass
class ClaudeCodeCompat:
    """Result of loading Claude Code compatibility data."""

    enabled: bool = False
    """Whether the compat layer is active."""
    extra_instructions: str | None = None
    """CLAUDE.md + rules content to merge with AGENTS.md."""
    extra_hooks: list[HookDef] = field(default_factory=list)
    """Hook definitions translated from Claude Code settings.json."""
    memory_prompt: str | None = None
    """Memory content to inject into the system prompt."""
    settings: dict = field(default_factory=dict)
    """Raw merged Claude Code settings (for advanced use)."""


def load_claude_code_compat(
    work_dir: Path,
    config_data: dict | None = None,
) -> ClaudeCodeCompat:
    """Load all Claude Code compatibility data for the given project.

    This is the main entry point. It:
    1. Checks if compat is enabled (via env/config/auto-detect)
    2. Loads Claude Code settings.json (merged from all layers)
    3. Loads CLAUDE.md instructions and .claude/rules/
    4. Loads memory files from ~/.claude/projects/
    5. Translates hooks from settings.json to kimi HookDef format

    Args:
        work_dir: The project working directory.
        config_data: Optional claude_code section from kimi's config.toml.

    Returns:
        ClaudeCodeCompat with all loaded data.
    """
    cfg = ClaudeCodeCompatConfig.from_env_and_config(config_data)

    if not cfg.enabled:
        logger.debug("Claude Code compatibility layer is disabled")
        return ClaudeCodeCompat(enabled=False)

    logger.info("Claude Code compatibility layer enabled")
    result = ClaudeCodeCompat(enabled=True)

    # Load settings
    try:
        result.settings = load_claude_settings(work_dir)
    except Exception as exc:
        logger.warning("Failed to load Claude Code settings: {}", exc)

    # Load instructions (CLAUDE.md + rules)
    if cfg.instructions:
        try:
            result.extra_instructions = load_claude_instructions(work_dir)
            if result.extra_instructions:
                logger.info("Loaded Claude Code instructions ({} bytes)",
                            len(result.extra_instructions.encode()))
        except Exception as exc:
            logger.warning("Failed to load Claude Code instructions: {}", exc)

    # Load memory
    if cfg.memory:
        try:
            result.memory_prompt = load_claude_memories(work_dir)
            if result.memory_prompt:
                logger.info("Loaded Claude Code memories ({} bytes)",
                            len(result.memory_prompt.encode()))
        except Exception as exc:
            logger.warning("Failed to load Claude Code memories: {}", exc)

    # Translate hooks
    if cfg.hooks and result.settings:
        try:
            result.extra_hooks = translate_claude_hooks(result.settings)
            if result.extra_hooks:
                logger.info("Translated {} Claude Code hook(s)", len(result.extra_hooks))
        except Exception as exc:
            logger.warning("Failed to translate Claude Code hooks: {}", exc)

    return result
