# Action Code Dashboard (Lawson AC12 Automation)

Web UI around `action_code_pipeline.py`: run the AC12 Master File pipeline for
selected purchasing codes, watch it run in the background, **schedule automatic
runs**, stop runs mid-flight, and get stats over time — all behind a login.

## Features

- **Portal choice** — after login, confirm **Action Code** or **ME2M** in a
  popup. Each portal has its own Dashboard, Schedule, New run and Run files.
  Use **Switch portal** to change workspaces without signing out.
- **ME2M Processing** — a separate portal for uploading an ME2M `.xlsx`
  export, selecting its reporting date, watching progress, stopping a run and
  downloading the processed workbook. Always processes the first worksheet,
  regardless of its name; required columns are validated. Outputs contain
  `Main File` with Period after Delivery Date and `Pivot Summary` with Net Order
  Value by purchasing group and Period, including blank/unclassified rows.
  Numeric text is converted to real Excel numbers before building the summary.
- **Dashboard statistics and charts** — both portals show summary totals,
  interactive processing charts, run outcomes and clickable run files. Select
  a chart metric to see rows, action items/order value or run counts. ME2M's
  Period and purchasing-group breakdowns appear only inside a run's details.
  Reruns are counted separately. ME2M never invokes the Action Code pipeline
  or its email notifications.
- **Login & users** — session-based auth, hashed passwords, admin/buyer roles.
  Default admin account is created on first launch (`admin` / `ACR@Adm1n#2024!`) and
  can be changed from **Settings → Users**.
- **Run pipeline** — choose a run type (Daily / Weekly / Monthly, compact
  segmented picker) and one or more purchasing codes from:
  `D07 D09 D13 D17 D25 D20 D40 D16 D30 S18 S02 S04`
  Runs execute in the background with a **live status page that shows the
  current pipeline step** (Step 1-2 loading GAINS … Step 7 writing Excel);
  the Dashboard and Run pages show the live step next to running runs too.
  **Source inputs** for Instant Run can come from the shared `inputs/` folder
  (default) or from files you upload just for that run — see Inputs below.
- **Stop option** — running runs can be stopped from the Run page, History, or
  the run detail page; the pipeline halts at the next step and the run is
  marked **stopped**.
- **Schedule** — each portal keeps separate schedules with a time (HH:MM)
  and weekdays. Times use Asia/Calcutta. Keep the app running. Once
  scheduled, the run fires automatically (checked every ~20s) and the result
  appears in History/Dashboard like any other run. Missed runs are caught up
  on next start.
- **Dashboard with filters** — filter by date range (7/14/30 days / all),
  purchasing code/group and status; summary totals, charts and run files react.
- **History & files** — filterable run table, weekly stats by ISO week, and
  monthly Excel output files with download links.
- **Logs by date** — every run writes its own log under `logs/YYYY-MM-DD/`,
  browsable in the UI, with a **Clear logs** option (admin-only, keeps logs of
  runs still in progress).
- **Mail notifications** — SMTP settings editable in the UI (host, TLS, auth,
  recipients, notify-on success/failure, test email).

## Setup & run

```bash
pip install -r requirements.txt
python app.py          # or double-click run.bat
```

Open http://127.0.0.1:5000 and sign in with the default admin account.
Environment overrides: `ACR_HOST`, `ACR_PORT`, `FLASK_DEBUG=1`.

## Inputs

For ME2M, choose the **ME2M** portal and open **New run**, upload one `.xlsx` workbook (up to 50 MB),
select the reporting date and click **Process ME2M workbook**. The ordered
rules from the supplied ME2M script are: `pjw` → With NCR; `Product in McCook`
→ Product in McCook; ABC Indicator X → Discontinued; Delivery Date strictly
before reporting date minus 7 days → Past Dues; otherwise Period stays blank.
The reporting date defaults to today's date in Asia/Calcutta. Use `2026-10-08`
to reproduce the supplied script's cutoff. Every report stores its chosen date.

The first input worksheet is the source, including when it has a custom name
or there are other worksheets named `Main File` or `Pivot Summary`. If the
first sheet is missing required columns, the run reports that error instead
of choosing another sheet. The output places processed data in `Main File`
(sheet 1) and the new pivot summary in `Pivot Summary` (sheet 2).

