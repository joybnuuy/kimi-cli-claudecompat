"""Load and merge Claude Code settings.json files.

Claude Code stores settings in a hierarchical precedence order:
1. ~/.claude/settings.json (user global)
2. ~/.claude/settings.local.json (user local, gitignored)
3. .claude/settings.json (project, committed)
4. .claude/settings.local.json (project local, gitignored)

Higher-numbered files override lower-numbered ones for scalar values;
hook lists are concatenated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kimi_cli.utils.logging import logger

CLAUDE_HOME = Path.home() / ".claude"


def _load_json_safe(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logger.warning("Claude Code settings is not a JSON object: {}", path)
        return None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read Claude Code settings {}: {}", path, exc)
        return None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge *override* into *base*. Lists are concatenated, dicts are recursively merged."""
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        elif key in merged and isinstance(merged[key], list) and isinstance(val, list):
            merged[key] = merged[key] + val
        else:
            merged[key] = val
    return merged


def load_claude_settings(work_dir: Path) -> dict[str, Any]:
    """Load merged Claude Code settings from all layers.

    Returns an empty dict if no settings files exist.
    """
    layers: list[Path] = [
        CLAUDE_HOME / "settings.json",
        CLAUDE_HOME / "settings.local.json",
        work_dir / ".claude" / "settings.json",
        work_dir / ".claude" / "settings.local.json",
    ]

    merged: dict[str, Any] = {}
    for path in layers:
        data = _load_json_safe(path)
        if data is not None:
            logger.info("Loaded Claude Code settings: {}", path)
            merged = _deep_merge(merged, data)

    return merged
