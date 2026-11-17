"""Terminal chat interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from pydantic_ai import ToolCallPart
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from .config import Settings
from .db import Store
from .session import Session
from .tools import google

console = Console()

HELP = """\
[bold]Commands[/bold]
  /new            start a new conversation
  /list           list recent conversations
  /load <id>      switch to a conversation
  /memories       show what the assistant remembers
  /todos          show open todos
  /model [name]   show or change the model (e.g. openai:gpt-5.1)
  /help           this help
  /quit           exit  (Ctrl-D works too)
"""


def _format_args(call: ToolCallPart) -> str:
    args = call.args_as_dict()
    if call.tool_name == "run_shell":
        return f"$ {args.get('command')}\n  (cwd: {args.get('cwd', '~')})"
    return json.dumps(args, indent=2, ensure_ascii=False)


async def chat(settings: Settings, *, new: bool) -> None:
    store = Store(settings.db_path)
    session = Session(settings=settings, store=store, channel="cli")
    session.new() if new else session.resume_latest()

    prompt = PromptSession(history=FileHistory(str(settings.data_dir / "cli_history")))

    async def approve(call: ToolCallPart) -> bool:
        lexer = "bash" if call.tool_name == "run_shell" else "json"
        console.print(
            Panel(
                Syntax(_format_args(call), lexer, word_wrap=True, background_color="default"),
                title=f"[yellow]Approve {call.tool_name}?[/yellow]",
                border_style="yellow",
            )
        )
        answer = await prompt.prompt_async("Allow? [y/N] ")
        return answer.strip().lower() in {"y", "yes"}

    def on_text(chunk: str) -> None:
        console.print(chunk, end="", markup=False, highlight=False)

    def on_tool(call: ToolCallPart) -> None:
        console.print(f"\n[dim]→ {call.tool_name}[/dim]")

    google_note = "connected" if google.is_authorized(settings) else "not connected (`assistant auth google`)"
    console.print(
        f"[bold]pai-assistant[/bold] · model [cyan]{settings.model}[/cyan] · Google {google_note}\n"
        f"[dim]Conversation #{session.conversation_id} ({len(session.messages)} messages). /help for commands.[/dim]"
    )

    while True:
        try:
            with patch_stdout():
                text = (await prompt.prompt_async("\n› ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue

        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            match cmd:
                case "/quit" | "/exit":
                    break
                case "/help":
                    console.print(HELP)
                case "/new":
                    session.new()
                    console.print(f"[dim]New conversation #{session.conversation_id}.[/dim]")
                case "/list":
                    for c in store.list_conversations("cli"):
                        mark = "*" if c.id == session.conversation_id else " "
                        console.print(f"{mark} #{c.id:<4} {c.updated_at}  {c.title or '(empty)'}")
                case "/load":
                    if arg.isdigit() and store.conversation_exists(int(arg)):
                        session.load(int(arg))
                        console.print(f"[dim]Loaded #{arg} ({len(session.messages)} messages).[/dim]")
                    else:
                        console.print("[red]Usage: /load <existing id>[/red]")
                case "/memories":
                    for m in store.list_memories() or []:
                        console.print(f"  {m.id}: {m.fact}")
                case "/todos":
                    for t in store.list_todos():
                        console.print(f"  [{t.id}] {t.text}" + (f"  (due {t.due})" if t.due else ""))
                case "/model":
                    if arg:
                        settings.model = arg
                        session.reload_agent()
                    console.print(f"Model: [cyan]{settings.model}[/cyan]")
                case _:
                    console.print(f"[red]Unknown command {cmd}[/red]. /help")
            continue

        try:
            console.print()
            await session.ask(text, approve=approve, on_text=on_text, on_tool_call=on_tool)
            console.print()
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
        except Exception as e:  # keep the REPL alive on model/tool errors
            console.print(f"\n[red]Error:[/red] {type(e).__name__}: {e}")

    store.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="assistant", description="Personal assistant (PydanticAI)")
    parser.add_argument("--new", action="store_true", help="start a new conversation instead of resuming")
    parser.add_argument("--model", help="override PAI_MODEL for this session")
    sub = parser.add_subparsers(dest="command")
    auth = sub.add_parser("auth", help="connect external accounts")
    auth.add_argument("service", choices=["google"])
    args = parser.parse_args(argv)

    settings = Settings()
    if args.model:
        settings.model = args.model
    settings.ensure_dirs()

    if args.command == "auth":
        google.authorize(settings)
        console.print(f"[green]Google connected.[/green] Token saved to {settings.google_token_path}")
        return

    try:
        asyncio.run(chat(settings, new=args.new))
    except KeyboardInterrupt:
        sys.exit(130)
