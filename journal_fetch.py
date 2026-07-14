#!/usr/bin/env python3
"""Fetch Kindle Scribe journal emails from Gmail, download the PDF, OCR to text.

Nightly flow: handwritten journal shared from Kindle Scribe -> Amazon emails a
download link -> this script (run each morning by launchd) downloads the PDF,
OCRs it, and writes one entries/YYYY-MM-DD.md per handwritten journal date.

Whole-notebook resends are handled by page-level dedup: every rendered page is
hashed into entries/.seen_pages.json and already-seen pages are skipped, so
only new pages are OCR'd regardless of how many pages the share contains.

Usage:
  python journal_fetch.py --auth            # one-time browser OAuth
  python journal_fetch.py                   # normal run
  python journal_fetch.py --backfill X.pdf  # (re)process a local PDF, no Gmail
"""

import argparse
import difflib
import hashlib
import html as html_lib
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timezone, timedelta

import requests

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
INBOX_DIR = os.path.join(REPO_DIR, "inbox")
ENTRIES_DIR = os.path.join(REPO_DIR, "entries")
SEEN_PAGES_PATH = os.path.join(ENTRIES_DIR, ".seen_pages.json")

DEFAULT_QUERY = (
    "from:do-not-reply@amazon.com newer_than:7d -label:journal/processed"
)
BANGKOK = timezone(timedelta(hours=7))
MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]

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
    """Find the Kindle file download link in the share email body."""
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


def render_pages(pdf_path: str) -> list[bytes]:
    """Render each PDF page to PNG bytes."""
    import fitz  # PyMuPDF

    dpi = int(os.environ.get("OCR_DPI", "300"))
    with fitz.open(pdf_path) as doc:
        return [page.get_pixmap(dpi=dpi).tobytes("png") for page in doc]


def load_seen_pages() -> dict:
    if os.path.exists(SEEN_PAGES_PATH):
        with open(SEEN_PAGES_PATH) as f:
            return json.load(f)
    return {}


def save_seen_pages(seen: dict) -> None:
    os.makedirs(ENTRIES_DIR, exist_ok=True)
    with open(SEEN_PAGES_PATH, "w") as f:
        json.dump(seen, f, indent=1, sort_keys=True)


def extract_page_date(page_text: str, email_date: date) -> date | None:
    """Parse the handwritten date heading at the top of a journal page.

    Expects day-first headings like "29 June -Mon" / "2. July - day 4",
    tolerating OCR noise in the month via fuzzy matching. Returns None when
    no plausible date is found (caller treats the page as a continuation).
    """
    for line in page_text.splitlines()[:4]:
        for m in re.finditer(r"(\d{1,2})\s*[.\-~]*\s*([A-Za-z]{2,12})", line):
            day = int(m.group(1))
            if not 1 <= day <= 31:
                continue
            month_word = m.group(2).lower()
            match = difflib.get_close_matches(month_word, MONTHS, n=1, cutoff=0.6)
            if not match:
                continue
            month = MONTHS.index(match[0]) + 1
            try:
                parsed = date(email_date.year, month, day)
            except ValueError:
                continue
            if parsed > email_date:
                parsed = parsed.replace(year=email_date.year - 1)
            # Notebook pages should be recent; a huge gap means the "date"
            # was OCR garbage (e.g. "1 July" misread as "15ly").
            if (email_date - parsed).days > 180:
                continue
            return parsed
    return None


SECTION_HEADINGS = ["Concerns", "Good things"]


def _match_heading(line: str) -> str | None:
    """Fuzzy-match a short line against the journal template's section
    headings, tolerating OCR noise ("Concerus", "Good flings", "Goodthiags")."""
    if len(line.split()) > 3:
        return None
    norm = re.sub(r"[^a-z]", "", line.lower())
    if not 4 <= len(norm) <= 14:
        return None
    for title in SECTION_HEADINGS:
        target = re.sub(r"[^a-z]", "", title.lower())
        if difflib.SequenceMatcher(None, norm, target).ratio() >= 0.55:
            return title
    return None


def format_page_markdown(text: str) -> str:
    """Structure a page's OCR text along the journal template:
    main text, then ## Concerns / ## Good things. Drops "N of M" footers."""
    out = []
    for line in text.splitlines():
        line = line.rstrip()
        if re.fullmatch(r"\d+\s*of\s*\d+", line.strip()):
            continue
        title = _match_heading(line.strip())
        if title:
            out.append(f"\n## {title}\n")
        else:
            out.append(line)
    return "\n".join(out).strip()


