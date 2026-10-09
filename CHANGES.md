# UI polish pass — Action Code Dashboard

## What I found on audit
The app already had a solid, modern design system in place: CSS custom-property
tokens, consistent card/badge/button/table components, a real responsive
breakpoint strategy, and reduced-motion support. So this wasn't a redesign —
it was a consistency and polish pass. Two real problems stood out:

1. **Inline `style=""` attributes** scattered across 7 templates (flex
   layout, spacing, colors, display toggles) — the same visual decisions
   re-implemented ad hoc instead of reusing the design system.
2. **Colored emoji used as icons** (⏹ ✅ ❌ ⬇ 📄 🗑 ⚙ ▶) mixed into an
   otherwise clean, monochrome stroke-SVG icon system. Emoji render
   differently per OS/browser and clash with a "professional SaaS" look.

Everything else — layout, routes, business logic, data, DB schema — is
untouched.

## What changed

**`templates/_icons.html`** (new) — a shared Jinja macro, `icon(name, size)`,
producing the same stroke-based SVG style already used in the sidebar nav.
Replaces every emoji glyph across the app: `play`, `stop`, `check-circle`,
`x-circle`, `download`, `file-text`, `trash`, `chevron-left/right`.

**`static/style.css`** — added:
- Utility classes (`.actions-row`, `.inline-form`, `.subhead`,
  `.text-success/-danger/-warning`, `.icon-link`, `.grid-full`, flex/gap/margin
  helpers) so templates stop reaching for `style=""`.
- A skip-link for keyboard users.
- Password-visibility toggle styling (`.password-wrap`, `.pw-toggle`).

**`static/app.js`** — added a small enhancement: any `input[type=password]`
on the page (SMTP password in Settings, new-user password, login password)
now gets an eye-icon toggle to show/hide what was typed. Pure client-side,
doesn't change how anything posts to the server.

**Templates** — `history.html`, `logs.html`, `run.html`, `run_detail.html`,
`settings.html`, `login.html`, `schedule.html`, `view_log.html`, `base.html`:
removed inline styles in favor of the new utility classes, swapped emoji for
the icon macro, and added `role="status" aria-live="polite"` to flash
messages for screen readers.

## Verified
- All templates pass Jinja syntax compilation.
- Rendered every page (`/dashboard`, `/run`, `/history`, `/logs`,
  `/schedule`, `/settings`, `/login`) via Flask's test client — all 200.
- Seeded runs in every status (`running`, `success`, `failed`, `cancelled`)
  and rendered `/run/<id>` for each — all 200, including the live-status
  polling panel.
- Rendered `/logs` and a real `/logs/<file>` view — 200.
- Grepped final output HTML for leftover emoji — none found (only plain
  monochrome `→` typographic arrows remain, which are fine).

## How to apply
Drop `templates/` and `static/` from this package over your existing copies
— nothing else in the app needs to change.

---

# Instant Run: manual source-file upload

## What changed
Added a second "source inputs" option to the Run page. Previously a run
always read the six GAINS/SAP exports from the shared `inputs/` folder.
Now you can instead upload all six files just for that one run.

- **`templates/run.html`** — new "Source inputs" chip-radio (folder / upload,
  styled like the existing purchasing-code chips); selecting "upload" reveals
  the six file fields. Form is now `multipart/form-data`. Recent Runs table
  gets a "Source" column.
- **`static/app.js`** — shows/hides and (un)requires the file fields based on
  the selected radio.
- **`static/style.css`** — `.upload-body` panel + `input[type=file]` styling
  matching the existing form design.
- **`routes/run_routes.py`** — validates all six files are present and
  `.xlsx` (all-or-nothing), stages them under a unique per-run folder using
  the exact filenames `action_code_pipeline.py` expects, and passes that
  folder in as the run's `input_dir`.
- **`services/pipeline_runner.py`** — threads `input_dir` through
  `start_run` → `_execute_run` → `pipeline.run_pipeline`; deletes the staged
  folder once the run finishes (success, failure, or stop) so uploads never
  linger on disk.
- **`database/db.py`** — new `source_mode` column (`folder` / `upload`) on
  `runs`, added to the base schema and as an additive migration for existing
  databases.
- **`templates/run_detail.html`** — shows which source a run used.

## Verified
Ran the app end-to-end (not just template/syntax checks):
- Folder-mode run: correctly read from the shared `inputs/` folder,
  `source_mode` recorded as `folder`.
- Upload with one of six files missing: rejected with a clear flash message,
  no run created.
- Upload with all six files: got past the GAINS-loading step using the
  *uploaded* file specifically (the shared `inputs/` folder was empty at the
  time, so this proves the upload folder was actually used), `source_mode`
  recorded as `upload`, and the staged upload folder was deleted once the
  run finished.

## How to apply
This pass touches more than templates/static — it also changes
`routes/run_routes.py`, `services/pipeline_runner.py`, `database/db.py`, and
`config.py` (new `SOURCE_FILE_LABELS` / `MANUAL_UPLOAD_DIR`). Replace those
files (and `templates/run.html`, `templates/run_detail.html`, `static/app.js`,
`static/style.css`) with the versions in this package. The DB migration is
additive and runs automatically on next app start — no manual schema change
needed.
