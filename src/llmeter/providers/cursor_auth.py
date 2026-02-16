"""Cursor authentication — cookie-based session storage.

Cursor uses browser session cookies rather than OAuth tokens.
The cookie string is stored in auth.json under the "cursor" key.

Schema:
{
  "cursor": {
    "type": "cookie",
    "cookie": "WorkosCursorSessionToken=...; ...",
    "email": "user@example.com"
  }
}
"""

from __future__ import annotations

from typing import Optional

from .. import auth

PROVIDER_KEY = "cursor"


def load_credentials() -> Optional[dict]:
    """Load stored Cursor cookie credentials, or None."""
    creds = auth.load_provider(PROVIDER_KEY)
    if creds is None:
        return None
    if not creds.get("cookie"):
        return None
    return creds


def save_credentials(cookie: str, email: str | None = None) -> None:
    """Save Cursor cookie credentials."""
    creds: dict = {
        "type": "cookie",
        "cookie": cookie,
    }
    if email:
        creds["email"] = email
    auth.save_provider(PROVIDER_KEY, creds)


def clear_credentials() -> None:
    """Remove stored Cursor credentials."""
    auth.clear_provider(PROVIDER_KEY)
