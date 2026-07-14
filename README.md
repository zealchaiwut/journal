# journal

Kindle Scribe → Gmail → OCR pipeline. Handwritten journal shared nightly from
the Scribe (Share → email as PDF); every morning launchd runs `journal_fetch.py`,
which finds the Amazon share email in Gmail, downloads the PDF, OCRs the
handwriting with the macOS Vision framework, and writes `entries/YYYY-MM-DD.md`.

The dated entries are the input for the next stage (LLM reflection → morning
brief + to-dos — not built yet).

## One-time setup

1. **Google Cloud** (~15 min):
   - Create a project at https://console.cloud.google.com
   - Enable the **Gmail API**
   - OAuth consent screen → External, add your own gmail as a test user
   - Credentials → Create OAuth client ID → **Desktop app** → download JSON
     as `credentials.json` into this repo (gitignored)

2. **Environment**:

   ```bash
   ~/.local/bin/python3.12 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   cp .env.example .env   # adjust OCR_LANGS if journaling in Thai (th-TH)
   ```

3. **Authorize Gmail** (opens browser once; token auto-refreshes after):

   ```bash
   ./venv/bin/python journal_fetch.py --auth
   ```

4. **Test the flow**: share a page from the Scribe to your gmail, then:

   ```bash
   ./venv/bin/python journal_fetch.py
   ```

   Check `inbox/*.pdf`, `entries/<today>.md`, and that the email got the
   `journal/processed` label. Rerun — should be a no-op.

5. **Schedule** (daily 06:30 via launchd; missed runs fire on wake):

   ```bash
   bash scripts/install_launchd.sh
   ```

## Config (.env)

| Key | Default | Meaning |
|-----|---------|---------|
| `OCR_BACKEND` | `macos` | `macos` (Vision, local/free) or `claude` (not implemented yet) |
| `OCR_LANGS` | `en-US` | Comma-separated Vision recognition languages |
| `OCR_DPI` | `300` | PDF page render resolution before OCR |
| `GMAIL_QUERY` | see `journal_fetch.py` | Override the Gmail search |

## How it stays idempotent

Processed emails are labeled `journal/processed` and excluded from the search
query. Downloaded PDFs are kept in `inbox/` and reused; an entry file that
already contains a message id is not rewritten.

## Troubleshooting

- `No valid Gmail token` → run `journal_fetch.py --auth` again (token revoked
  or consent expired — Google expires tokens for apps left in "Testing" after
  7 days; push the OAuth consent screen to "In production" to stop that).
- `Download did not return a PDF` → Amazon link expired (they last days) or
  the redirect wanted a login. Re-share from the Scribe.
- Poor OCR → try `OCR_DPI=400`, add the right `OCR_LANGS`, or implement the
  Claude vision backend (`ocr/claude_vision.py`) and set `OCR_BACKEND=claude`.
