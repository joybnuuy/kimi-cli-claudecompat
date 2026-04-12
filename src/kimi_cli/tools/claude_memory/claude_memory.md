Read, write, update, delete, and list memories in Claude Code's file-based memory system (`~/.claude/projects/<project>/memory/`).

Use this tool to persist knowledge across sessions in a format compatible with Claude Code. Memories are organized by type:
- **user**: Information about the user's role, preferences, and expertise
- **feedback**: Behavioral guidance — what to do or avoid
- **project**: Ongoing work, decisions, and context
- **reference**: Pointers to external resources

Actions:
- `list`: Show all memories in MEMORY.md index
- `read`: Read a specific memory file by name
- `write`: Create or overwrite a memory file with frontmatter
- `delete`: Remove a memory file and its MEMORY.md entry
- `search`: Find memories whose name or description matches a query
