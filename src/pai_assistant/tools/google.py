"""Gmail and Google Calendar tools.

Setup: create an OAuth client ("Desktop app") in Google Cloud Console with the Gmail and
Calendar APIs enabled, save it as ``~/.config/pai-assistant/google_credentials.json`` and
run ``assistant auth google``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from email.message import EmailMessage
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from pydantic_ai import FunctionToolset, ModelRetry, RunContext

from ..config import Settings
from ..deps import Deps

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",  # drafts + send
    "https://www.googleapis.com/auth/calendar.events",
]

toolset: FunctionToolset[Deps] = FunctionToolset(id="google")


# ---- auth ------------------------------------------------------------------
def _client_config(settings: Settings) -> dict[str, Any]:
    """Load the OAuth client JSON downloaded from Google Cloud Console, with useful errors."""
    path = settings.google_credentials_path
    if not path.exists():
        raise SystemExit(
            f"Missing {path}.\n"
            "Create an OAuth client (type: Desktop app) in Google Cloud Console, enable the Gmail and "
            "Google Calendar APIs, and save the downloaded JSON at that path."
        )
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError):
        config = None
    if not isinstance(config, dict) or not isinstance(config.get("installed", config.get("web")), dict):
        raise SystemExit(
            f"{path} is not an OAuth client secrets file.\n"
            "Download the JSON for a Desktop-app OAuth client (Google Cloud Console \u2192 APIs & Services "
            "\u2192 Credentials \u2192 your client \u2192 Download JSON) and save that file there unchanged. "
            'It should look like {"installed": {"client_id": "...", "client_secret": "...", ...}}.'
        )
    return config


def authorize(settings: Settings) -> None:
    """Interactive OAuth flow (opens a browser). Stores a refresh token in the data dir."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(_client_config(settings), SCOPES)
    creds = flow.run_local_server(port=0)
    settings.google_token_path.write_text(creds.to_json())
    settings.google_token_path.chmod(0o600)


def is_authorized(settings: Settings) -> bool:
    return settings.google_token_path.exists()


def _credentials(settings: Settings) -> Credentials:
    creds = Credentials.from_authorized_user_file(str(settings.google_token_path), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        settings.google_token_path.write_text(creds.to_json())
    return creds


_services: dict[str, Any] = {}


def _service(settings: Settings, name: str, version: str) -> Any:
    if name not in _services:
        _services[name] = build(name, version, credentials=_credentials(settings), cache_discovery=False)
    return _services[name]


def gmail(settings: Settings) -> Any:
    return _service(settings, "gmail", "v1")


def calendar(settings: Settings) -> Any:
    return _service(settings, "calendar", "v3")


async def _run(request: Any) -> Any:
    """googleapiclient is synchronous; run requests in a thread."""
    return await asyncio.to_thread(request.execute)


# ---- gmail -----------------------------------------------------------------
def _header(headers: list[dict], name: str) -> str:
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


def _extract_body(payload: dict) -> str:
    """Prefer text/plain; fall back to the first text/html part."""

    def walk(part: dict, mime: str) -> str | None:
        if part.get("mimeType") == mime and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode(errors="replace")
        for sub in part.get("parts", []) or []:
            if (found := walk(sub, mime)) is not None:
                return found
        return None

    return walk(payload, "text/plain") or walk(payload, "text/html") or ""


@toolset.tool
async def search_email(ctx: RunContext[Deps], query: str = "in:inbox", max_results: int = 10) -> list[dict]:
    """Search Gmail and return message summaries (id, from, subject, date, snippet).

    Args:
        query: Gmail search syntax, e.g. "is:unread newer_than:2d", "from:bob subject:invoice".
        max_results: Maximum messages to return (max 50).
    """
    svc = gmail(ctx.deps.settings)
    resp = await _run(svc.users().messages().list(userId="me", q=query, maxResults=min(max_results, 50)))
    out = []
    for m in resp.get("messages", []):
        msg = await _run(
            svc.users()
            .messages()
            .get(userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "To", "Subject", "Date"])
        )
        h = msg["payload"]["headers"]
        out.append(
            {
                "id": msg["id"],
                "thread_id": msg["threadId"],
                "from": _header(h, "From"),
                "subject": _header(h, "Subject"),
                "date": _header(h, "Date"),
                "unread": "UNREAD" in msg.get("labelIds", []),
                "snippet": msg.get("snippet", ""),
            }
        )
    return out


@toolset.tool
async def read_email(ctx: RunContext[Deps], message_id: str) -> dict:
    """Read the full content of one email by id (from search_email)."""
    msg = await _run(gmail(ctx.deps.settings).users().messages().get(userId="me", id=message_id, format="full"))
    h = msg["payload"]["headers"]
    body = _extract_body(msg["payload"])
    return {
        "id": msg["id"],
        "thread_id": msg["threadId"],
        "from": _header(h, "From"),
        "to": _header(h, "To"),
        "cc": _header(h, "Cc"),
        "subject": _header(h, "Subject"),
        "date": _header(h, "Date"),
        "message_id_header": _header(h, "Message-ID"),
        "body": body[:20_000],
    }


