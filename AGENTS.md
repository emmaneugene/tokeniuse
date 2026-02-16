# Overview

**llmeter** is a terminal dashboard for monitoring AI coding assistant usage limits and API spend.

## Tech Stack

- **Language**: Python 3.11+
- **Build system**: Hatchling (`pyproject.toml`)
- **TUI framework**: [Textual](https://textual.textualize.io/) [Rich](https://github.com/Textualize/rich)
- **HTTP library**: [aiohttp](https://docs.aiohttp.org/)
- **Testing**: pytest + pytest-asyncio + aioresponses

## Repository Layout

```
src/llmeter/
├── __main__.py          # CLI entry point
├── app.py               # Main Textual App (dashboard, keybindings, themes)
├── app.tcss             # Textual CSS styles
├── backend.py           # Orchestrates provider fetches
├── config.py            # JSON config (~/.config/llmeter/settings.json)
├── auth.py              # Unified credential store (~/.config/llmeter/auth.json)
├── models.py            # Data models
├── providers/           # One module per provider
│   ├── helpers.py       # Shared utilities (config_dir, HTTP helpers)
│   ├── codex.py         # Codex (ChatGPT) usage fetcher
│   ├── codex_oauth.py   # Codex OAuth flow
│   ├── claude.py        # Claude usage fetcher
│   ├── claude_oauth.py  # Claude OAuth flow
│   ├── cursor.py        # Cursor usage fetcher
│   ├── cursor_auth.py   # Cursor auth
│   ├── gemini.py        # Gemini CLI quota fetcher
│   ├── gemini_oauth.py  # Gemini OAuth flow
│   ├── openai_api.py    # OpenAI API billing
│   └── anthropic_api.py # Anthropic API billing
└── widgets/
    ├── provider_card.py # Dashboard card widget
    └── usage_bar.py     # Color-coded usage bar widget

tests/                   # pytest test suite
├── conftest.py          # Shared fixtures (tmp config dirs, auth helpers)
├── test_auth.py
├── test_codex.py
├── test_claude.py
├── test_cursor.py
├── test_gemini.py
└── test_config.py
```

## Key Conventions

### Code Style

- Use `from __future__ import annotations` in all modules.
- Type hints everywhere; use `Optional` / `X | None` style from `typing`.
- Dataclasses for data models (`models.py`).
- All provider fetchers are async functions returning `ProviderResult`.

### Provider Architecture

- Each provider has a `fetch_<name>()` async function in `src/llmeter/providers/`.
- OAuth providers have a separate `*_oauth.py` module for the login flow.
- Fetchers are registered in `backend.py` via the `PROVIDER_FETCHERS` dict.
- When adding a new provider: create the fetcher module, register it in `backend.py`, and add its ID to `models.py` `PROVIDERS`.

### Configuration

- App config: `~/.config/llmeter/settings.json` — controls enabled providers, order, API keys, refresh interval.
- Auth store: `~/.config/llmeter/auth.json` — unified OAuth token storage for all providers.
- Config dir follows XDG conventions (`XDG_CONFIG_HOME`).

### Testing

- Tests use `tmp_path` and `monkeypatch` to isolate config/auth from the real filesystem.
- Mock HTTP calls with `aioresponses`.
- Run tests: `pytest` (or `uv run pytest`).
- Async test mode: `asyncio_mode = "auto"` (no need for `@pytest.mark.asyncio`).

### Building & Running

- Install dev deps: `uv sync --extra dev`
- Run locally: `uv run llmeter`
- Snapshot mode: `uv run llmeter --snapshot`
- Run tests: `uv run pytest`

## Important Notes

- OAuth tokens contain secrets — never log or commit `auth.json` contents.
- Provider API responses vary; always handle missing/unexpected fields gracefully with sensible defaults.
- All timestamps in `auth.json` are **milliseconds** since epoch.
