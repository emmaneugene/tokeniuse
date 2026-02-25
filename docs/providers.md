# Provider Overview

How each provider authenticates and what data it fetches.

---

## Summary

| Provider | Auth method | Data source | Tracks |
|---|---|---|---|
| Claude | PKCE OAuth | Anthropic OAuth API | Rate windows, extra spend |
| Codex | PKCE OAuth | ChatGPT usage API | Rate windows, credits |
| Cursor | Session cookie | cursor.com REST API | Plan usage, on-demand spend |
| Gemini | PKCE OAuth (Google) | Cloud Code internal API | Per-model quota |
| Copilot | GitHub Device Flow | GitHub Copilot internal API | Premium request quota |
| OpenAI API | Admin API key | OpenAI costs API | Monthly spend |
| Anthropic API | Admin API key | Anthropic cost report API | Monthly spend |
| Opencode Zen | Auth cookie | SSR workspace page | Balance, monthly spend |

---

## Claude

**Auth:** PKCE OAuth via `console.anthropic.com`. Login opens a browser,
completes the consent flow, and receives an access + refresh token pair.
Tokens are stored in `auth.json` and automatically refreshed on each fetch.

**Setup:** `llmeter --login claude`

**Endpoints:**

- `GET /api/oauth/usage` — returns rate-limit utilisation windows
- `GET /api/oauth/profile` — returns account email and plan

**What is tracked:**

- **Session (5h):** utilisation % of the rolling 5-hour token window,
  with the exact reset time
- **Weekly:** utilisation % of the 7-day window
- **Sonnet / Opus:** model-specific weekly window when present
- **Extra spend:** monthly on-demand cost in USD if enabled on the account

**Notes:**

- The `anthropic-beta: oauth-2025-04-20` header is required on all calls
- The refresh token is long-lived; re-login is rarely needed

---

## Codex (ChatGPT)

**Auth:** PKCE OAuth via `auth.openai.com` with a local callback server on
`localhost:1455`. Login opens a browser and captures the authorization code
on return. The access token is a JWT; the `chatgpt_account_id` claim is
extracted from it and stored alongside the tokens.

**Setup:** `llmeter --login codex`

**Endpoints:**

- `GET /backend-api/wham/usage` — rate windows, plan type, credits balance
  (requires `ChatGPT-Account-Id` header)

**What is tracked:**

- **Session (5h):** primary rolling window utilisation %
- **Weekly:** secondary rolling window utilisation %
- **Credits:** remaining balance if the account has a credits allocation
- **Plan:** free, plus, pro, team, etc. extracted from the response

**Notes:**

- Tokens expire and are refreshed against `auth.openai.com/oauth/token`
- The account ID is derived from the JWT at login time and persisted;
  it does not change between refreshes

---

## Cursor

**Auth:** Browser session cookie (`WorkosCursorSessionToken` or
`__Secure-next-auth.session-token`). The user copies it from DevTools and
pastes it at `llmeter --login cursor`. There is no token refresh — the
cookie is valid until Cursor invalidates it.

**Setup:** `llmeter --login cursor`

**Endpoints:**

- `GET /api/usage-summary` — billing cycle, plan spend, on-demand spend
- `GET /api/auth/me` — email and user sub ID
- `GET /api/usage?user=<sub>` — request counts for enterprise request plans

**What is tracked:**

- **Plan:** spend % against the plan's dollar limit (hobby/pro), or request
  count % against the request cap (enterprise)
- **On-demand:** additional spend % against the on-demand limit when set
- **Extra spend:** absolute dollar amounts for the current billing cycle

**Notes:**

- On a 401 or 403 the stored cookie is automatically cleared and the user
  is prompted to re-login
- Enterprise accounts that have `maxRequestUsage` set are detected
  automatically and shown as request counts rather than dollar amounts
- The email discovered from `/api/auth/me` is saved back to `auth.json`
- The session token is a WorkOS / Next.js Auth session; Cursor has not
  published the exact TTL but it is typically ~30 days. It is invalidated
  early by a password change, explicit sign-out, or WorkOS session
  revocation. There is no refresh mechanism — a 401/403 means the user
  must paste a new cookie.

---

## Gemini

**Auth:** PKCE OAuth via Google (`oauth2.googleapis.com`), using the same
client ID as the Gemini CLI. Login opens a browser and listens for the
callback on `localhost:8085`. Access tokens expire in ~1 hour and are
refreshed automatically.

**Setup:** `llmeter --login gemini`

**Endpoints:**

- `POST /v1internal:loadCodeAssist` — discovers the account tier and
  associated GCP project ID
