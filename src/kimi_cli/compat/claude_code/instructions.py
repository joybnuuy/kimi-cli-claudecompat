"""Load Claude Code project instructions (CLAUDE.md) and rules.

Claude Code uses several instruction sources:
- CLAUDE.md at project root and nested directories
- ~/.claude/CLAUDE.md for global instructions
- .claude/rules/*.md for conditional rules (with optional paths: frontmatter)

These are loaded and merged with kimi's AGENTS.md system.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kimi_cli.utils.logging import logger

_MAX_BYTES = 32 * 1024  # 32 KiB total budget for claude instructions
_INCLUDE_PATTERN = re.compile(r"^@(?:include\s+)?(\S+.*)$", re.MULTILINE)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter from markdown content.

    Returns (frontmatter_dict, body) or ({}, full_content) if no frontmatter.
    """
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content

    fm_text = content[3:end].strip()
    body = content[end + 3 :].strip()

    fm: dict[str, Any] = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            # Simple list parsing for paths: ["a", "b"]
            if val.startswith("[") and val.endswith("]"):
                items = [s.strip().strip("\"'") for s in val[1:-1].split(",") if s.strip()]
                fm[key] = items
            else:
                fm[key] = val
    return fm, body


def _resolve_includes(content: str, base_dir: Path, depth: int = 0) -> str:
    """Resolve @include directives in CLAUDE.md content."""
    if depth > 5:
        return content

    def _replace(match: re.Match[str]) -> str:
        rel_path = match.group(1).strip()
        target = (base_dir / rel_path).resolve()
        if not target.is_file():
            logger.warning("Claude Code @include target not found: {}", target)
            return match.group(0)
        # Skip binary files
        if target.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".bin"):
            return match.group(0)
        try:
            included = target.read_text(encoding="utf-8")
            return _resolve_includes(included, target.parent, depth + 1)
        except OSError as exc:
            logger.warning("Failed to read @include {}: {}", target, exc)
            return match.group(0)

    return _INCLUDE_PATTERN.sub(_replace, content)


def _load_file_if_exists(path: Path) -> str | None:
    """Read a file and return stripped content, or None if missing/empty."""
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8").strip()
        return content if content else None
    except OSError as exc:
        logger.warning("Failed to read {}: {}", path, exc)
        return None


def _strip_html_comments(content: str) -> str:
    """Remove HTML comments (hidden from Claude when auto-injected)."""
    return re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL).strip()


def load_claude_instructions(work_dir: Path) -> str | None:
    """Load and merge all Claude Code instruction sources.

    Loads from:
    1. ~/.claude/CLAUDE.md (global)
    2. CLAUDE.md in project root and nested directories
    3. .claude/rules/*.md (conditional rules)

    Returns merged content or None if no instructions found.
    """
    claude_home = Path.home() / ".claude"
    parts: list[tuple[str, str]] = []  # (source_label, content)

    # 1. Global CLAUDE.md
    global_md = _load_file_if_exists(claude_home / "CLAUDE.md")
    if global_md:
        global_md = _resolve_includes(global_md, claude_home)
        global_md = _strip_html_comments(global_md)
        if global_md:
            parts.append(("~/.claude/CLAUDE.md", global_md))
            logger.info("Loaded Claude Code global instructions")

    # 2. Project CLAUDE.md - walk from project root to work_dir
    project_root = _find_project_root(work_dir)
    dirs = _dirs_root_to_leaf(work_dir, project_root)

    for d in dirs:
        # .claude/CLAUDE.md (project-local)
        dot_claude_md = d / ".claude" / "CLAUDE.md"
        content = _load_file_if_exists(dot_claude_md)
        if content:
            content = _resolve_includes(content, dot_claude_md.parent)
            content = _strip_html_comments(content)
            if content:
                parts.append((str(dot_claude_md), content))
                logger.info("Loaded Claude Code instructions: {}", dot_claude_md)

        # Root-level CLAUDE.md
        for name in ("CLAUDE.md", "claude.md"):
            root_md = d / name
            content = _load_file_if_exists(root_md)
            if content:
                content = _resolve_includes(content, d)
                content = _strip_html_comments(content)
                if content:
                    parts.append((str(root_md), content))
                    logger.info("Loaded Claude Code instructions: {}", root_md)
                break

    # 3. Rules from .claude/rules/*.md
    rules_dir = work_dir / ".claude" / "rules"
    if rules_dir.is_dir():
        for rule_file in sorted(rules_dir.glob("*.md")):
            raw = _load_file_if_exists(rule_file)
            if not raw:
                continue
            fm, body = _parse_frontmatter(raw)
            # If the rule has path conditions, note them but still include
            # (kimi doesn't have conditional loading, so we include all rules
            # and annotate the paths for the model to decide relevance)
            paths = fm.get("paths")
            if paths and isinstance(paths, list):
                body = f"[Applies to: {', '.join(paths)}]\n\n{body}"
            if body.strip():
                parts.append((str(rule_file), body.strip()))
                logger.info("Loaded Claude Code rule: {}", rule_file)

    # Also check global rules
    global_rules_dir = claude_home / "rules"
    if global_rules_dir.is_dir():
        for rule_file in sorted(global_rules_dir.glob("*.md")):
            raw = _load_file_if_exists(rule_file)
            if not raw:
                continue
            fm, body = _parse_frontmatter(raw)
            paths = fm.get("paths")
            if paths and isinstance(paths, list):
                body = f"[Applies to: {', '.join(paths)}]\n\n{body}"
            if body.strip():
                parts.append((str(rule_file), body.strip()))
                logger.info("Loaded Claude Code global rule: {}", rule_file)

    if not parts:
        return None

    # Assemble with budget
    remaining = _MAX_BYTES
    assembled: list[str] = []
    for source, content in parts:
        annotation = f"<!-- Claude Code: {source} -->\n"
        overhead = len(annotation.encode()) + 2  # separator
        remaining -= overhead
        if remaining <= 0:
            break
        encoded = content.encode()
        if len(encoded) > remaining:
            content = encoded[:remaining].decode(errors="ignore").strip()
            logger.warning("Claude Code instructions truncated: {}", source)
        remaining -= len(content.encode())
        if content:
            assembled.append(f"{annotation}{content}")

    return "\n\n".join(assembled) if assembled else None


def _find_project_root(work_dir: Path) -> Path:
    current = work_dir.resolve()
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return work_dir
        current = parent


def _dirs_root_to_leaf(work_dir: Path, project_root: Path) -> list[Path]:
    dirs: list[Path] = []
    current = work_dir.resolve()
    project_root = project_root.resolve()
    while True:
        dirs.append(current)
        if current == project_root:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    dirs.reverse()
    return dirs
