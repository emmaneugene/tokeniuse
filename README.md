# llmeter

A CLI tool to monitor your LLM subscription and API limits.

![llmeter demo](docs/demo.png)

## Overview

AI-assisted coding is here to stay, and at some point you'll probably be trying to manage your usage on some paid
subscriptions. `llmeter` helps you do that without toggling between multiple apps or leaving the comfort of the CLI.

## Features

There are a bunch of tools out there that do similar things, but I found that they were either too complex and invasive,
or lacking in features. Here's what `llmeter` offers:

- **Usage tracking for subscription and API providers**
  - For subscriptions (e.g. Claude, Codex, Cursor), reporting follows providers' respective formats.
  - For API providers (e.g. Anthropic, OpenAI, OpenCode Zen), it tracks API spend for the current month with respect
    to a fixed monthly budget.
- **Self-contained**: Login once with OAuth or manually enter cookies/API keys. No dependencies on having other apps
  running or scraping from local storage. You know exactly how secrets are being fetched and stored.
- **Interactive or static usage** — View in a TUI with auto-refresh or just get a one-time snapshot. Also supports JSON
  output which you can build workflows on top of.
- **Simple**: Pure Python, minimal dependencies, no system permissions required. App state is fully persisted at `$XDG_CONFIG_HOME/llmeter`.

## Supported Providers

| Provider             | ID              | Type         | Auth type  |
| -------------------- | --------------- | ------------ | ---------- |
| **OpenAI ChatGPT**   | `codex`         | Subscription | OAuth      |
| **Anthropic Claude** | `claude`        | Subscription | OAuth      |
| **Google Gemini**    | `gemini`        | Subscription | OAuth      |
| **GitHub Copilot**   | `copilot`       | Subscription | OAuth      |
| **Cursor**           | `cursor`        | Subscription | Cookie     |
| **OpenAI API**       | `openai-api`    | API usage    | API key    |
| **Anthropic API**    | `anthropic-api` | API usage    | API key    |
| **Opencode Zen**     | `opencode`      | API usage    | Cookie     |

>     Note: Anthropic API spend data can lag 1 day behind real-time usage.

For more information on how usage data is fetched and parsed, see the [docs](./docs/).

## Prerequisites

- Python 3.11+

## Install

With `uv`:

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

Or plain `pip`:

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
uv venv
source .venv/bin/activate
uv sync --extra dev
```

## Configuration

Config file lives at `~/.config/llmeter/settings.json`.

For example:

```json
{
  "providers": [
    { "id": "codex", "enabled": true },
    { "id": "claude", "enabled": false },
    { "id": "cursor", "enabled": true },
    { "id": "openai-api", "monthly_budget": 50.0, "enabled": false },
    { "id": "anthropic-api", "monthly_budget": 50.0, "enabled": false },
    { "id": "opencode", "monthly_budget": 20.0, "enabled": true }
  ],
  "refresh_interval": 300
}
```

Generate a default:

```bash
llmeter --init-config
```

### Auth secrets

All secrets (OAuth tokens, API keys, auth cookies) are stored in `~/.config/llmeter/auth.json`, created with `0600`
permissions. Run `llmeter --login <provider>` to set credentials — this saves them to `auth.json` and enables the
provider in `settings.json` automatically.

For example:

```json
{
  "openai-codex": {
    "type": "oauth",
    "access": "***",
    "refresh": "***",
    "expires": 1740589200000,
    "accountId": "user-***",
    "email": "user@example.com"
  },
  "anthropic": {
    "type": "oauth",
    "refresh": "***",
    "access": "***",
    "expires": 1740589200000
  },
  "google-gemini-cli": {
    "type": "oauth",
    "refresh": "***",
    "access": "***",
    "expires": 1740589200000,
    "projectId": "gemini-cli-proj-3a7f2b9e",
    "email": "user@example.com"
  },
  "github-copilot": {
    "type": "oauth",
    "access": "ghu_***"
  },
  "cursor": {
    "type": "cookie",
    "cookie": "WorkosCursorSessionToken=***",
    "email": "user@example.com"
  },
  "anthropic-api": {
    "type": "api_key",
    "api_key": "sk-ant-admin***"
  },
  "openai-api": {
    "type": "api_key",
    "api_key": "sk-admin-***"
  },
  "opencode": {
    "type": "api_key",
    "api_key": "Fe26.2***"
  }
}
```

### HTTP debug logging

To inspect provider HTTP request/response metadata without disrupting the TUI, enable file-based debug logging:

```bash
LLMETER_DEBUG_HTTP=1 llmeter
```

Logs are written as JSON lines to `~/.config/llmeter/debug.log`

Optional custom path:

```bash
LLMETER_DEBUG_HTTP=1 LLMETER_DEBUG_LOG_PATH=/tmp/llmeter-debug.log llmeter
```

Logs include full request metadata (including auth headers/tokens/cookies when present). The debug log file is written
with user-only permissions when possible (`0600`).

## Contributing

As it stands right now the app fits my needs well enough, but I'm happy to accept contributions for more providers!

## See also

- **[CodexBar](https://github.com/steipete/CodexBar)** — Original inspiration
- **[pi-mono](https://github.com/badlogic/pi-mono)** — Referenced for OAuth implementations, and my daily driver
- **[ccusage](https://github.com/ryoppippi/ccusage)** — Also useful for cost tracking