- `POST /v1internal:retrieveUserQuota` — returns per-model quota buckets

**What is tracked:**

- **Pro (24h):** `remainingFraction` for the primary (Pro/standard) model,
  converted to `used_percent = 100 − remainingFraction × 100`
- **Flash (24h):** `remainingFraction` for the Flash model
- **Account tier:** free, standard, or legacy, shown as the plan label

**Notes:**

- The Cloud Code API (`cloudcode-pa.googleapis.com`) is an internal Google
  endpoint used by the Gemini CLI and VS Code extension
- `remainingFraction` represents capacity _remaining_, not used; the app
  inverts it
- If a GCP project ID is discovered it is passed to the quota call; some
  tier configurations require it

---

## GitHub Copilot

**Auth:** GitHub Device Flow — the user visits `github.com/login/device`,
enters a short code displayed by llmeter, and authorises the app. The
resulting GitHub OAuth token is long-lived and has no refresh mechanism.

**Setup:** `llmeter --login copilot`

**Endpoints:**

- `GET /copilot_internal/user` — quota snapshots and plan info

**What is tracked:**

- **Premium interactions:** monthly quota of premium model requests
  (`entitlement`, `remaining`, `percent_remaining`), converted to
  `used_percent = 100 − percent_remaining`

**Notes:**

- `chat` and `completions` quotas are unlimited and intentionally skipped
- The token is sent as `Authorization: token <value>` (not Bearer)
- The `Editor-Version` / `Editor-Plugin-Version` headers are required by
  the endpoint

---

## OpenAI API

**Auth:** OpenAI Admin API key (`sk-admin-...`). Set via `OPENAI_ADMIN_KEY`
env var or `api_key` in `settings.json`. No login flow.

**Endpoint:**

- `GET /v1/organization/costs` — daily cost buckets for a date range

**What is tracked:**

- **Monthly spend:** sum of all daily cost buckets for the current calendar
  month in USD
- If `monthly_budget` is set in config, spend is shown as a % of that
  budget; otherwise it is shown as a raw dollar amount

**Notes:**

- A standard API key (`sk-...`) will not work; an admin key is required
  for the costs endpoint
- The endpoint is paginated; llmeter follows `next_page` tokens until all
  buckets for the month are consumed

---

## Anthropic API

**Auth:** Anthropic Admin API key (`sk-ant-admin01-...`). Set via
`ANTHROPIC_ADMIN_KEY` env var or `api_key` in `settings.json`. No login
flow.

**Endpoint:**

- `GET /v1/organizations/cost_report` — daily cost buckets for a date range

**What is tracked:**

- **Monthly spend:** sum of all daily cost amounts for the current calendar
  month, converted from cents to USD
- If `monthly_budget` is set in config, spend is shown as a % of that
  budget; otherwise it is shown as a raw dollar amount

**Notes:**

- Amounts in the response are strings representing cents (e.g. `"1234"` =
  $12.34); llmeter divides by 100
- A standard API key will not work; admin keys are required for cost
  reporting
- The endpoint is paginated via `has_more` / `next_page`

---

## Opencode Zen

**Auth:** The `auth` session cookie from opencode.ai. It is HttpOnly and
cannot be read from the browser console — extract it from DevTools
(Application → Cookies → opencode.ai → `auth`). Set via `api_key` in
`settings.json` or the `OPENCODE_AUTH_COOKIE` env var. No login flow.

**Endpoint:**

- `GET https://opencode.ai/zen` — server-rendered HTML (follows redirect to
  `/workspace/<id>`)

**What is tracked:**

- **Monthly spend:** spend this month vs the monthly cap, shown as a % bar
  and `$X.XX / $Y` label
- **Balance:** current wallet balance in USD
- **Email:** account email extracted from the page

By default, the monthly cap is synced from the Opencode Zen platform's own
`monthlyLimit` value. If you explicitly set `monthly_budget` in
`settings.json`, llmeter uses that value instead.

**Notes:**

- The page is server-side rendered (SolidStart); all billing data is
  embedded as inline JavaScript hydration — there is no separate JSON API
- The monthly cap shown by default comes from the platform payload
  (`monthlyLimit`); set `monthly_budget` only if you want to override it
- All cost integers on the page are in units of 1e-8 USD; llmeter divides
  by `1e8` to get dollars
- The session cookie uses the `@hapi/iron` sealed format (`Fe26.2**…`),
  which embeds the expiry as a millisecond timestamp in the cookie itself.
  Cookies issued by opencode.ai currently expire after approximately one
  year. There is no refresh mechanism — a 401/403 means the user must
  supply a new cookie.
