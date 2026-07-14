"""Gmail API client for the journal fetch pipeline.

Handles OAuth (Desktop flow), message search, HTML body extraction,
and the journal/processed label used as the idempotency key.
"""

import base64
import logging
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
PROCESSED_LABEL = "journal/processed"

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(REPO_DIR, "credentials.json")
TOKEN_PATH = os.path.join(REPO_DIR, "token.json")


class AuthError(Exception):
    """Raised when Gmail auth cannot be established non-interactively."""


def get_service(interactive: bool = False):
    """Return an authenticated Gmail API service.

    interactive=True runs the browser OAuth consent flow if needed.
    interactive=False (launchd path) raises AuthError instead of blocking.
    """
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
        except Exception as e:
            log.warning("Token refresh failed: %s", e)
            creds = None

    if not creds or not creds.valid:
        if not interactive:
            raise AuthError(
                "No valid Gmail token. Run: python journal_fetch.py --auth"
            )
        if not os.path.exists(CREDENTIALS_PATH):
            raise AuthError(
                f"Missing {CREDENTIALS_PATH}. Download an OAuth Desktop client "
                "JSON from Google Cloud console (Gmail API enabled) first."
            )
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
        creds = flow.run_local_server(port=0)
        _save_token(creds)

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _save_token(creds) -> None:
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())
    os.chmod(TOKEN_PATH, 0o600)


def search_messages(service, query: str) -> list[dict]:
    """Return full message resources matching the Gmail search query."""
    results = []
    page_token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token)
            .execute()
        )
        for ref in resp.get("messages", []):
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=ref["id"], format="full")
                .execute()
            )
            results.append(msg)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def get_html_body(msg: dict) -> str:
    """Extract the HTML (fallback: plain text) body from a message resource."""
    html, text = "", ""

    def walk(part):
        nonlocal html, text
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            if mime == "text/html":
                html += decoded
            elif mime == "text/plain":
                text += decoded
        for sub in part.get("parts", []):
            walk(sub)

    walk(msg.get("payload", {}))
    return html or text


def get_header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def ensure_label(service, name: str = PROCESSED_LABEL) -> str:
    """Return the label id for `name`, creating the label if missing."""
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for lb in labels:
        if lb["name"] == name:
            return lb["id"]
    created = (
        service.users()
        .labels()
        .create(userId="me", body={"name": name})
        .execute()
    )
    log.info("Created Gmail label %s", name)
    return created["id"]


def mark_processed(service, msg_id: str, label_id: str) -> None:
    service.users().messages().modify(
        userId="me", id=msg_id, body={"addLabelIds": [label_id]}
    ).execute()
