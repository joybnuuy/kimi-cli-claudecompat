"""Tests for the ClaudeMemory tool — read/write/delete/search operations."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kimi_cli.tools.claude_memory import ClaudeMemory, Params, _slugify


@pytest.fixture
def memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a fake Claude Code memory directory for a project."""
    project = tmp_path / "project"
    project.mkdir()
    project_hash = str(project.resolve()).replace("/", "-").replace("\\", "-")
    fake_claude_home = tmp_path / ".claude_home"
    mem_dir = fake_claude_home / "projects" / project_hash / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text("")

    monkeypatch.setattr("kimi_cli.tools.claude_memory.CLAUDE_HOME", fake_claude_home)
    monkeypatch.setattr("kimi_cli.compat.claude_code.memory.CLAUDE_HOME", fake_claude_home)
    return mem_dir


@pytest.fixture
def tool(tmp_path: Path, memory_dir: Path) -> ClaudeMemory:
    """Create a ClaudeMemory tool instance with a fake runtime."""
    project = tmp_path / "project"
    runtime = SimpleNamespace(
        builtin_args=SimpleNamespace(KIMI_WORK_DIR=project),
    )
    return ClaudeMemory(runtime)  # type: ignore[arg-type]


class TestSlugify:
    def test_simple(self):
        assert _slugify("User Role") == "user_role"

    def test_special_chars(self):
        assert _slugify("my-project! (v2)") == "my_project_v2"

    def test_long_name_truncated(self):
        result = _slugify("a" * 100)
        assert len(result) <= 60


class TestClaudeMemoryList:
    @pytest.mark.asyncio
    async def test_list_empty(self, tool: ClaudeMemory):
        result = await tool(Params(action="list"))
        assert not result.is_error
        assert "empty" in result.output.lower() or "empty" in (result.message or "").lower()

    @pytest.mark.asyncio
    async def test_list_with_entries(self, tool: ClaudeMemory, memory_dir: Path):
        (memory_dir / "MEMORY.md").write_text(
            "- [User Role](user_role.md) — Senior engineer\n"
        )
        (memory_dir / "user_role.md").write_text(
            "---\nname: User Role\ntype: user\n---\nSenior engineer"
        )
        result = await tool(Params(action="list"))
        assert not result.is_error
        assert "User Role" in result.output
        assert "1" in (result.message or "")


class TestClaudeMemoryWrite:
    @pytest.mark.asyncio
    async def test_write_new_memory(self, tool: ClaudeMemory, memory_dir: Path):
        result = await tool(
            Params(
                action="write",
                name="Test Memory",
                description="A test memory entry",
                memory_type="project",
                content="This is test content.\n\n**Why:** Testing.\n**How to apply:** Always.",
            )
        )
        assert not result.is_error
        assert "Created" in result.output or "Created" in (result.message or "")

        # Verify file was created
        slug_file = memory_dir / "test_memory.md"
        assert slug_file.exists()
        content = slug_file.read_text()
        assert "name: Test Memory" in content
        assert "type: project" in content
        assert "This is test content." in content

        # Verify MEMORY.md was updated
        index = (memory_dir / "MEMORY.md").read_text()
        assert "Test Memory" in index
        assert "test_memory.md" in index

    @pytest.mark.asyncio
    async def test_write_requires_name(self, tool: ClaudeMemory):
        result = await tool(Params(action="write", content="some content"))
        assert result.is_error

    @pytest.mark.asyncio
    async def test_write_requires_content(self, tool: ClaudeMemory):
        result = await tool(Params(action="write", name="Test"))
        assert result.is_error

    @pytest.mark.asyncio
    async def test_write_update_existing(self, tool: ClaudeMemory, memory_dir: Path):
        # Write initial
        await tool(
            Params(
                action="write",
                name="Evolving Memory",
                description="v1",
                content="Version 1",
            )
        )
        # Update
        result = await tool(
            Params(
                action="write",
                name="Evolving Memory",
                description="v2",
                content="Version 2",
            )
        )
        assert not result.is_error
        assert "Updated" in result.output or "Updated" in (result.message or "")

        # Verify content was replaced
        slug_file = memory_dir / "evolving_memory.md"
        content = slug_file.read_text()
        assert "Version 2" in content
        assert "Version 1" not in content


