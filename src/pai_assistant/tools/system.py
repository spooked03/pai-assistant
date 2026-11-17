"""Shell and filesystem tools. Anything with side effects requires approval."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic_ai import FunctionToolset, ModelRetry, RunContext

from ..deps import Deps

toolset: FunctionToolset[Deps] = FunctionToolset(id="system")

MAX_OUTPUT = 20_000


def _resolve(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n... [truncated, {len(text) - MAX_OUTPUT} more chars]"


@toolset.tool(requires_approval=True)
async def run_shell(ctx: RunContext[Deps], command: str, cwd: str = "~") -> dict:
    """Run a shell command on the user's machine and return its output.

    Prefer read_file / list_dir for simply reading files.

    Args:
        command: The command line, run with /bin/sh -c.
        cwd: Working directory.
    """
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=_resolve(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=ctx.deps.settings.shell_timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return {"exit_code": None, "error": f"timed out after {ctx.deps.settings.shell_timeout}s"}
    return {
        "exit_code": proc.returncode,
        "stdout": _truncate(out.decode(errors="replace")),
        "stderr": _truncate(err.decode(errors="replace")),
    }


@toolset.tool
def read_file(ctx: RunContext[Deps], path: str, offset: int = 0, max_chars: int = MAX_OUTPUT) -> str:
    """Read a text file.

    Args:
        path: File path (~ is expanded).
        offset: Character offset to start reading from, for large files.
        max_chars: Maximum number of characters to return.
    """
    p = _resolve(path)
    if not p.is_file():
        raise ModelRetry(f"{p} is not a file")
    text = p.read_text(errors="replace")
    chunk = text[offset : offset + max_chars]
    if offset + max_chars < len(text):
        chunk += f"\n... [{len(text) - offset - max_chars} more chars; continue with offset={offset + max_chars}]"
    return chunk


@toolset.tool
def list_dir(ctx: RunContext[Deps], path: str = "~", pattern: str = "*") -> list[str]:
    """List entries in a directory, optionally filtered by a glob pattern (e.g. '*.pdf' or '**/*.py')."""
    p = _resolve(path)
    if not p.is_dir():
        raise ModelRetry(f"{p} is not a directory")
    entries = sorted(p.glob(pattern))[:500]
    return [str(e) + ("/" if e.is_dir() else "") for e in entries]


@toolset.tool(requires_approval=True)
def write_file(ctx: RunContext[Deps], path: str, content: str, append: bool = False) -> str:
    """Write (or append) text to a file, creating parent directories as needed."""
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a" if append else "w") as f:
        f.write(content)
    return f"Wrote {len(content)} chars to {p}."
