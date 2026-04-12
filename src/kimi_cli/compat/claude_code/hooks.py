"""Translate Claude Code hook definitions into kimi's HookDef format.

Claude Code defines hooks in settings.json at the top level:
{
    "PreToolUse": [
        {
            "matcher": "Write|Edit",
            "hooks": [
                {"type": "command", "command": "/path/to/hook.sh", "timeout": 30}
            ]
        }
    ],
    "PostToolUse": [...],
    ...
}

Kimi uses a flat list of HookDef objects:
[
    HookDef(event="PreToolUse", command="...", matcher="...", timeout=30),
    ...
]

This module bridges between the two formats.
"""

from __future__ import annotations

from typing import Any

from kimi_cli.hooks.config import HOOK_EVENT_TYPES, HookDef
from kimi_cli.utils.logging import logger

# Claude Code hook events that map directly to kimi events
_DIRECT_EVENT_MAP: dict[str, str] = {
    "PreToolUse": "PreToolUse",
    "PostToolUse": "PostToolUse",
    "Stop": "Stop",
    "SessionStart": "SessionStart",
    "SessionEnd": "SessionEnd",
    "UserPromptSubmit": "UserPromptSubmit",
    "PreCompact": "PreCompact",
    "PostCompact": "PostCompact",
    "Notification": "Notification",
    "SubagentStop": "SubagentStop",
    "SubagentStart": "SubagentStart",
}

# Claude Code tool names → kimi tool names for matcher rewriting
_TOOL_NAME_MAP: dict[str, str] = {
    "Bash": "Shell",
    "Write": "WriteFile",
    "Edit": "StrReplaceFile",
    "Read": "ReadFile",
    "WebSearch": "SearchWeb",
    "WebFetch": "FetchURL",
}


def _translate_matcher(matcher: str) -> str:
    """Rewrite Claude Code tool names in a matcher pattern to kimi equivalents.

    Handles pipe-separated patterns like "Bash|Write" → "Shell|WriteFile"
    and regex patterns like "mcp__.*" (passed through unchanged).
    """
    if not matcher:
        return matcher

    # Split on pipe, translate each part, rejoin
    parts = matcher.split("|")
    translated = [_TOOL_NAME_MAP.get(part, part) for part in parts]
    result = "|".join(translated)

    if result != matcher:
        logger.debug("Translated hook matcher: {} → {}", matcher, result)

    return result


def translate_claude_hooks(settings: dict[str, Any]) -> list[HookDef]:
    """Extract hook definitions from Claude Code settings and convert to kimi HookDefs.

    Handles both formats:
    - Top-level: {"PreToolUse": [...]} (used in some settings.json variants)
    - Nested: {"hooks": {"PreToolUse": [...]}} (standard settings.json format)

    Args:
        settings: Merged Claude Code settings dict.

    Returns:
        List of kimi-compatible HookDef objects.
    """
    hook_defs: list[HookDef] = []

    # Claude Code settings.json wraps hooks in a "hooks" key
    hooks_section = settings.get("hooks")
    if isinstance(hooks_section, dict):
        hook_defs.extend(_extract_hooks(hooks_section))

    # Also check top-level (some formats put events directly at root)
    hook_defs.extend(_extract_hooks(settings))

    return hook_defs


def _extract_hooks(source: dict[str, Any]) -> list[HookDef]:
    """Extract hook definitions from a dict that has event names as keys."""
    hook_defs: list[HookDef] = []

    for event_name in HOOK_EVENT_TYPES:
        raw_matchers = source.get(event_name)
        if not isinstance(raw_matchers, list):
            continue

        for matcher_group in raw_matchers:
            if not isinstance(matcher_group, dict):
                continue

            # Claude Code uses "matcher" or legacy "pattern" field
            raw_matcher = matcher_group.get("matcher") or matcher_group.get("pattern", "")
            matcher = _translate_matcher(raw_matcher)

            hooks = matcher_group.get("hooks")
            if not isinstance(hooks, list):
                continue

            for hook in hooks:
                if not isinstance(hook, dict):
                    continue

                hook_type = hook.get("type", "command")
                if hook_type != "command":
                    # kimi only supports command hooks; skip http/prompt types
                    logger.info(
                        "Skipping non-command Claude Code hook: type={}, event={}",
                        hook_type,
                        event_name,
                    )
                    continue

                command = hook.get("command", "")
                if not command:
                    continue

                timeout = hook.get("timeout", 30)
                if not isinstance(timeout, int):
                    timeout = 30
                timeout = max(1, min(600, timeout))

                # Map Claude Code event to kimi event
                kimi_event = _DIRECT_EVENT_MAP.get(event_name)
                if kimi_event is None:
                    logger.info(
                        "No kimi equivalent for Claude Code hook event: {}", event_name
                    )
                    continue

                try:
                    hook_def = HookDef(
                        event=kimi_event,  # type: ignore[arg-type]
                        command=command,
                        matcher=matcher,
                        timeout=timeout,
                    )
                    hook_defs.append(hook_def)
                    logger.debug(
                        "Translated Claude Code hook: event={}, matcher={}, command={}",
                        kimi_event,
                        matcher,
                        command,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to create HookDef from Claude Code hook: {}", exc
                    )

    return hook_defs


def translate_plugin_hooks(hooks_config: dict[str, Any]) -> list[HookDef]:
    """Translate hook definitions from a Claude Code plugin's hooks.json.

    Plugin hooks.json wraps the hook definitions in a "hooks" key:
    {
        "description": "...",
        "hooks": {
            "PreToolUse": [...]
        }
    }
    """
    hooks_section = hooks_config.get("hooks")
    if isinstance(hooks_section, dict):
        return translate_claude_hooks(hooks_section)
    # Fall back to treating the whole dict as hook definitions
    return translate_claude_hooks(hooks_config)