Conversion changes numeric cell values, rather than only their display format.
Numeric-looking text across the first sheet is converted, including leading
apostrophes, padded numbers, thousands separators and accounting negatives.
Other text and alphanumeric codes stay as text; dates keep their date format.
Material and Purchasing Document numbers display without comma separators.

For automatic ME2M runs, open **Schedule** inside the ME2M portal. Enter an
existing input folder using its full path, a run time and optional weekdays.
The default folder is `me2m/inputs/`. Every run reads the most recently modified
source `.xlsx` in that folder (excluding `~$` temporary files and filenames
containing `_Processed`) and uses that day's reporting date. It stages a copy
so the source workbook remains intact. The schedule owner's current purchasing
group allocation applies. ME2M schedules are stored in `me2m_schedules`.
Unreadable or missing inputs appear as errors on the Schedule page; failures
are attempted once per day. Pausing and re-enabling allows another attempt.

ME2M records are stored separately in `me2m_runs`. Its files live in
`me2m/outputs/` and logs in `me2m/logs/`; staged uploads are deleted after each
run. Admins see all ME2M reports; buyers see their own reports, and allocated
buyers' exports are limited to their purchasing groups. Existing ACR records
are retained by the additive database initialization on the next app launch.

The separate existing ACR management-report `build_report.py` remains available.
The new `me2m_pipeline.py` implements the supplied ME2M report rules.

Place the GAINS/SAP Excel exports in `inputs/`:

| File | Source |
|------|--------|
| `AC12_hot_raw.xlsx` | GAINS EAC12hots query export (contains Purchasing Group column) |
| `Date_of_Supply.xlsx` | Monthly DOS reference file |
| `Total_Demant.xlsx` | GAINS total-demand pivot export |
| `MB52-Fert_oh.xlsx` | SAP MB52 FERT on-hand |
| `MB52-RO_oh.xlsx` | SAP MB52 raw on-hand |
| `ZEXP.xlsx` | SAP ZEXP current on-order |

Alternatively, on the **Run** page, choose **Source inputs → "Upload files for
this run"** to supply all six files through the browser for a single Instant
Run instead of pre-placing them in `inputs/`. All six are required together;
they're staged in a per-run temp folder and deleted automatically once that
run finishes.

## Layout

```
acr_dashboard/
├── app.py                    entry point (app factory, route registry,
│                             startup init, scheduler kick-off)
├── config.py                 paths, purchasing codes, secret key, statuses
├── action_code_pipeline.py   the Excel pipeline (takes purchasing groups,
│                             returns stats, supports cancel between steps)
├── routes/                   all HTTP views, one module per page group
│   ├── auth_routes.py        login / logout
│   ├── dashboard_routes.py   dashboard + filters
│   ├── run_routes.py         run control (start, detail, stop, status)
│   ├── history_routes.py     weekly stats + monthly run files
│   ├── logs_routes.py        per-date log browsing
│   ├── schedule_routes.py    automatic scheduled runs
│   ├── settings_routes.py    mail config + users (admin)
│   └── files_routes.py       output file downloads
├── services/                 background + side-effect logic
│   ├── pipeline_runner.py    background run execution + per-run logging
│   ├── scheduler.py          scheduled runs (HH:MM + weekdays)
│   └── mailer.py             SMTP notifications
├── database/                 persistence layer
│   └── db.py                 SQLite (users, runs, schedules, settings)
├── utils/                    shared helpers
│   ├── auth.py               login + role checks (decorators, users)
│   └── dates.py              ISO-week helper
├── templates/  static/       UI
├── run.bat                   Windows launcher
├── inputs/                   GAINS/SAP Excel exports
├── data/                     SQLite DB + secret key (runtime)
├── logs/YYYY-MM-DD/          per-date run logs (runtime)
└── outputs/                  timestamped Master File exports (runtime)
```

## Notes

- Output files are timestamped, so nothing is ever overwritten.
- Run statuses: `success`, `failed`, `running`, `cancelled` (user stop),
  `interrupted` (app restarted mid-run).
- For a production deployment behind the corporate network, wrap `app.py` in
  `waitress` (Windows) or `gunicorn` (Linux).
