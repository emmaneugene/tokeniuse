# Manual `curl` testing for providers

Quick-reference curl commands for manually testing each provider's API endpoints.

> **Prerequisite:** You need valid credentials for each provider. Run the
> corresponding `llmeter --login <provider>` first, then extract tokens from
> `~/.config/llmeter/auth.json`.

---

## Table of Contents

1. [Claude (OAuth)](#1-claude-oauth)
2. [Codex / ChatGPT (OAuth)](#2-codex--chatgpt-oauth)
3. [Cursor (Cookie)](#3-cursor-cookie)
4. [Gemini CLI (OAuth)](#4-gemini-cli-oauth)
5. [GitHub Copilot (Device Flow)](#5-github-copilot-device-flow)
6. [OpenAI API (Admin Key)](#6-openai-api-admin-key)
7. [Anthropic API (Admin Key)](#7-anthropic-api-admin-key)
8. [Opencode Zen (Auth Cookie)](#8-opencode-zen-auth-cookie)
9. [Extracting Tokens from auth.json](#9-extracting-tokens-from-authjson)

---

## 1. Claude (OAuth)

**Auth:** Bearer token from Claude OAuth flow (`llmeter --login claude`).

```bash
# Set your access token (see §8 for how to extract it)
CLAUDE_TOKEN="<your-access-token>"
```

### 1a. Fetch Usage (rate limits, extra usage/credits)

```bash
curl -s \
  -H "Authorization: Bearer $CLAUDE_TOKEN" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "anthropic-beta: oauth-2025-04-20" \
  -H "User-Agent: LLMeter/0.1.0" \
  "https://api.anthropic.com/api/oauth/usage" | jq .
```

**Expected response shape:**

```jsonc
{
  "five_hour": {
    "utilization": 15.0,        // percentage used (0–100)
    "resets_at": "2025-..."     // ISO 8601 or epoch timestamp
  },
  "seven_day": {
    "utilization": 5.0,
    "resets_at": "2025-..."
  },
  "seven_day_sonnet": { ... },  // optional model-specific windows
  "seven_day_opus": { ... },    // optional
  "extra_usage": {              // optional
    "is_enabled": true,
    "used_credits": 1500,       // in cents
    "monthly_limit": 10000,     // in cents
    "currency": "USD"
  }
}
```

### 1b. Fetch Profile (email, plan)

```bash
curl -s \
  -H "Authorization: Bearer $CLAUDE_TOKEN" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "anthropic-beta: oauth-2025-04-20" \
  -H "User-Agent: LLMeter/0.1.0" \
  "https://api.anthropic.com/api/oauth/profile" | jq .
```

**Expected response shape:**

```jsonc
{
  "account": {
    "email": "user@example.com",
    "has_claude_pro": true,
    "has_claude_max": false
  },
  "organization": {
    "organization_type": "...",
    "billing_type": "stripe",
    "rate_limit_tier": "..."
  }
}
```

---

## 2. Codex / ChatGPT (OAuth)

**Auth:** Bearer token + Account ID from OpenAI Codex OAuth flow (`llmeter --login codex`).

```bash
# Set your credentials (see §8 for how to extract them)
CODEX_TOKEN="<your-access-token>"
CODEX_ACCOUNT_ID="<your-account-id>"
```

### 2a. Fetch Usage (rate limits, credits)

```bash
curl -s \
  -H "Authorization: Bearer $CODEX_TOKEN" \
  -H "ChatGPT-Account-Id: $CODEX_ACCOUNT_ID" \
  -H "User-Agent: LLMeter/0.1.0" \
  -H "Accept: application/json" \
  "https://chatgpt.com/backend-api/wham/usage" | jq .
```

**Expected response shape:**

```jsonc
{
  "plan_type": "pro",                // "free", "plus", "pro", "team", etc.
  "rate_limit": {
    "primary_window": {
      "used_percent": 15,
      "reset_at": 1735401600,        // epoch seconds
      "limit_window_seconds": 18000  // 5 hours
    },
    "secondary_window": {
      "used_percent": 5,
      "reset_at": 1735920000,
      "limit_window_seconds": 604800 // 7 days
    }
  },
  "credits": {
    "has_credits": true,
    "unlimited": false,
    "balance": 150.0
  }
}
```

---

## 3. Cursor (Cookie)

**Auth:** Session cookie from browser (`llmeter --login cursor`).

```bash
# Set your cookie (see §8 for how to extract it)
CURSOR_COOKIE="WorkosCursorSessionToken=..."
```

### 3a. Fetch Usage Summary (dollar-based plan + on-demand spend)

```bash
curl -s \
  -H "Cookie: $CURSOR_COOKIE" \
  -H "Accept: application/json" \
  -H "User-Agent: LLMeter/0.1.0" \
  "https://cursor.com/api/usage-summary" | jq .
```

**Expected response shape:**

```jsonc
{
  "billingCycleEnd": "2025-02-01T00:00:00.000Z",
  "membershipType": "pro",          // "pro", "hobby", "team", etc.
  "individualUsage": {
    "plan": {
      "used": 1500,                  // cents
      "limit": 5000,                 // cents
      "totalPercentUsed": 30.0
    },
    "onDemand": {
      "used": 500,                   // cents
      "limit": 10000                 // cents
    }
  }
}
```

### 3b. Fetch User Info (email, sub ID)

```bash
curl -s \
  -H "Cookie: $CURSOR_COOKIE" \
  -H "Accept: application/json" \
  -H "User-Agent: LLMeter/0.1.0" \
  "https://cursor.com/api/auth/me" | jq .
```

**Expected response shape:**

```jsonc
{
  "email": "user@example.com",
  "sub": "user_abc123",
  "name": "..."
}
```

### 3c. Fetch Legacy Request Usage (request-based plans)

Requires the `sub` ID from the `/api/auth/me` response above:

```bash
CURSOR_USER_ID="<sub-from-auth-me>"

curl -s \
  -H "Cookie: $CURSOR_COOKIE" \
  -H "Accept: application/json" \
  -H "User-Agent: LLMeter/0.1.0" \
  "https://cursor.com/api/usage?user=$CURSOR_USER_ID" | jq .
```

**Expected response shape:**

```jsonc
{
  "gpt-4": {
    "numRequests": 138,
    "numRequestsTotal": 138,
    "maxRequestUsage": 500           // present only on request-based plans
  }
}
```

---

## 4. Gemini CLI (OAuth)

**Auth:** Google OAuth Bearer token from Gemini CLI flow (`llmeter --login gemini`).

```bash
# Set your credentials (see §8 for how to extract them)
GEMINI_TOKEN="<your-access-token>"
GEMINI_PROJECT_ID="<your-project-id>"  # optional, discovered via loadCodeAssist
```

### 4a. Load Code Assist (discover tier + project ID)

```bash
curl -s -X POST \
  -H "Authorization: Bearer $GEMINI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"metadata": {"ideType": "GEMINI_CLI", "pluginType": "GEMINI"}}' \
  "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist" | jq .
```

**Expected response shape:**

```jsonc
{
  "currentTier": {
    "id": "standard-tier"            // "free-tier", "standard-tier", "legacy-tier"
  },
  "cloudaicompanionProject": "my-project-id"
}
```

### 4b. Fetch Quota (per-model usage buckets)

```bash
# Without project ID:
curl -s -X POST \
  -H "Authorization: Bearer $GEMINI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}' \
  "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota" | jq .

# With project ID:
curl -s -X POST \
  -H "Authorization: Bearer $GEMINI_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"project\": \"$GEMINI_PROJECT_ID\"}" \
  "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota" | jq .
```

**Expected response shape:**

```jsonc
{
  "buckets": [
    {
      "modelId": "gemini-2.5-pro",
      "remainingFraction": 0.85,     // 0.0–1.0 (1.0 = fully available)
      "resetTime": "2025-01-16T00:00:00Z"
    },
    {
      "modelId": "gemini-2.5-flash",
      "remainingFraction": 0.92,
      "resetTime": "2025-01-16T00:00:00Z"
    }
  ]
}
```

> **Note:** `remainingFraction` is _remaining_ capacity (not used). The app
> converts it: `used_percent = 100 - remainingFraction * 100`.

---

## 5. GitHub Copilot (Device Flow)

**Auth:** GitHub OAuth token from Device Flow (`llmeter --login copilot`).

```bash
# Set your GitHub OAuth token (see §8 for how to extract it)
COPILOT_TOKEN="<your-github-oauth-token>"
```

### 5a. Fetch Usage (monthly quotas)

```bash
curl -s \
  -H "Authorization: token $COPILOT_TOKEN" \
  -H "Accept: application/json" \
  -H "Editor-Version: vscode/1.96.2" \
  -H "Editor-Plugin-Version: copilot-chat/0.26.7" \
  -H "User-Agent: GitHubCopilotChat/0.26.7" \
  -H "X-Github-Api-Version: 2025-04-01" \
  "https://api.github.com/copilot_internal/user" | jq .
```

**Expected response shape:**

```jsonc
{
  "login": "username",
  "copilot_plan": "individual",          // "individual", "business", "enterprise"
  "assigned_date": null,
  "quota_reset_date": "2026-03-01",
  "quota_reset_date_utc": "2026-03-01T00:00:00.000Z",
  "quota_snapshots": {
    "chat": {                            // unlimited — skipped by llmeter
      "entitlement": 0,
      "remaining": 0,
      "percent_remaining": 100.0,
      "quota_id": "chat",
      "unlimited": true
    },
    "completions": {                     // unlimited — skipped by llmeter
      "entitlement": 0,
      "remaining": 0,
      "percent_remaining": 100.0,
      "quota_id": "completions",
      "unlimited": true
    },
    "premium_interactions": {            // the one that matters
      "entitlement": 300,                // total monthly quota
      "remaining": 279,                  // remaining this month
      "percent_remaining": 93.0,         // 0–100 (remaining, not used)
      "quota_id": "premium_interactions",
      "unlimited": false
    }
  }
}
```

> **Note:** Only `premium_interactions` is tracked (chat/completions are unlimited).
> `percent_remaining` is _remaining_ capacity. The app converts it:
> `used_percent = 100 - percent_remaining`.

---

## 6. OpenAI API (Admin Key)

**Auth:** OpenAI Admin API key (`sk-admin-...`). Set via `OPENAI_ADMIN_KEY` env var or in settings.

```bash
# Set your admin API key
OPENAI_ADM_KEY="sk-admin-..."
```

### 5a. Fetch Monthly Costs

```bash
# Calculate current month boundaries (epoch seconds)
MONTH_START=$(date -u -j -f "%Y-%m-%d" "$(date -u +%Y-%m-01)" +%s 2>/dev/null \
  || date -u -d "$(date -u +%Y-%m-01)" +%s)
MONTH_END=$(date -u +%s)

curl -s -G \
  -H "Authorization: Bearer $OPENAI_ADM_KEY" \
  -H "Content-Type: application/json" \
  --data-urlencode "start_time=$MONTH_START" \
  --data-urlencode "end_time=$MONTH_END" \
  --data-urlencode "bucket_width=1d" \
  --data-urlencode "limit=31" \
  "https://api.openai.com/v1/organization/costs" | jq .
```

**Expected response shape:**

```jsonc
{
  "data": [
    {
      "start_time": 1735689600,
      "end_time": 1735776000,
      "results": [
        {
          "object": "costs.result",
          "amount": {
            "value": 12.34,            // USD
            "currency": "usd"
          },
          "line_item": "gpt-4o"
        }
      ]
    }
  ],
  "has_more": false,
  "next_page": null                    // pagination token if has_more=true
}
```

> **Pagination:** If `next_page` is returned, add `&page=<token>` to fetch the next page.

---

## 7. Anthropic API (Admin Key)

**Auth:** Anthropic Admin API key (`sk-ant-admin01-...`). Set via `ANTHROPIC_ADMIN_KEY` env var or in settings.

```bash
# Set your admin API key
ANTHROPIC_ADM_KEY="sk-ant-admin01-..."
```

### 6a. Fetch Monthly Cost Report

```bash
# Current month boundaries in ISO 8601
MONTH_START="$(date -u +%Y-%m-01T00:00:00Z)"
MONTH_END="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

curl -s -G \
  -H "x-api-key: $ANTHROPIC_ADM_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  --data-urlencode "starting_at=$MONTH_START" \
  --data-urlencode "ending_at=$MONTH_END" \
  --data-urlencode "bucket_width=1d" \
  --data-urlencode "limit=31" \
  "https://api.anthropic.com/v1/organizations/cost_report" | jq .
```

**Expected response shape:**

```jsonc
{
  "data": [
    {
      "results": [
        {
          "amount": "1234",            // cents (string), divide by 100 for USD
          "model": "claude-sonnet-4-20250514"
        }
      ]
    }
  ],
  "has_more": false,
  "next_page": null                    // pagination token if has_more=true
}
```

> **Pagination:** If `has_more` is true, add `&page=<next_page>` to fetch subsequent pages.

---

## 8. Opencode Zen (Auth Cookie)

**Auth:** The `auth` session cookie from opencode.ai. It's HttpOnly — extract
it from DevTools → Application → Cookies → opencode.ai → `auth`, or from a
running CDP session. Set via `api_key` in settings or `OPENCODE_AUTH_COOKIE`
env var.

```bash
# Set your auth cookie value (the full Fe26.2** string, not "auth=...")
OPENCODE_COOKIE="Fe26.2**..."
```

### 8a. Fetch Workspace Page (balance, monthly spend, usage history)

The page is server-rendered — all billing data is embedded as inline
JavaScript. The response is HTML, not JSON, so use `grep` to extract values.

```bash
curl -s \
  -H "Cookie: auth=$OPENCODE_COOKIE" \
  -H "Accept: text/html" \
  -H "User-Agent: llmeter/1.0" \
  -L "https://opencode.ai/zen" \
  | grep -oE '(balance|monthlyUsage|monthlyLimit):[0-9]+'
```

**Expected output:**

```
balance:1708723204
monthlyUsage:379059776
monthlyLimit:20
```

All cost integers are in units of 1e-8 USD — divide by `1e8` to get dollars.

To also extract the account email:

```bash
curl -s \
  -H "Cookie: auth=$OPENCODE_COOKIE" \
  -H "Accept: text/html" \
  -L "https://opencode.ai/zen" \
  | grep -oE '"[^"@]+@[^"]+"' | head -1
```

**Expected response shape** (embedded in the page's `<script>` block):

```js
// billing block
$R[40]($R[16], {
  balance: 1708723204,        // wallet balance  (÷ 1e8 = $17.09)
  monthlyUsage: 379059776,    // spend this month (÷ 1e8 = $3.79)
  monthlyLimit: 20,           // monthly cap in whole USD ($20)
  reload: true,
  reloadAmount: 50,
  ...
});

// per-request usage list
$R[40]($R[24], [
  {
    id: "usg_...",
    timeCreated: new Date("2026-02-24T06:15:54.000Z"),
    model: "gemini-3.1-pro",
    provider: "google",
    inputTokens: 1133,
    outputTokens: 28,
    cost: 1161740,            // (÷ 1e8 = $0.0116)
    enrichment: { plan: "byok" }
  },
  ...
]);
```

> **Note:** `https://opencode.ai/zen` redirects to
> `https://opencode.ai/workspace/<workspace_id>`. The `-L` flag follows
> the redirect automatically.

---

## 9. Extracting Tokens from auth.json

After running `llmeter --login <provider>`, credentials are stored in
`~/.config/llmeter/auth.json`. Here's how to extract them:

```bash
AUTH_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/llmeter/auth.json"
```

### Claude

```bash
CLAUDE_TOKEN=$(jq -r '.anthropic.access' "$AUTH_FILE")
```

### Codex (ChatGPT)

```bash
CODEX_TOKEN=$(jq -r '.["openai-codex"].access' "$AUTH_FILE")
CODEX_ACCOUNT_ID=$(jq -r '.["openai-codex"].account_id // .["openai-codex"].accountId' "$AUTH_FILE")
```

### Cursor

```bash
CURSOR_COOKIE=$(jq -r '.cursor.cookie' "$AUTH_FILE")
```

### Gemini

```bash
GEMINI_TOKEN=$(jq -r '.["google-gemini-cli"].access' "$AUTH_FILE")
GEMINI_PROJECT_ID=$(jq -r '.["google-gemini-cli"].projectId // empty' "$AUTH_FILE")
```

### GitHub Copilot

```bash
COPILOT_TOKEN=$(jq -r '.["github-copilot"].access' "$AUTH_FILE")
```

### OpenAI API / Anthropic API / Opencode Zen

These use API keys (or cookies-as-keys), not OAuth tokens. They come from
env vars or `settings.json`:

```bash
SETTINGS_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/llmeter/settings.json"

# OpenAI
OPENAI_ADM_KEY=$(jq -r '.providers[] | select(.id=="openai-api") | .api_key // empty' "$SETTINGS_FILE")
: "${OPENAI_ADM_KEY:=$OPENAI_ADMIN_KEY}"   # fall back to env var

# Anthropic
ANTHROPIC_ADM_KEY=$(jq -r '.providers[] | select(.id=="anthropic-api") | .api_key // empty' "$SETTINGS_FILE")
: "${ANTHROPIC_ADM_KEY:=$ANTHROPIC_ADMIN_KEY}"   # fall back to env var

# Opencode Zen
OPENCODE_COOKIE=$(jq -r '.providers[] | select(.id=="opencode") | .api_key // empty' "$SETTINGS_FILE")
: "${OPENCODE_COOKIE:=$OPENCODE_AUTH_COOKIE}"   # fall back to env var
```

---

## Quick Health-Check Script

Run all providers at once (skips any without credentials):

```bash
#!/usr/bin/env bash
set -euo pipefail

AUTH_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/llmeter/auth.json"
SETTINGS_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/llmeter/settings.json"

echo "=== Claude ==="
CLAUDE_TOKEN=$(jq -r '.anthropic.access // empty' "$AUTH_FILE" 2>/dev/null)
if [[ -n "$CLAUDE_TOKEN" ]]; then
  curl -sw '\nHTTP %{http_code}\n' \
    -H "Authorization: Bearer $CLAUDE_TOKEN" \
    -H "Accept: application/json" \
    -H "anthropic-beta: oauth-2025-04-20" \
    "https://api.anthropic.com/api/oauth/usage" | jq .
else
  echo "SKIP — no token"
fi

echo ""
echo "=== Codex ==="
CODEX_TOKEN=$(jq -r '.["openai-codex"].access // empty' "$AUTH_FILE" 2>/dev/null)
CODEX_ACCOUNT_ID=$(jq -r '.["openai-codex"].account_id // .["openai-codex"].accountId // empty' "$AUTH_FILE" 2>/dev/null)
if [[ -n "$CODEX_TOKEN" && -n "$CODEX_ACCOUNT_ID" ]]; then
  curl -sw '\nHTTP %{http_code}\n' \
    -H "Authorization: Bearer $CODEX_TOKEN" \
    -H "ChatGPT-Account-Id: $CODEX_ACCOUNT_ID" \
    -H "Accept: application/json" \
    "https://chatgpt.com/backend-api/wham/usage" | jq .
else
  echo "SKIP — no token"
fi

echo ""
echo "=== Cursor ==="
CURSOR_COOKIE=$(jq -r '.cursor.cookie // empty' "$AUTH_FILE" 2>/dev/null)
if [[ -n "$CURSOR_COOKIE" ]]; then
  curl -sw '\nHTTP %{http_code}\n' \
    -H "Cookie: $CURSOR_COOKIE" \
    -H "Accept: application/json" \
    "https://cursor.com/api/usage-summary" | jq .
else
  echo "SKIP — no cookie"
fi

echo ""
echo "=== Gemini ==="
GEMINI_TOKEN=$(jq -r '.["google-gemini-cli"].access // empty' "$AUTH_FILE" 2>/dev/null)
if [[ -n "$GEMINI_TOKEN" ]]; then
  curl -sw '\nHTTP %{http_code}\n' -X POST \
    -H "Authorization: Bearer $GEMINI_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{}' \
    "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota" | jq .
else
  echo "SKIP — no token"
fi

echo ""
echo "=== GitHub Copilot ==="
COPILOT_TOKEN=$(jq -r '.["github-copilot"].access // empty' "$AUTH_FILE" 2>/dev/null)
if [[ -n "$COPILOT_TOKEN" ]]; then
  curl -sw '\nHTTP %{http_code}\n' \
    -H "Authorization: token $COPILOT_TOKEN" \
    -H "Accept: application/json" \
    -H "Editor-Version: vscode/1.96.2" \
    -H "Editor-Plugin-Version: copilot-chat/0.26.7" \
    -H "User-Agent: GitHubCopilotChat/0.26.7" \
    -H "X-Github-Api-Version: 2025-04-01" \
    "https://api.github.com/copilot_internal/user" | jq .
else
  echo "SKIP — no token"
fi

echo ""
echo "=== OpenAI API ==="
OPENAI_ADM_KEY="${OPENAI_ADMIN_KEY:-}"
if [[ -z "$OPENAI_ADM_KEY" && -f "$SETTINGS_FILE" ]]; then
  OPENAI_ADM_KEY=$(jq -r '.providers[]? | select(.id=="openai-api") | .api_key // empty' "$SETTINGS_FILE" 2>/dev/null)
fi
if [[ -n "$OPENAI_ADM_KEY" ]]; then
  MONTH_START=$(date -u +%Y-%m-01T00:00:00Z)
  START_TS=$(date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$MONTH_START" +%s 2>/dev/null \
    || date -u -d "$MONTH_START" +%s)
  END_TS=$(date -u +%s)
  curl -sw '\nHTTP %{http_code}\n' -G \
    -H "Authorization: Bearer $OPENAI_ADM_KEY" \
    --data-urlencode "start_time=$START_TS" \
    --data-urlencode "end_time=$END_TS" \
    --data-urlencode "bucket_width=1d" \
    --data-urlencode "limit=31" \
    "https://api.openai.com/v1/organization/costs" | jq .
else
  echo "SKIP — no API key"
fi

echo ""
echo "=== Anthropic API ==="
ANTHROPIC_ADM_KEY="${ANTHROPIC_ADMIN_KEY:-}"
if [[ -z "$ANTHROPIC_ADM_KEY" && -f "$SETTINGS_FILE" ]]; then
  ANTHROPIC_ADM_KEY=$(jq -r '.providers[]? | select(.id=="anthropic-api") | .api_key // empty' "$SETTINGS_FILE" 2>/dev/null)
fi
if [[ -n "$ANTHROPIC_ADM_KEY" ]]; then
  MONTH_START="$(date -u +%Y-%m-01T00:00:00Z)"
  MONTH_END="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  curl -sw '\nHTTP %{http_code}\n' -G \
    -H "x-api-key: $ANTHROPIC_ADM_KEY" \
    -H "anthropic-version: 2023-06-01" \
    --data-urlencode "starting_at=$MONTH_START" \
    --data-urlencode "ending_at=$MONTH_END" \
    --data-urlencode "bucket_width=1d" \
    --data-urlencode "limit=31" \
    "https://api.anthropic.com/v1/organizations/cost_report" | jq .
else
  echo "SKIP — no API key"
fi

echo ""
echo "=== Opencode Zen ==="
OPENCODE_COOKIE="${OPENCODE_AUTH_COOKIE:-}"
if [[ -z "$OPENCODE_COOKIE" && -f "$SETTINGS_FILE" ]]; then
  OPENCODE_COOKIE=$(jq -r '.providers[]? | select(.id=="opencode") | .api_key // empty' "$SETTINGS_FILE" 2>/dev/null)
fi
if [[ -n "$OPENCODE_COOKIE" ]]; then
  curl -sw '\nHTTP %{http_code}\n' \
    -H "Cookie: auth=$OPENCODE_COOKIE" \
    -H "Accept: text/html" \
    -H "User-Agent: llmeter/1.0" \
    -L "https://opencode.ai/zen" \
    | grep -oE '(balance|monthlyUsage|monthlyLimit):[0-9]+'
else
  echo "SKIP — no cookie"
fi
```

---

## Common Error Codes

| HTTP Status | Meaning | Fix |
|---|---|---|
| **401** | Token expired / invalid | Re-authenticate: `llmeter --login <provider>` |
| **403** | Missing permissions / wrong key type | Use an admin key; re-login with correct scopes |
| **429** | Rate limited | Wait and retry |
| **404** | Endpoint not found | Check URL; API may have changed |