def append_day_entry(entry_date: str, msg_id: str, page_no: int,
                     page_hash: str, text: str) -> str:
    """Append one OCR'd page to entries/<date>.md. Returns the entry path."""
    os.makedirs(ENTRIES_DIR, exist_ok=True)
    entry_path = os.path.join(ENTRIES_DIR, f"{entry_date}.md")
    marker = f"<!-- source: {msg_id} page {page_no} sha {page_hash} -->"

    if os.path.exists(entry_path):
        with open(entry_path) as f:
            content = f.read()
        if marker in content:
            return entry_path
        with open(entry_path, "a") as f:
            f.write(f"\n{marker}\n\n{text.strip()}\n")
        return entry_path

    backend_name = os.environ.get("OCR_BACKEND", "macos")
    with open(entry_path, "w") as f:
        f.write(
            f"---\ndate: {entry_date}\nocr_backend: {backend_name}\n---\n"
            f"\n{marker}\n\n{text.strip()}\n"
        )
    return entry_path


def process_pdf(pdf_path: str, msg_id: str, email_date: date) -> int:
    """OCR the new pages of a PDF into per-date entry files.

    Returns the number of new pages processed. Already-seen pages (tracked
    by PNG hash in entries/.seen_pages.json) are skipped, which makes
    whole-notebook resends cheap and duplicate-free.
    """
    from ocr import get_backend

    backend = get_backend()
    pngs = render_pages(pdf_path)
    seen = load_seen_pages()

    new_pages = [
        (i + 1, png, hashlib.sha256(png).hexdigest()[:16])
        for i, png in enumerate(pngs)
        if hashlib.sha256(png).hexdigest()[:16] not in seen
    ]
    log.info(
        "%s: %d page(s), %d already seen, %d new",
        os.path.basename(pdf_path), len(pngs), len(pngs) - len(new_pages),
        len(new_pages),
    )

    current_date = None
    for page_no, png, page_hash in new_pages:
        text = backend.ocr_image(png)
        parsed = extract_page_date(text, email_date)
        text = format_page_markdown(text)
        if parsed:
            current_date = parsed
            log.info("Page %d: handwritten date %s", page_no, parsed)
        elif current_date:
            log.info("Page %d: no date heading — continuing %s", page_no, current_date)
        else:
            current_date = email_date
            log.warning(
                "Page %d: no date heading and no prior page — using email date %s",
                page_no, current_date,
            )
        entry_date = current_date.isoformat()
        entry_path = append_day_entry(entry_date, msg_id, page_no, page_hash, text)
        seen[page_hash] = {"date": entry_date, "msg": msg_id, "page": page_no}
        save_seen_pages(seen)
        log.info("Page %d -> %s", page_no, entry_path)

    return len(new_pages)


def process_message(service, msg: dict, label_id: str, gmail_client) -> bool:
    """Handle one share email end-to-end. True if fully processed."""
    msg_id = msg["id"]
    subject = gmail_client.get_header(msg, "Subject")
    received = datetime.fromtimestamp(
        int(msg["internalDate"]) / 1000, tz=timezone.utc
    ).astimezone(BANGKOK)
    email_date = received.date()
    log.info("Processing %s (%s) — %r", msg_id, email_date, subject)

    url = extract_download_url(gmail_client.get_html_body(msg))
    if not url:
        log.error("No download link found in %s — leaving unprocessed", msg_id)
        return False

    os.makedirs(INBOX_DIR, exist_ok=True)
    pdf_path = os.path.join(INBOX_DIR, f"{email_date}-{msg_id[:8]}.pdf")
    if os.path.exists(pdf_path):
        log.info("PDF already downloaded: %s", pdf_path)
    elif not download_pdf(url, pdf_path):
        return False

    process_pdf(pdf_path, msg_id, email_date)
    gmail_client.mark_processed(service, msg_id, label_id)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--auth", action="store_true",
        help="run interactive browser OAuth flow and exit",
    )
    parser.add_argument(
        "--backfill", metavar="PDF",
        help="(re)process a local PDF without touching Gmail",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    load_env()

    if args.backfill:
        name = os.path.basename(args.backfill)
        m = re.match(r"(\d{4}-\d{2}-\d{2})-(\w+)\.pdf", name)
        email_date = date.fromisoformat(m.group(1)) if m else datetime.now(BANGKOK).date()
        msg_id = m.group(2) if m else os.path.splitext(name)[0]
        n = process_pdf(args.backfill, msg_id, email_date)
        log.info("Backfill complete: %d new page(s)", n)
        return 0

    import gmail_client

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
            if process_message(service, msg, label_id, gmail_client):
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
