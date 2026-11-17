# pai-assistant

A personal assistant built with [PydanticAI](https://ai.pydantic.dev), managed with `uv`.

**Capabilities**: notes and todos, long-term memory, web search (DuckDuckGo) and page fetching, shell and file access, Gmail, and Google Calendar.
Every action with side effects asks you to approve it first: shell commands, file writes, sending or drafting email, calendar changes and deletions.

## Quick start

```bash
cd ~/Projects/pai-assistant
mkdir -p ~/.config/pai-assistant
cp .env.example ~/.config/pai-assistant/.env   # set PAI_MODEL + API key
uv run assistant            # resumes your last conversation
uv run assistant --new      # fresh conversation
uv run assistant --model openai:gpt-5.1
```

Install it globally with `uv tool install -e .` to get `assistant` on your PATH.

## Models

The default model is **DeepSeek V4.1 Flash 1017 through OpenRouter**
(`openrouter:deepseek/deepseek-v4.1-flash-1017`). It only needs `OPENROUTER_API_KEY`.

There are two ways to choose a different model:

1. **A PydanticAI model string**: `PAI_MODEL=<provider>:<model>`, for example `anthropic:…`, `openai:…`,
   `google-gla:…` or `openrouter:<vendor>/<model>`. The API key comes from the provider's usual
   environment variable.
2. **Any OpenAI-compatible endpoint**: set `PAI_BASE_URL`. `PAI_MODEL` is then the endpoint's own
   model name, and `PAI_API_KEY` is the key (optional for local servers):

   ```bash
   PAI_BASE_URL=https://api.deepseek.com   PAI_MODEL=deepseek-flash      PAI_API_KEY=sk-...
   PAI_BASE_URL=http://localhost:11434/v1  PAI_MODEL=qwen3:14b           # Ollama
   PAI_BASE_URL=http://localhost:8000/v1   PAI_MODEL=<served-model>      # vLLM / llama.cpp / LM Studio
   ```

Override for one session with `--model` / `--base-url`, or switch mid-chat with `/model <name>` and
`/endpoint <url>` (`/endpoint off` returns to option 1). The assistant relies on tools, so pick a model
that supports tool calling.

In-chat commands: `/new`, `/list`, `/load <id>`, `/memories`, `/todos`, `/model <name>`, `/help`, `/quit`.

## Connecting Gmail and Calendar

1. In Google Cloud Console, create a project and enable the **Gmail API** and **Google Calendar API**.
2. Set up the OAuth consent screen (External, testing mode) and add yourself as a test user.
3. Create an OAuth client ID of type **Desktop app**, download the JSON and save it as
   `~/.config/pai-assistant/google_credentials.json`.
4. Run `uv run assistant auth google`. A browser opens. The resulting token is stored in
   `~/.local/share/pai-assistant/google_token.json`.

## Layout

```
src/pai_assistant/
  config.py        settings (PAI_* env vars / .env)
  db.py            SQLite: notes, todos, memories, conversations
  agent.py         Agent: model, instructions, toolsets
  session.py       interface-agnostic chat loop + approval handling
  cli.py           terminal REPL
  tools/
    personal.py    notes, todos, remember/forget
    system.py      run_shell, read_file, list_dir, write_file
    google.py      Gmail + Calendar
```

Data lives in `~/.local/share/pai-assistant/` (`assistant.db`, `cli_history`, and the Google token).

## Adding a web UI or Telegram bot later

`Session` has no dependency on the terminal. A new frontend creates
`Session(settings, store, channel="telegram")` and calls
`await session.ask(text, approve=..., on_text=...)`, where `approve` is an async callback that
returns True or False (for example, Telegram inline buttons). Each channel keeps its own conversation
history. Memories, notes and todos are shared across channels.
