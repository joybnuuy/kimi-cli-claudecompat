from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal, cast

from kimi_cli import logger


@dataclass
class HookResult:
    """Result of a single hook execution."""

    action: Literal["allow", "block"] = "allow"
    reason: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    updated_input: dict[str, Any] | None = None
    """If set, the hook wants to rewrite the tool's input arguments."""
    system_message: str = ""
    """If set, a message to inject into the conversation context."""


async def run_hook(
    command: str,
    input_data: dict[str, Any],
    *,
    timeout: int = 30,
    cwd: str | None = None,
) -> HookResult:
    """Execute a single hook command. Fail-open: errors/timeouts -> allow."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=json.dumps(input_data).encode()),
                timeout=timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("Hook timed out after {}s: {}", timeout, command)
            return HookResult(action="allow", timed_out=True)
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            raise
    except Exception as e:
        logger.warning("Hook failed: {}: {}", command, e)
        return HookResult(action="allow", stderr=str(e))

    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")
    exit_code = proc.returncode or 0

    # Exit 2 = block
    if exit_code == 2:
        return HookResult(
            action="block",
            reason=stderr.strip(),
            stdout=stdout,
            stderr=stderr,
            exit_code=2,
        )

    # Exit 0 + JSON stdout = structured decision
    if exit_code == 0 and stdout.strip():
        try:
            raw = json.loads(stdout)
            if isinstance(raw, dict):
                return _parse_hook_json(cast(dict[str, Any], raw), stdout, stderr)
        except (json.JSONDecodeError, TypeError):
            pass

    return HookResult(action="allow", stdout=stdout, stderr=stderr, exit_code=exit_code)


def _parse_hook_json(
    raw: dict[str, Any], stdout: str, stderr: str
) -> HookResult:
    """Parse Claude Code-compatible JSON output from a hook.

    Supported fields (all optional):
    - hookSpecificOutput.permissionDecision: "allow" | "deny" | "ask"
    - hookSpecificOutput.permissionDecisionReason: str
    - hookSpecificOutput.updatedInput: dict (rewrite tool arguments)
    - systemMessage: str (inject into conversation)

    Exit code semantics:
    - 0 + permissionDecision="allow" → auto-approve
    - 0 + permissionDecision="deny"  → block
    - 0 + permissionDecision="ask"   → pass through (no auto-approve)
    - 0 + no permissionDecision      → allow
    """
    hook_output = cast(dict[str, Any], raw.get("hookSpecificOutput", {}))

    decision = hook_output.get("permissionDecision", "")
    reason = str(hook_output.get("permissionDecisionReason", ""))
    updated_input = hook_output.get("updatedInput")
    system_message = str(raw.get("systemMessage", ""))

    if not isinstance(updated_input, dict):
        updated_input = None

    if decision == "deny":
        return HookResult(
            action="block",
            reason=reason,
            stdout=stdout,
            stderr=stderr,
            exit_code=0,
            updated_input=updated_input,
            system_message=system_message,
        )

    # "allow" and "ask" both pass through — "allow" with updated_input,
    # "ask" defers to kimi's normal approval flow
    return HookResult(
        action="allow",
        reason=reason,
        stdout=stdout,
        stderr=stderr,
        exit_code=0,
        updated_input=updated_input,
        system_message=system_message,
    )
