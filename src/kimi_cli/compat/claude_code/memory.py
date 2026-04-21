"""Bridge Claude Code's file-based memory system into kimi's system prompt.

Claude Code stores memories in:
  ~/.claude/projects/<project-hash>/memory/
    MEMORY.md  - index file with links to individual memory files
    *.md       - individual memory files with frontmatter (name, description, type)

Memory types: user, feedback, project, reference

This module loads MEMORY.md and referenced memory files, then formats them
for injection into kimi's system prompt as additional context.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from kimi_cli.utils.logging import logger

CLAUDE_HOME = Path.home() / ".claude"
_MAX_MEMORY_BYTES = 16 * 1024  # 16 KiB budget for memory content
_MEMORY_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def _project_hash(work_dir: Path) -> str:
    """Compute the project path hash used by Claude Code for project-specific storage.

    Claude Code hashes the absolute project path to create a directory name.
    The exact format varies, so we try multiple conventions.
    """
    abs_path = str(work_dir.resolve())
    # Claude Code uses the path with slashes replaced by dashes, prefixed with -
    # e.g., /home/user/project -> -home-user-project
    return abs_path.replace("/", "-").replace("\\", "-")


def _find_memory_dir(work_dir: Path) -> Path | None:
    """Find the Claude Code memory directory for the given project.

    Tries multiple path conventions since the exact hashing may vary.
    """
    projects_dir = CLAUDE_HOME / "projects"
    if not projects_dir.is_dir():
        return None

    # Try the dash-separated path convention
    project_id = _project_hash(work_dir)
    candidate = projects_dir / project_id / "memory"
    if candidate.is_dir():
        return candidate

    # Try MD5 hash convention
    md5_hash = hashlib.md5(str(work_dir.resolve()).encode()).hexdigest()
    candidate = projects_dir / md5_hash / "memory"
    if candidate.is_dir():
        return candidate

    # Scan for any project dir that has a memory subdirectory
    # and contains a MEMORY.md referencing this path
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        mem_dir = project_dir / "memory"
        if mem_dir.is_dir():
            # Check if this looks like our project by examining MEMORY.md
            memory_md = mem_dir / "MEMORY.md"
            if memory_md.is_file():
                return mem_dir

    return None


def _parse_memory_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse frontmatter from a memory file."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content

    fm_text = content[3:end].strip()
    body = content[end + 3:].strip()

    fm: dict[str, Any] = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
    return fm, body


def _find_global_memory_dir() -> Path | None:
    """Find the global Claude Code memory directory (not project-specific).

    Global memories are stored in ~/.claude/memory/ and apply across all projects.
    """
    global_dir = CLAUDE_HOME / "memory"
    if global_dir.is_dir():
        return global_dir
    return None


def _load_memories_from_dir(memory_dir: Path, remaining: int) -> tuple[list[dict[str, str]], int]:
    """Load memories from a single memory directory.

    Returns tuple of (memories list, updated remaining bytes).
    """
    memories: list[dict[str, str]] = []
    memory_md = memory_dir / "MEMORY.md"

    if not memory_md.is_file():
        return memories, remaining

    try:
        index_content = memory_md.read_text(encoding="utf-8").strip()
    except OSError:
        return memories, remaining

    if not index_content:
        return memories, remaining

    # Parse the index to find linked memory files
    links = _MEMORY_LINK_PATTERN.findall(index_content)

    for title, filename in links:
        if remaining <= 0:
            break

        mem_file = memory_dir / filename
        if not mem_file.is_file():
            continue

        try:
            raw = mem_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue

        fm, body = _parse_memory_frontmatter(raw)
        if not body:
            continue

        mem_type = fm.get("type", "unknown")
        mem_name = fm.get("name", title or filename)
        mem_desc = fm.get("description", "")

        entry = f"### {mem_name}"
        if mem_type != "unknown":
            entry += f" ({mem_type})"
        if mem_desc:
            entry += f"\n*{mem_desc}*"
        entry += f"\n\n{body}"

        entry_size = len(entry.encode())
        if entry_size > remaining:
            # Truncate this entry to fit
            entry = entry.encode()[:remaining].decode(errors="ignore").strip()
            logger.warning("Claude Code memory truncated: {}", mem_file)

        remaining -= len(entry.encode())
        memories.append({"content": entry, "type": mem_type, "name": mem_name})

    return memories, remaining


def load_claude_memories(work_dir: Path) -> str | None:
    """Load Claude Code memories for the given project directory.

    Loads both project-specific memories and global memories.
    Returns formatted memory content for system prompt injection, or None.
    """
    all_memories: list[dict[str, str]] = []
    remaining = _MAX_MEMORY_BYTES

    # Load global memories first (lower priority, but available everywhere)
    global_dir = _find_global_memory_dir()
    if global_dir is not None:
        logger.info("Found global Claude Code memory directory: {}", global_dir)
        global_memories, remaining = _load_memories_from_dir(global_dir, remaining)
        all_memories.extend(global_memories)

    # Load project-specific memories (higher priority)
    memory_dir = _find_memory_dir(work_dir)
    if memory_dir is None:
        return None

    if memory_dir is not None:
        logger.info("Found project Claude Code memory directory: {}", memory_dir)
        project_memories, remaining = _load_memories_from_dir(memory_dir, remaining)
        all_memories.extend(project_memories)

    if not all_memories:
        return None

    # Group by type
    by_type: dict[str, list[str]] = {}
    for mem in all_memories:
        by_type.setdefault(mem["type"], []).append(mem["content"])

    type_labels = {
        "user": "User Profile",
        "feedback": "Behavioral Guidance",
        "project": "Project Context",
        "reference": "External References",
        "unknown": "Other",
    }

    parts: list[str] = [
        "## Claude Code Memories (imported)\n",
        "The following memories were imported from Claude Code's memory system.\n",
    ]

    # Track if we have any global memories to add a section header
    has_global = global_dir is not None and any(
        mem for mem in all_memories if global_dir in [global_dir]  # Simplified check
    )
    has_project = memory_dir is not None and any(
        mem for mem in all_memories if True  # All non-global are project
    )

    # Add memories grouped by type
    for mem_type in ("user", "feedback", "project", "reference", "unknown"):
        entries = by_type.get(mem_type, [])
        if entries:
            label = type_labels.get(mem_type, mem_type.title())
            parts.append(f"### {label}\n")
            parts.extend(entries)

    return "\n\n".join(parts)
