"""Notes, todos and long-term memory tools (local SQLite)."""

from __future__ import annotations

from dataclasses import asdict

from pydantic_ai import FunctionToolset, RunContext

from ..deps import Deps

toolset: FunctionToolset[Deps] = FunctionToolset(id="personal")


# ---- notes -----------------------------------------------------------------
@toolset.tool
def add_note(ctx: RunContext[Deps], title: str, body: str, tags: list[str] | None = None) -> str:
    """Save a note.

    Args:
        title: Short title.
        body: Note content (markdown allowed).
        tags: Optional tags for later lookup.
    """
    note_id = ctx.deps.store.add_note(title, body, tags or [])
    return f"Saved note #{note_id}."


@toolset.tool
def search_notes(ctx: RunContext[Deps], query: str | None = None, limit: int = 20) -> list[dict]:
    """Search notes by text in title, body or tags. Omit the query to list the most recent notes."""
    return [asdict(n) for n in ctx.deps.store.search_notes(query, limit)]


@toolset.tool
def update_note(ctx: RunContext[Deps], note_id: int, body: str) -> str:
    """Replace the body of an existing note."""
    return "Updated." if ctx.deps.store.update_note(note_id, body) else f"No note #{note_id}."


@toolset.tool(requires_approval=True)
def delete_note(ctx: RunContext[Deps], note_id: int) -> str:
    """Delete a note permanently."""
    return "Deleted." if ctx.deps.store.delete_note(note_id) else f"No note #{note_id}."


# ---- todos -----------------------------------------------------------------
@toolset.tool
def add_todo(ctx: RunContext[Deps], text: str, due: str | None = None) -> str:
    """Add a todo item.

    Args:
        text: What needs to be done.
        due: Optional due date/time in ISO 8601 (e.g. 2026-11-20 or 2026-11-20T15:00).
    """
    return f"Added todo #{ctx.deps.store.add_todo(text, due)}."


@toolset.tool
def list_todos(ctx: RunContext[Deps], include_done: bool = False) -> list[dict]:
    """List todos, open ones first, sorted by due date."""
    return [asdict(t) for t in ctx.deps.store.list_todos(include_done)]


@toolset.tool
def complete_todo(ctx: RunContext[Deps], todo_id: int) -> str:
    """Mark a todo as done."""
    return "Done." if ctx.deps.store.complete_todo(todo_id) else f"No todo #{todo_id}."


@toolset.tool(requires_approval=True)
def delete_todo(ctx: RunContext[Deps], todo_id: int) -> str:
    """Delete a todo permanently (use complete_todo for finished tasks)."""
    return "Deleted." if ctx.deps.store.delete_todo(todo_id) else f"No todo #{todo_id}."


# ---- long-term memory ------------------------------------------------------
@toolset.tool
def remember(ctx: RunContext[Deps], fact: str) -> str:
    """Store a durable fact about the user (preferences, people, recurring context).

    Use this for things worth knowing in future conversations, not for transient details.
    Phrase facts so they stand on their own, e.g. "User's partner is called Anna".
    """
    return f"Remembered (memory #{ctx.deps.store.add_memory(fact)})."


@toolset.tool
def forget(ctx: RunContext[Deps], memory_id: int) -> str:
    """Remove a stored fact that is wrong or outdated. Memory ids are shown in your instructions."""
    return "Forgotten." if ctx.deps.store.delete_memory(memory_id) else f"No memory #{memory_id}."
