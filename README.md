# llmeter

A CLI tool to monitor your LLM subscription and API limits.

![llmeter demo](docs/demo.png)

## Features

- **Multiple providers** — Codex, Claude, Gemini CLI quotas, OpenAI & Anthropic API billing
- **Self-contained auth** — Login once with OAuth or manually enter cookies/API keys. No external dependencies.
- **Interactive or static** — Color-coded usage bars with reset countdowns, auto-refreshing
- **Config-driven** — JSON config controls which providers are shown and in what order

## Supported Providers

### Subscription-based

| Provider | ID | How it works | Auth |
|----------|----|-------------|------|
| **OpenAI ChatGPT** | `codex` | OAuth | `llmeter --login-codex` |
| **Anthropic Claude** | `claude` | OAuth | `llmeter --login-claude` |
| **Google Gemini** | `gemini` | OAuth | `llmeter --login-gemini` |
| **Cursor** | `cursor` | Cookie | `llmeter --login-cursor` |

### API usage

| Provider | ID | How it works | Auth |
|----------|----|-------------|------|
| **OpenAI API** | `openai-api` | `GET /v1/organization/costs` | Admin API key |
| **Anthropic API** | `anthropic-api` | `GET /v1/organizations/cost_report` | Admin API key |

## Prerequisites

- Python 3.11+

## Install

### For global usage

Install with `uv`:

```bash
# Install
uv tool install git+https://github.com/emmaneugene/llmeter
# Upgrade
uv tool upgrade llmeter
# Uninstall
uv tool uninstall llmeter
```

Or `pipx`:

```bash
# Install
pipx install git+https://github.com/emmaneugene/llmeter
# Upgrade
pipx upgrade llmeter
# Uninstall
pipx uninstall llmeter
```

Or plain pip:

```bash
# Install
pip install git+https://github.com/emmaneugene/llmeter
# Upgrade
pip install --upgrade git+https://github.com/emmaneugene/llmeter
# Uninstall
pip uninstall llmeter
```

### Local development

```bash
uv venv && uv pip install -e ".[dev]"
```

## Configuration

Config file: `~/.config/llmeter/settings.json`

Generate a default one:

```bash
llmeter --init-config
```

### Example with all providers

```json
{
  "providers": [
    { "id": "codex" },
    { "id": "claude" },
    { "id": "gemini" },
    { "id": "openai-api", "api_key": "sk-admin-...", "monthly_budget": 50.0 },
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

### Credential storage

All OAuth credentials are stored in a single file:

```
~/.config/llmeter/auth.json
```

Each provider stores its tokens under a provider key (`anthropic`, `openai-codex`, `google-gemini-cli`). Tokens are auto-refreshed on each run. The file is created with `0600` permissions.

### Environment variables

API keys can also be set via environment variables:

| Variable | Provider |
|----------|----------|
| `OPENAI_ADMIN_KEY` | `openai-api` |
| `ANTHROPIC_ADMIN_KEY` | `anthropic-api` |

If no config file exists, `claude` and `codex` are shown by default.

### HTTP debug logging

To inspect provider HTTP request/response metadata without disrupting the TUI, enable file-based debug logging:

```bash
LLMETER_DEBUG_HTTP=1 llmeter
```

Logs are written as JSON lines to:

```
~/.config/llmeter/debug.log
```

Optional custom path:

```bash
LLMETER_DEBUG_HTTP=1 LLMETER_DEBUG_LOG_PATH=/tmp/llmeter-debug.log llmeter
```

Logs include full request metadata (including auth headers/tokens/cookies when present).
The debug log file is written with user-only permissions when possible (`0600`).

## Credits

- **[CodexBar](https://github.com/steipete/CodexBar)** — original inspiration
- **[pi-mono](https://github.com/badlogic/pi-mono)** — referenced for OAuth implementations