def _build_raw(to: str, subject: str, body: str, cc: str | None, in_reply_to: str | None) -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


@toolset.tool(requires_approval=True)
async def create_email_draft(
    ctx: RunContext[Deps],
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    reply_to_message_id: str | None = None,
    thread_id: str | None = None,
) -> str:
    """Create a Gmail draft (not sent). Prefer this over send_email unless the user explicitly wants it sent.

    Args:
        to: Recipient address(es), comma separated.
        subject: Subject line.
        body: Plain-text body.
        cc: Optional CC addresses.
        reply_to_message_id: The Message-ID header of the email being replied to (from read_email).
        thread_id: Gmail thread id when replying.
    """
    message: dict[str, Any] = {"raw": _build_raw(to, subject, body, cc, reply_to_message_id)}
    if thread_id:
        message["threadId"] = thread_id
    draft = await _run(gmail(ctx.deps.settings).users().drafts().create(userId="me", body={"message": message}))
    return f"Draft created (id {draft['id']})."


@toolset.tool(requires_approval=True)
async def send_email(
    ctx: RunContext[Deps],
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    reply_to_message_id: str | None = None,
    thread_id: str | None = None,
) -> str:
    """Send an email immediately. Arguments as for create_email_draft."""
    message: dict[str, Any] = {"raw": _build_raw(to, subject, body, cc, reply_to_message_id)}
    if thread_id:
        message["threadId"] = thread_id
    sent = await _run(gmail(ctx.deps.settings).users().messages().send(userId="me", body=message))
    return f"Sent (id {sent['id']})."


# ---- calendar --------------------------------------------------------------
def _event_summary(e: dict) -> dict:
    return {
        "id": e["id"],
        "summary": e.get("summary", "(no title)"),
        "start": e["start"].get("dateTime") or e["start"].get("date"),
        "end": e["end"].get("dateTime") or e["end"].get("date"),
        "location": e.get("location"),
        "attendees": [a.get("email") for a in e.get("attendees", [])],
        "description": (e.get("description") or "")[:500],
    }


@toolset.tool
async def list_events(
    ctx: RunContext[Deps], time_min: str, time_max: str, query: str | None = None, calendar_id: str = "primary"
) -> list[dict]:
    """List calendar events in a time range.

    Args:
        time_min: Start, RFC3339 with offset (e.g. 2026-11-17T00:00:00+01:00).
        time_max: End, RFC3339 with offset.
        query: Optional free-text filter.
        calendar_id: Calendar id; "primary" is the user's main calendar.
    """
    resp = await _run(
        calendar(ctx.deps.settings)
        .events()
        .list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            q=query,
            singleEvents=True,
            orderBy="startTime",
            maxResults=100,
        )
    )
    return [_event_summary(e) for e in resp.get("items", [])]


def _time(value: str, tz: str) -> dict:
    # A bare date (YYYY-MM-DD) means an all-day event.
    return {"date": value} if len(value) == 10 else {"dateTime": value, "timeZone": tz}


@toolset.tool(requires_approval=True)
async def create_event(
    ctx: RunContext[Deps],
    summary: str,
    start: str,
    end: str,
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    calendar_id: str = "primary",
) -> dict:
    """Create a calendar event.

    Args:
        summary: Event title.
        start: RFC3339 datetime, or YYYY-MM-DD for an all-day event.
        end: RFC3339 datetime, or YYYY-MM-DD (exclusive) for an all-day event.
        description: Optional details.
        location: Optional location.
        attendees: Optional attendee emails (they will receive invitations).
        calendar_id: Calendar id.
    """
    tz = ctx.deps.settings.timezone
    body: dict[str, Any] = {"summary": summary, "start": _time(start, tz), "end": _time(end, tz)}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
    e = await _run(calendar(ctx.deps.settings).events().insert(calendarId=calendar_id, body=body, sendUpdates="all"))
    return _event_summary(e)


@toolset.tool(requires_approval=True)
async def update_event(
    ctx: RunContext[Deps],
    event_id: str,
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
    calendar_id: str = "primary",
) -> dict:
    """Change fields of an existing event. Only provided fields are updated."""
    tz = ctx.deps.settings.timezone
    patch: dict[str, Any] = {}
    if summary is not None:
        patch["summary"] = summary
    if start is not None:
        patch["start"] = _time(start, tz)
    if end is not None:
        patch["end"] = _time(end, tz)
    if description is not None:
        patch["description"] = description
    if location is not None:
        patch["location"] = location
    if not patch:
        raise ModelRetry("Provide at least one field to update.")
    e = await _run(
        calendar(ctx.deps.settings)
        .events()
        .patch(calendarId=calendar_id, eventId=event_id, body=patch, sendUpdates="all")
    )
    return _event_summary(e)


@toolset.tool(requires_approval=True)
async def delete_event(ctx: RunContext[Deps], event_id: str, calendar_id: str = "primary") -> str:
    """Delete a calendar event."""
    await _run(calendar(ctx.deps.settings).events().delete(calendarId=calendar_id, eventId=event_id, sendUpdates="all"))
    return "Deleted."