class TestClaudeMemoryRead:
    @pytest.mark.asyncio
    async def test_read_existing(self, tool: ClaudeMemory, memory_dir: Path):
        (memory_dir / "my_mem.md").write_text(
            "---\nname: My Mem\ntype: user\n---\nHello world"
        )
        result = await tool(Params(action="read", name="My Mem"))
        assert not result.is_error
        assert "Hello world" in result.output

    @pytest.mark.asyncio
    async def test_read_by_filename(self, tool: ClaudeMemory, memory_dir: Path):
        (memory_dir / "custom_file.md").write_text("---\nname: Custom\n---\nData")
        result = await tool(Params(action="read", name="custom_file.md"))
        assert not result.is_error
        assert "Data" in result.output

    @pytest.mark.asyncio
    async def test_read_not_found(self, tool: ClaudeMemory):
        result = await tool(Params(action="read", name="nonexistent"))
        assert result.is_error

    @pytest.mark.asyncio
    async def test_read_requires_name(self, tool: ClaudeMemory):
        result = await tool(Params(action="read"))
        assert result.is_error


class TestClaudeMemoryDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self, tool: ClaudeMemory, memory_dir: Path):
        # Create a memory first
        (memory_dir / "to_delete.md").write_text("---\nname: To Delete\n---\nBye")
        (memory_dir / "MEMORY.md").write_text(
            "- [To Delete](to_delete.md) — Will be deleted\n"
        )

        result = await tool(Params(action="delete", name="To Delete"))
        assert not result.is_error
        assert not (memory_dir / "to_delete.md").exists()

        # Verify removed from index
        index = (memory_dir / "MEMORY.md").read_text()
        assert "to_delete.md" not in index

    @pytest.mark.asyncio
    async def test_delete_not_found(self, tool: ClaudeMemory):
        result = await tool(Params(action="delete", name="ghost"))
        assert result.is_error

    @pytest.mark.asyncio
    async def test_delete_requires_name(self, tool: ClaudeMemory):
        result = await tool(Params(action="delete"))
        assert result.is_error


class TestClaudeMemorySearch:
    @pytest.mark.asyncio
    async def test_search_finds_match(self, tool: ClaudeMemory, memory_dir: Path):
        (memory_dir / "backend.md").write_text(
            "---\nname: Backend Stack\ndescription: Go and Postgres\ntype: project\n---\n"
            "We use Go 1.22 with pgx for Postgres."
        )
        (memory_dir / "frontend.md").write_text(
            "---\nname: Frontend Stack\ndescription: React and TypeScript\ntype: project\n---\n"
            "React 18 with Next.js."
        )

        result = await tool(Params(action="search", query="Postgres"))
        assert not result.is_error
        assert "Backend Stack" in result.output
        assert "Frontend Stack" not in result.output

    @pytest.mark.asyncio
    async def test_search_no_match(self, tool: ClaudeMemory, memory_dir: Path):
        (memory_dir / "something.md").write_text("---\nname: Something\n---\nStuff")
        result = await tool(Params(action="search", query="zzznonexistentzz"))
        assert not result.is_error
        assert "No memories matching" in result.output

    @pytest.mark.asyncio
    async def test_search_requires_query(self, tool: ClaudeMemory):
        result = await tool(Params(action="search"))
        assert result.is_error


class TestClaudeMemoryRoundtrip:
    @pytest.mark.asyncio
    async def test_write_then_read_then_search_then_delete(
        self, tool: ClaudeMemory, memory_dir: Path
    ):
        """Full lifecycle: write → read → search → delete."""
        # Write
        write_result = await tool(
            Params(
                action="write",
                name="Lifecycle Test",
                description="Testing full lifecycle",
                memory_type="feedback",
                content="Always test the full lifecycle.\n\n**Why:** Prevents regressions.",
            )
        )
        assert not write_result.is_error

        # Read
        read_result = await tool(Params(action="read", name="Lifecycle Test"))
        assert not read_result.is_error
        assert "full lifecycle" in read_result.output
        assert "type: feedback" in read_result.output

        # Search
        search_result = await tool(Params(action="search", query="lifecycle"))
        assert not search_result.is_error
        assert "Lifecycle Test" in search_result.output

        # List
        list_result = await tool(Params(action="list"))
        assert not list_result.is_error
        assert "Lifecycle Test" in list_result.output

        # Delete
        delete_result = await tool(Params(action="delete", name="Lifecycle Test"))
        assert not delete_result.is_error

        # Verify gone
        read_after = await tool(Params(action="read", name="Lifecycle Test"))
        assert read_after.is_error
