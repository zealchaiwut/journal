#!/usr/bin/env python3
"""Revise one day's raw OCR'd journal entry into clean bullet points.

Smart-server step: reads entries/YYYY-MM-DD.md (raw OCR, appended to across
possibly-multiple pages/emails), asks Claude to fix OCR noise and restructure
it as bullets under the same headings, and atomically writes
entries/cleaned/YYYY-MM-DD.md. The raw file is never modified — this is a
read-only-source, write-a-derived-copy step. Failure never blocks the raw
fetch; the caller (journal_fetch.py) treats this as best-effort.

Usage:
  python journal_clean.py --date 2026-07-14   # clean one day, print + write
  python journal_clean.py --date 2026-07-14 --dry-run   # print only
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
ENTRIES_DIR = os.path.join(REPO_DIR, "entries")
CLEANED_DIR = os.path.join(ENTRIES_DIR, "cleaned")
PROMPT_PATH = os.path.join(REPO_DIR, "prompts", "journal_clean.md")

log = logging.getLogger("journal_clean")


def _cfg(name: str, default: str) -> str:
    return os.environ.get(name, default)


def raw_entry_path(entry_date: str) -> str:
    return os.path.join(ENTRIES_DIR, f"{entry_date}.md")


def strip_frontmatter_and_markers(raw: str) -> str:
    """Drop the YAML frontmatter and `<!-- source: ... -->` page markers —
    noise the cleaning model doesn't need to see."""
    body = re.sub(r"^---\n.*?\n---\n", "", raw, count=1, flags=re.DOTALL)
    body = re.sub(r"<!-- source:.*?-->\n?", "", body)
    return body.strip()


def call_claude(system_prompt: str, user_message: str, model: str) -> str:
    result = subprocess.run(
        ["claude", "-p", "--model", model, "--output-format", "json"],
        input=f"{system_prompt}\n\n{user_message}",
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude -p failed (rc={result.returncode}): "
            f"stderr={result.stderr.strip()[-300:]!r}"
        )
    envelope = json.loads(result.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p returned error: {envelope.get('result')!r}")
    text = envelope.get("result", "").strip()
    return re.sub(r"^\s*```(?:markdown)?\s*|\s*```\s*$", "", text)


def clean_entry(entry_date: str, model: str | None = None) -> str:
    """Read entries/<date>.md, ask Claude to revise it, return the cleaned
    markdown. Raises if the raw entry doesn't exist or Claude fails."""
    model = model or _cfg("MODEL", "sonnet")
    raw_path = raw_entry_path(entry_date)
    if not os.path.exists(raw_path):
        raise FileNotFoundError(f"No raw entry for {entry_date}: {raw_path}")

    with open(raw_path) as f:
        raw = f.read()
    body = strip_frontmatter_and_markers(raw)
    if not body:
        raise ValueError(f"Raw entry for {entry_date} is empty after stripping markers")

    with open(PROMPT_PATH) as f:
        system_prompt = f.read()
    user_message = f"RAW ENTRY ({entry_date}):\n\n{body}"
    return call_claude(system_prompt, user_message, model)


def write_cleaned(entry_date: str, cleaned_text: str) -> str:
    os.makedirs(CLEANED_DIR, exist_ok=True)
    dest = os.path.join(CLEANED_DIR, f"{entry_date}.md")
    payload = f"---\ndate: {entry_date}\nsource: cleaned\n---\n\n{cleaned_text.strip()}\n"

    fd, tmp = tempfile.mkstemp(dir=CLEANED_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="entry date, YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the cleaned markdown, write nothing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    cleaned = clean_entry(args.date)
    if args.dry_run:
        print(cleaned)
        return 0
    dest = write_cleaned(args.date, cleaned)
    log.info("Cleaned entry written: %s", dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
