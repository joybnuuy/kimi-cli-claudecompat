"""Load and merge Claude Code MCP (Model Context Protocol) settings.

Claude Code stores MCP server configurations in multiple locations:
1. ~/.claude.json (user global) - under "mcpServers" key (PRIMARY LOCATION)
2. ~/.claude/settings.json (legacy/alternate)
3. ~/.claude/settings.local.json
4. .claude/settings.json (project)
5. .claude/settings.local.json (project local)

Additionally, Claude Code plugins may have their own .mcp.json files.

This module loads these configurations and converts them to fastmcp.MCPConfig
objects that kimi-cli can use directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kimi_cli.utils.logging import logger

from .settings import CLAUDE_HOME, _deep_merge, _load_json_safe


def _load_claude_json(path: Path = Path.home() / ".claude.json") -> dict[str, Any] | None:
    """Load the main Claude Code configuration file (~/.claude.json).
    
    This is the primary location where Claude Code stores MCP server configs,
    project settings, and user preferences.
    """
    return _load_json_safe(path)


def load_claude_mcp_settings(work_dir: Path) -> dict[str, Any]:
    """Load merged Claude Code MCP settings from all layers.

    This checks multiple locations in order of precedence:
    1. ~/.claude.json (user global mcpServers) - PRIMARY
    2. ~/.claude.json projects.{work_dir}.mcpServers (project-specific)
    3. ~/.claude/settings.json (legacy)
    4. ~/.claude/settings.local.json
    5. .claude/settings.json (project)
    6. .claude/settings.local.json (project local)

    Returns:
        Dict with "mcpServers" key containing merged MCP server configs.
        Returns empty dict if no MCP settings found.
    """
    merged: dict[str, Any] = {}

    # First, check ~/.claude.json (primary location for MCP configs)
    claude_json = _load_claude_json()
    if claude_json is not None:
        # Global mcpServers
        global_mcp = claude_json.get("mcpServers", {})
        if global_mcp:
            logger.info("Loaded global MCP settings from ~/.claude.json")
            merged = _deep_merge(merged, {"mcpServers": global_mcp})
        
        # Project-specific mcpServers
        projects = claude_json.get("projects", {})
        work_dir_str = str(work_dir)
        if work_dir_str in projects:
            project_mcp = projects[work_dir_str].get("mcpServers", {})
            if project_mcp:
                logger.info("Loaded project MCP settings from ~/.claude.json for {}", work_dir)
                merged = _deep_merge(merged, {"mcpServers": project_mcp})

    # Also check legacy settings files
    layers: list[Path] = [
        CLAUDE_HOME / "settings.json",
        CLAUDE_HOME / "settings.local.json",
        work_dir / ".claude" / "settings.json",
        work_dir / ".claude" / "settings.local.json",
    ]

    for path in layers:
        data = _load_json_safe(path)
        if data is not None:
            mcp_data = data.get("mcpServers", {})
            if mcp_data:
                logger.info("Loaded Claude Code MCP settings from: {}", path)
                merged = _deep_merge(merged, {"mcpServers": mcp_data})

    return merged


def load_claude_mcp_config(work_dir: Path) -> list[dict[str, Any]]:
    """Load Claude Code MCP configurations as list of MCPConfig-compatible dicts.

    Args:
        work_dir: The project working directory.

    Returns:
        List of MCP config dicts that can be validated with MCPConfig.model_validate().
        Each dict has the format: {"mcpServers": {...}}
    """
    mcp_settings = load_claude_mcp_settings(work_dir)

    if not mcp_settings or not mcp_settings.get("mcpServers"):
        return []

    # Return as a single config dict with all servers
    return [mcp_settings]


def _convert_claude_mcp_server_to_fastmcp(name: str, config: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a Claude Code MCP server config to fastmcp format.

    Claude Code format examples:
    - HTTP: {"type": "http", "url": "...", "headers": {...}}
    - SSE: {"type": "sse", "url": "...", "headers": {...}}
    - Stdio: {"type": "stdio", "command": "...", "args": [...], "env": {...}}
    - WebSocket: {"type": "ws", "url": "...", "headers": {...}}  # Not supported by fastmcp

    fastmcp format:
    - HTTP: {"url": "...", "transport": "http", "headers": {...}}
    - SSE: {"url": "...", "transport": "sse", "headers": {...}}
    - Stdio: {"command": "...", "args": [...], "env": {...}}

    Args:
        name: Server name
        config: Claude Code server configuration

    Returns:
        fastmcp-compatible server configuration, or None if not supported
    """
    server_type = config.get("type", "stdio")

    if server_type == "http":
        result: dict[str, Any] = {
            "url": config.get("url", ""),
            "transport": "http",
        }
        if "headers" in config:
            result["headers"] = config["headers"]
        if "auth" in config:
            result["auth"] = config["auth"]
        return result

    elif server_type == "sse":
        result = {
            "url": config.get("url", ""),
            "transport": "sse",
        }
        if "headers" in config:
            result["headers"] = config["headers"]
        return result

    elif server_type == "ws" or server_type == "websocket":
        # WebSocket is supported by Claude Code but not by fastmcp
        logger.warning(
            "MCP server '{}' uses WebSocket transport which is not supported by fastmcp. "
            "This server will be skipped. Consider using HTTP or SSE transport instead.",
            name
        )
        return None

    else:  # stdio (default)
        result = {
            "command": config.get("command", ""),
        }
        if "args" in config:
            result["args"] = config["args"]
        if "env" in config:
            result["env"] = config["env"]
        return result


def convert_claude_mcp_to_fastmcp(claude_mcp: dict[str, Any]) -> dict[str, Any]:
    """Convert Claude Code MCP config format to fastmcp format.

    Args:
        claude_mcp: Dict with "mcpServers" key containing Claude-style configs

    Returns:
        Dict with "mcpServers" key in fastmcp format
    """
    if not claude_mcp or "mcpServers" not in claude_mcp:
        return {"mcpServers": {}}

    servers = claude_mcp["mcpServers"]
    converted: dict[str, Any] = {"mcpServers": {}}

    for name, config in servers.items():
        if isinstance(config, dict):
            result = _convert_claude_mcp_server_to_fastmcp(name, config)
            if result is not None:
                converted["mcpServers"][name] = result
            # If result is None, the server was skipped (e.g., WebSocket)
        else:
            # Pass through as-is if not a dict
            converted["mcpServers"][name] = config

    return converted
