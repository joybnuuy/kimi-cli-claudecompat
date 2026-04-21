"""Claude Code memory read/write tool for kimi-cli.

Provides a tool that lets kimi read, write, update, delete, and search
memories stored in Claude Code's file-based memory system.
"""

import re
from pathlib import Path
from typing import Literal, override

from kosong.tooling import CallableTool2, ToolError, ToolOk, ToolReturnValue
from pydantic import BaseModel, Field

from kimi_cli.compat.claude_code.memory import (
    CLAUDE_HOME,
    _find_global_memory_dir,
    _find_memory_dir,
    _parse_memory_frontmatter,
    _project_hash,
)
from kimi_cli.soul.agent import Runtime
from kimi_cli.tools.utils import load_desc
from kimi_cli.utils.logging import logger

_MEMORY_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_DESC = load_desc(Path(__file__).parent / "claude_memory.md")


def _slugify(name: str) -> str:
    """Convert a memory name to a filename-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug[:60] if slug else "memory"


def _ensure_memory_dir(work_dir: Path) -> Path:
    """Find or create the memory directory for this project."""
    mem_dir = _find_memory_dir(work_dir)
    if mem_dir is not None:
        return mem_dir

    # Create it using the dash-path convention
    project_id = _project_hash(work_dir)
    mem_dir = CLAUDE_HOME / "projects" / project_id / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)

    # Create empty MEMORY.md
    memory_md = mem_dir / "MEMORY.md"
    if not memory_md.exists():
        memory_md.write_text("")

    logger.info("Created Claude Code memory directory: {}", mem_dir)
    return mem_dir


def _update_memory_index(mem_dir: Path, filename: str, title: str, hook: str) -> None:
    """Add or update an entry in MEMORY.md index."""
    memory_md = mem_dir / "MEMORY.md"
    existing = memory_md.read_text(encoding="utf-8") if memory_md.exists() else ""

    # Remove existing entry for this file
    lines = [
        line
        for line in existing.splitlines()
        if not (f"({filename})" in line)
    ]

    # Add new entry
    entry = f"- [{title}]({filename}) — {hook}"
    lines.append(entry)

    # Remove blank lines and write
    content = "\n".join(line for line in lines if line.strip()) + "\n"
    memory_md.write_text(content, encoding="utf-8")


def _remove_from_index(mem_dir: Path, filename: str) -> None:
    """Remove an entry from MEMORY.md index."""
    memory_md = mem_dir / "MEMORY.md"
    if not memory_md.exists():
        return
    existing = memory_md.read_text(encoding="utf-8")
    lines = [
        line
        for line in existing.splitlines()
        if not (f"({filename})" in line)
    ]
    content = "\n".join(line for line in lines if line.strip()) + "\n" if lines else ""
    memory_md.write_text(content, encoding="utf-8")


class Params(BaseModel):
    action: Literal["list", "read", "write", "delete", "search"] = Field(
        description=(
            "The action to perform. "
            "`list` shows all memories, "
            "`read` reads a specific memory by name, "
            "`write` creates or updates a memory, "
            "`delete` removes a memory, "
            "`search` finds memories matching a query."
        )
    )
    name: str = Field(
        default="",
        description=(
            "The memory name. Required for `read`, `write`, and `delete`. "
            "For `write`, this becomes the memory's title."
        ),
    )
    description: str = Field(
        default="",
        description=(
            "A one-line description of the memory. Required for `write`. "
            "Used to decide relevance in future conversations."
        ),
    )
    memory_type: Literal["user", "feedback", "project", "reference"] = Field(
        default="project",
        description=(
            "The type of memory. Used for `write`. "
            "Options: user, feedback, project, reference."
        ),
    )
    content: str = Field(
        default="",
        description=(
            "The memory content body. Required for `write`. "
            "For feedback/project types, structure as: "
            "rule/fact, then **Why:** and **How to apply:** lines."
        ),
    )
    query: str = Field(
        default="",
        description="Search query for `search` action. Matches against name and description.",
    )
    scope: Literal["project", "global", "all"] = Field(
        default="project",
        description=(
            "The scope for the action. "
            "`project` = current project only, "
            "`global` = global memories only, "
            "`all` = both project and global. "
            "Applies to `list`, `read`, `write`, `search`."
        ),
    )


class ClaudeMemory(CallableTool2[Params]):
    name: str = "ClaudeMemory"
    description: str = _DESC
    params: type[Params] = Params

    def __init__(self, runtime: Runtime):
        super().__init__()
        self._work_dir = Path(str(runtime.builtin_args.KIMI_WORK_DIR))

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        try:
            match params.action:
                case "list":
                    return self._list_memories(params.scope)
                case "read":
                    return self._read_memory(params.name, params.scope)
                case "write":
                    return self._write_memory(
                        params.name,
                        params.description,
                        params.memory_type,
                        params.content,
                        params.scope,
                    )
                case "delete":
                    return self._delete_memory(params.name, params.scope)
                case "search":
                    return self._search_memories(params.query, params.scope)
                case _:
                    return ToolError(
                        message=f"Unknown action: {params.action}",
                        brief="Invalid action",
                    )
        except Exception as exc:
            logger.warning("ClaudeMemory error: {}", exc)
            return ToolError(
                message=f"Memory operation failed: {exc}",
                brief="Memory error",
            )

    def _list_memories(self, scope: str = "project") -> ToolReturnValue:
        parts: list[str] = []
        total_files = 0

        # List project memories
        if scope in ("project", "all"):
            mem_dir = _find_memory_dir(self._work_dir)
            if mem_dir is not None:
                memory_md = mem_dir / "MEMORY.md"
                if memory_md.exists() and memory_md.read_text().strip():
                    index = memory_md.read_text(encoding="utf-8").strip()
                    md_files = list(mem_dir.glob("*.md"))
                    file_count = len([f for f in md_files if f.name != "MEMORY.md"])
                    total_files += file_count
                    parts.append(f"## Project Memories ({mem_dir})\n{file_count} file(s)\n\n{index}")
                else:
                    parts.append(f"## Project Memories ({mem_dir})\nMEMORY.md is empty.")
            elif scope == "project":
                return ToolOk(
                    output="No Claude Code memory directory found for this project.",
                    message="No memories exist yet. Use `write` to create one.",
                )

        # List global memories
        if scope in ("global", "all"):
            global_dir = _find_global_memory_dir()
            if global_dir is not None:
                memory_md = global_dir / "MEMORY.md"
                if memory_md.exists() and memory_md.read_text().strip():
                    index = memory_md.read_text(encoding="utf-8").strip()
                    md_files = list(global_dir.glob("*.md"))
                    file_count = len([f for f in md_files if f.name != "MEMORY.md"])
                    total_files += file_count
                    parts.append(f"## Global Memories ({global_dir})\n{file_count} file(s)\n\n{index}")
                else:
                    parts.append(f"## Global Memories ({global_dir})\nMEMORY.md is empty.")
            elif scope == "global":
                return ToolOk(
                    output="No global Claude Code memory directory found.",
                    message="Create ~/.claude/memory/ and MEMORY.md to use global memories.",
                )

        if not parts:
            return ToolOk(output="No memories found.", message="No memories stored.")

        return ToolOk(
            output="\n\n---\n\n".join(parts),
            message=f"Found {total_files} memory file(s) total.",
        )

    def _read_memory(self, name: str, scope: str = "project") -> ToolReturnValue:
        if not name:
            return ToolError(message="Memory name is required for `read`.", brief="Missing name")

        # Search in specified scope
        locations: list[tuple[str, Path]] = []

        if scope in ("project", "all"):
            mem_dir = _find_memory_dir(self._work_dir)
            if mem_dir is not None:
                locations.append(("project", mem_dir))

        if scope in ("global", "all"):
            global_dir = _find_global_memory_dir()
            if global_dir is not None:
                locations.append(("global", global_dir))

        if not locations:
            return ToolError(
                message="No Claude Code memory directories found.",
                brief="No memory dir",
            )

        # Try to find in each location
        for loc_name, loc_dir in locations:
            target = self._find_memory_file(loc_dir, name)
            if target is not None:
                content = target.read_text(encoding="utf-8")
                return ToolOk(
                    output=content,
                    message=f"Read memory from {loc_name}: {target.name}"
                )

        return ToolError(
            message=f"Memory not found: {name}",
            brief="Not found",
        )

    def _write_memory(
        self,
        name: str,
        description: str,
        memory_type: str,
        content: str,
        scope: str = "project",
    ) -> ToolReturnValue:
        if not name:
            return ToolError(message="Memory name is required for `write`.", brief="Missing name")
        if not content:
            return ToolError(
                message="Memory content is required for `write`.", brief="Missing content"
            )
        if not description:
            description = name

        # Determine target directory based on scope
        if scope == "global":
            mem_dir = _find_global_memory_dir()
            if mem_dir is None:
                # Create global memory directory
                mem_dir = CLAUDE_HOME / "memory"
                mem_dir.mkdir(parents=True, exist_ok=True)
                # Create empty MEMORY.md
                memory_md = mem_dir / "MEMORY.md"
                if not memory_md.exists():
                    memory_md.write_text("")
                logger.info("Created global Claude Code memory directory: {}", mem_dir)
        else:
            mem_dir = _ensure_memory_dir(self._work_dir)

        filename = _slugify(name) + ".md"

        # Check if updating an existing file
        existing = self._find_memory_file(mem_dir, name)
        if existing is not None:
            filename = existing.name

        file_path = mem_dir / filename

        # Write memory file with frontmatter
        memory_content = (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"type: {memory_type}\n"
            f"---\n\n"
            f"{content}\n"
        )
        file_path.write_text(memory_content, encoding="utf-8")

        # Update MEMORY.md index
        hook = description[:120]
        _update_memory_index(mem_dir, filename, name, hook)

        location = "global" if scope == "global" else "project"
        action = "Updated" if existing else "Created"
        logger.info("{} {} Claude Code memory: {} -> {}", action, location, name, file_path)
        return ToolOk(
            output=f"{action} {location} memory: {filename}",
            message=f"{action} memory '{name}' in {mem_dir}",
        )

    def _delete_memory(self, name: str, scope: str = "project") -> ToolReturnValue:
        if not name:
            return ToolError(message="Memory name is required for `delete`.", brief="Missing name")

        # Search in specified scope
        locations: list[tuple[str, Path]] = []

        if scope in ("project", "all"):
            mem_dir = _find_memory_dir(self._work_dir)
            if mem_dir is not None:
                locations.append(("project", mem_dir))

        if scope in ("global", "all"):
            global_dir = _find_global_memory_dir()
            if global_dir is not None:
                locations.append(("global", global_dir))

        if not locations:
            return ToolError(
                message="No Claude Code memory directories found.",
                brief="No memory dir",
            )

        # Try to find and delete in each location
        for loc_name, loc_dir in locations:
            target = self._find_memory_file(loc_dir, name)
            if target is not None:
                # Remove the file
                target.unlink()
                # Remove from index
                _remove_from_index(loc_dir, target.name)
                logger.info("Deleted {} Claude Code memory: {} ({})", loc_name, name, target)
                return ToolOk(
                    output=f"Deleted {loc_name} memory: {target.name}",
                    message=f"Deleted memory '{name}' from {loc_name}",
                )

        return ToolError(message=f"Memory not found: {name}", brief="Not found")

    def _search_memories(self, query: str, scope: str = "project") -> ToolReturnValue:
        if not query:
            return ToolError(
                message="Search query is required for `search`.", brief="Missing query"
            )

        # Collect directories to search
        dirs_to_search: list[tuple[str, Path]] = []

        if scope in ("project", "all"):
            mem_dir = _find_memory_dir(self._work_dir)
            if mem_dir is not None:
                dirs_to_search.append(("project", mem_dir))

        if scope in ("global", "all"):
            global_dir = _find_global_memory_dir()
            if global_dir is not None:
                dirs_to_search.append(("global", global_dir))

        if not dirs_to_search:
            return ToolOk(output="No memory directories found.", message="No memories to search.")

        query_lower = query.lower()
        results: list[str] = []

        for loc_name, mem_dir in dirs_to_search:
            for md_file in sorted(mem_dir.glob("*.md")):
                if md_file.name == "MEMORY.md":
                    continue

                try:
                    raw = md_file.read_text(encoding="utf-8")
                except OSError:
                    continue

                fm, body = _parse_memory_frontmatter(raw)
                mem_name = fm.get("name", md_file.stem)
                mem_desc = fm.get("description", "")
                mem_type = fm.get("type", "unknown")

                # Search in name, description, and body
                searchable = f"{mem_name} {mem_desc} {body}".lower()
                if query_lower in searchable:
                    preview = body[:200].replace("\n", " ")
                    results.append(
                        f"- **{mem_name}** ({mem_type}, {loc_name}) — {mem_desc}\n  File: {md_file.name}\n  Preview: {preview}"
                    )

        if not results:
            return ToolOk(
                output=f"No memories matching '{query}'.",
                message="No matches found.",
            )

        output = f"Found {len(results)} match(es) for '{query}':\n\n" + "\n\n".join(results)
        return ToolOk(output=output, message=f"Found {len(results)} matching memory/memories.")

    def _find_memory_file(self, mem_dir: Path, name: str) -> Path | None:
        """Find a memory file by name, slug, or filename."""
        # Try exact filename
        exact = mem_dir / name
        if exact.is_file():
            return exact

        # Try with .md extension
        if not name.endswith(".md"):
            exact_md = mem_dir / (name + ".md")
            if exact_md.is_file():
                return exact_md

        # Try slugified name
        slug = _slugify(name) + ".md"
        slug_path = mem_dir / slug
        if slug_path.is_file():
            return slug_path

        # Search by frontmatter name
        for md_file in mem_dir.glob("*.md"):
            if md_file.name == "MEMORY.md":
                continue
            try:
                raw = md_file.read_text(encoding="utf-8")
                fm, _ = _parse_memory_frontmatter(raw)
                if fm.get("name", "").lower() == name.lower():
                    return md_file
            except OSError:
                continue

        return None
