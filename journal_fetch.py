#!/usr/bin/env python3
"""Fetch Kindle Scribe journal emails from Gmail, download the PDF, OCR to text.

Nightly flow: handwritten journal shared from Kindle Scribe -> Amazon emails a
download link -> this script (run each morning by launchd) downloads the PDF,
OCRs it, and writes entries/YYYY-MM-DD.md. Processed emails get the
journal/processed Gmail label so reruns are no-ops.

Usage:
  python journal_fetch.py --auth   # one-time browser OAuth
  python journal_fetch.py          # normal run
"""

import argparse
import html as html_lib
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import requests

import gmail_client
from ocr import get_backend

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
INBOX_DIR = os.path.join(REPO_DIR, "inbox")
ENTRIES_DIR = os.path.join(REPO_DIR, "entries")

DEFAULT_QUERY = (
    "from:do-not-reply@amazon.com newer_than:7d -label:journal/processed"
)
BANGKOK = timezone(timedelta(hours=7))

log = logging.getLogger("journal_fetch")


def load_env() -> None:
    """Load KEY=VALUE pairs from .env next to this script (no dependency)."""
    path = os.path.join(REPO_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def extract_download_url(body_html: str) -> str | None:
    """Find the Kindle file download link in the share email body.

    Amazon share emails link through https://www.amazon.com/gp/f.html?...
    which redirects to a presigned S3 URL for the PDF.
    """
    hrefs = re.findall(r'href=[\'"]([^\'"]+)[\'"]', body_html, flags=re.I)
    hrefs = [html_lib.unescape(h) for h in hrefs]
    for h in hrefs:
        if "/gp/f.html" in h:
            return h
    for h in hrefs:
        if "amazon" in h and "download" in h.lower():
            return h
    return None


def download_pdf(url: str, dest_path: str) -> bool:
    """Download the PDF, following the Amazon redirect. True on success."""
    resp = requests.get(url, allow_redirects=True, timeout=60)
    resp.raise_for_status()
    if not resp.content.startswith(b"%PDF"):
        log.error(
            "Download did not return a PDF (content-type=%s, %d bytes) — "
            "link may have expired or requires login",
            resp.headers.get("content-type"),
            len(resp.content),
        )
        return False
    with open(dest_path, "wb") as f:
        f.write(resp.content)
    return True


def ocr_pdf(pdf_path: str) -> list[str]:
    """Render each PDF page to PNG and OCR it. Returns per-page text."""
    import fitz  # PyMuPDF

    backend = get_backend()
    dpi = int(os.environ.get("OCR_DPI", "300"))
    pages = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            pages.append(backend.ocr_image(pix.tobytes("png")))
    return pages


def write_entry(entry_date: str, msg_id: str, pdf_path: str, pages: list[str]) -> str:
    """Append an OCR'd email to entries/<date>.md. Returns the entry path."""
    os.makedirs(ENTRIES_DIR, exist_ok=True)
    entry_path = os.path.join(ENTRIES_DIR, f"{entry_date}.md")
    marker = f"source_msg_id: {msg_id}"

    if os.path.exists(entry_path):
        with open(entry_path) as f:
            if marker in f.read():
                log.info("Entry %s already contains %s — skipping write", entry_path, msg_id)
                return entry_path

    backend_name = os.environ.get("OCR_BACKEND", "macos")
    body = "\n\n---\n\n".join(p.strip() for p in pages)
    block = (
        f"---\n"
        f"date: {entry_date}\n"
        f"{marker}\n"
        f"pdf: {os.path.relpath(pdf_path, REPO_DIR)}\n"
        f"ocr_backend: {backend_name}\n"
        f"---\n\n"
        f"{body}\n"
    )
    mode = "a" if os.path.exists(entry_path) else "w"
    with open(entry_path, mode) as f:
        if mode == "a":
            f.write("\n")
        f.write(block)
    return entry_path


def process_message(service, msg: dict, label_id: str) -> bool:
    """Handle one share email end-to-end. True if fully processed."""
    msg_id = msg["id"]
    subject = gmail_client.get_header(msg, "Subject")
    received = datetime.fromtimestamp(
        int(msg["internalDate"]) / 1000, tz=timezone.utc
    ).astimezone(BANGKOK)
    entry_date = received.strftime("%Y-%m-%d")
    log.info("Processing %s (%s) — %r", msg_id, entry_date, subject)

    url = extract_download_url(gmail_client.get_html_body(msg))
    if not url:
        log.error("No download link found in %s — leaving unprocessed", msg_id)
        return False

    os.makedirs(INBOX_DIR, exist_ok=True)
    pdf_path = os.path.join(INBOX_DIR, f"{entry_date}-{msg_id[:8]}.pdf")
    if os.path.exists(pdf_path):
        log.info("PDF already downloaded: %s", pdf_path)
    elif not download_pdf(url, pdf_path):
        return False

    pages = ocr_pdf(pdf_path)
    if not any(p.strip() for p in pages):
        log.warning("OCR produced no text for %s — writing empty entry anyway", pdf_path)

    entry_path = write_entry(entry_date, msg_id, pdf_path, pages)
    gmail_client.mark_processed(service, msg_id, label_id)
    log.info("Done: %s (%d pages) -> %s", pdf_path, len(pages), entry_path)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--auth", action="store_true",
        help="run interactive browser OAuth flow and exit",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    load_env()

    try:
        service = gmail_client.get_service(interactive=args.auth)
    except gmail_client.AuthError as e:
        log.error("%s", e)
        return 1
    if args.auth:
        log.info("Auth OK — token saved to token.json")
        return 0

    query = os.environ.get("GMAIL_QUERY", DEFAULT_QUERY)
    messages = gmail_client.search_messages(service, query)
    log.info("Query %r matched %d message(s)", query, len(messages))
    if not messages:
        return 0

    label_id = gmail_client.ensure_label(service)
    ok = fail = 0
    for msg in messages:
        try:
            if process_message(service, msg, label_id):
                ok += 1
            else:
                fail += 1
        except Exception:
            log.exception("Unhandled error processing %s", msg.get("id"))
            fail += 1

    log.info("Run complete: %d processed, %d failed/skipped", ok, fail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
