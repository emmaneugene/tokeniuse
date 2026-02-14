# tokeniuse 🎚️

*token + caniuse* — A terminal dashboard to monitor your AI coding assistant usage limits and API spend.

Built with [Textual](https://textual.textualize.io/) and [aiohttp](https://docs.aiohttp.org/). Provider logic reimplemented natively in Python from [CodexBar](https://github.com/steipete/CodexBar).

## Features

- **5 providers** — Codex, Claude, Gemini CLI quotas + OpenAI & Anthropic API billing
- **Config-driven** — JSON config controls which providers are shown and in what order
- **Live dashboard** — Color-coded usage bars with reset countdowns, auto-refreshing
- **One-shot mode** — Non-interactive Rich-formatted output for scripts and CI
- **Theme cycling** — 6 built-in themes (dark, light, monokai, dracula, nord, tokyo-night)

## Supported Providers

### CLI usage quotas

| Provider | ID | How it works | Auth |
|----------|----|-------------|------|
| **Codex** | `codex` | JSON-RPC to `codex app-server` | Codex CLI login |
| **Claude** | `claude` | OAuth API (`api.anthropic.com/api/oauth/usage`) | `~/.claude/.credentials.json` or macOS Keychain |
| **Gemini** | `gemini` | Google Cloud Code quota API | `~/.gemini/oauth_creds.json` (Gemini CLI login) |

### API billing

| Provider | ID | How it works | Auth |
|----------|----|-------------|------|
| **OpenAI API** | `openai-api` | `GET /v1/organization/costs` | Admin API key (`sk-admin-...`) |
| **Anthropic API** | `anthropic-api` | `GET /v1/organizations/cost_report` | Admin API key (`sk-ant-admin01-...`) |

## Prerequisites

- Python 3.11+
- **Codex**: `codex` CLI installed and logged in (`npm i -g @openai/codex`)
- **Claude**: `claude` CLI installed and logged in
- **Gemini**: `gemini` CLI installed and logged in with Google OAuth
- **OpenAI API**: Admin API key from [platform.openai.com/settings/organization/admin-keys](https://platform.openai.com/settings/organization/admin-keys)
- **Anthropic API**: Admin API key from [console.anthropic.com](https://console.anthropic.com) (starts with `sk-ant-admin01-`)

## Install

```bash
uv venv && uv pip install -e .
```

## Configuration

Config file: `~/.config/tokeniuse/config.json`

Generate a default one:
```bash
tokeniuse --init-config
```

### Example with all providers

```json
{
  "providers": [
    { "id": "codex" },
    { "id": "claude" },
    { "id": "gemini" },
    { "id": "openai-api", "api_key": "sk-admin-...", "monthly_budget": 100.0 },
    { "id": "anthropic-api", "api_key": "sk-ant-admin01-...", "monthly_budget": 50.0 }
  ],
  "refresh_interval": 120
}
```

- **`providers`** — Providers to display, in order. Only listed providers are fetched.
- **`refresh_interval`** — Auto-refresh interval in seconds (default: 120).

Provider-specific settings:

| Setting | Applies to | Description |
|---------|-----------|-------------|
| `api_key` | `openai-api`, `anthropic-api` | Admin API key (overrides env var) |
| `monthly_budget` | `openai-api`, `anthropic-api` | Budget in USD — spend shown as a percentage bar |

### Environment variables

API keys can also be set via environment variables:

| Variable | Provider |
|----------|----------|
| `OPENAI_ADMIN_KEY` | `openai-api` |
| `ANTHROPIC_ADMIN_KEY` | `anthropic-api` |

If no config file exists, only `codex` and `claude` are shown by default.

## Usage

### Interactive TUI

```bash
tokeniuse
```

### One-shot mode

```bash
tokeniuse --one-shot
```

### All options

```
tokeniuse [options]

  --refresh SECONDS  Auto-refresh interval (overrides config)
  --one-shot         Print once and exit (Rich-formatted)
  --init-config      Create a default config file and exit
  -V, --version      Show version
```

## Keybindings

| Key | Action |
|-----|--------|
| `r` | Refresh all providers |
| `t` | Cycle theme |
| `?` | Show help overlay |
| `q` | Quit |

## Architecture

```
src/tokeniuse/
├── __main__.py              # CLI entry point
├── app.py                   # Textual app with auto-refresh
├── app.tcss                 # Textual CSS stylesheet
├── backend.py               # Provider orchestration
├── config.py                # JSON config loading
├── models.py                # Shared data models
├── providers/
│   ├── codex.py             # Codex: JSON-RPC over stdin/stdout
│   ├── claude.py            # Claude: OAuth API + Keychain creds
│   ├── gemini.py            # Gemini: Google Cloud Code quota API
│   ├── openai_api.py        # OpenAI: /v1/organization/costs
│   └── anthropic_api.py     # Anthropic: /v1/organizations/cost_report
└── widgets/
    ├── provider_card.py     # Per-provider card UI
    └── usage_bar.py         # Color-coded progress bar
```

## License

MIT
