# Implementation notes — journal → Hermes morning brief

## Repo map (confirmed)

| What | Where |
|------|-------|
| Entries directory | `entries/YYYY-MM-DD.md` (frontmatter `date`, `ocr_backend`; sections `## Concerns`, `## Good things`) |
| Email fetch | `gmail_client.py` — **Gmail API + OAuth** (`credentials.json` + `token.json`, gitignored). NOT an Outlook rule — the handoff brief was wrong; Kindle Scribe emails the dedicated gmail (zeal.aijournal@) directly, Amazon share link → PDF download |
| OCR | `ocr/` — macOS Vision (`ocr/macos_vision.py`), pluggable via `OCR_BACKEND` (`claude` backend is an unimplemented stub) |
| Existing entry point | `journal_fetch.py` (fetch → download → OCR → dated entries; page-hash dedup in `entries/.seen_pages.json`; `--backfill <pdf>`) |
| NEW brief step | `generate_brief.py` (`--dry-run`, `--no-fetch`), prompt in `prompts/journal_reflection.md`, validator in `brief_schema.py` |
| Contract | `docs/hermes_journal_brief.contract.md` (authored here — no pre-existing file was supplied) |
| Language / deps | Python 3.11 (mini) / 3.12 (laptop), venv per clone; stdlib-only additions (no new pip deps for the brief step) |
| Config | `.env` at repo root, parsed by `load_env()` (KEY=VALUE, `os.environ.setdefault`) — see `.env.example` |
| Tests | `tests/` — `python -m unittest discover tests` |

## Hermes reconciliation

Hermes = `~/dev/hermes-agent-fork` on the mini (`~/dev/hermes` is an empty stub dir).
Native todo schema (`tools/todo_tool.py`): `{id, content, status}`,
status enum `pending|in_progress|completed|cancelled`, **list order = priority**
(no priority field). Therefore the brief emits `content` + `status:"pending"`;
`priority` stays as advisory metadata. The LLM prompt (kept verbatim from the
handoff) says `text`/`"open"` — `normalize_brief()` maps both to the Hermes
names, so either model behavior validates.

TODO (human decision): Commander REST todos use `{id, text, done, position}` —
if the brief should merge there instead of the agent's todo_tool, Hermes needs
a one-line translation; the contract stays as-is.

## Operational notes (mac mini = host)

- **Timezone**: system TZ verified `+07` (Asia/Bangkok) — cron hour needs no adjustment.
- **`claude` binary**: `/Users/zeal-server/.local/bin/claude` — NOT on cron's
  minimal PATH; `bin/journal-morning-run.sh` prepends `~/.local/bin`. Verify
  non-interactive auth once: `~/.local/bin/claude -p --model sonnet "ping"`
  from a non-login shell (`ssh zeal-server@mini '…'`).
- **No `flock` on macOS** — the wrapper uses an atomic `mkdir` lock
  (`.morning-run.lock/`, removed on exit via trap).
- **Wake**: cron does not wake a sleeping mac. Either keep the mini awake or
  `sudo pmset repeat wakeorpoweron MTWRFSU 05:40:00`. A launchd variant that
  catches up after wake is in `deploy/com.chaiwut.journal-morning.plist`.
- **Cron entry** (05:45 ICT, before the ~06:00 brief; hour is a constant to move):

  ```cron
  # Journal → Hermes brief, 05:45 ICT daily (before the morning brief)
  45 5 * * * /Users/zeal-server/dev/journal/bin/journal-morning-run.sh >> /Users/zeal-server/dev/journal/logs/morning.log 2>&1
  ```

- **Failure semantics**: fetch is best-effort (never blocks the brief); the
  claude call gets one retry; a second failure exits non-zero and leaves
  `journal_brief.latest.json` untouched (Hermes serves yesterday's brief).
- **Two-writer warning**: the mini is the writer of `entries/` and
  `outputs/`. Don't run the fetch on the laptop clone without git-syncing
  `entries/.seen_pages.json` first.
- `outputs/` and `logs/` are gitignored; entries stay committed.
