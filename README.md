# GulfEye

AI-assisted maritime intelligence, surveillance and anti-piracy platform for Nigerian waters and the Gulf of Guinea.
GulfEye fuses AIS tracks, Sentinel-1 radar detections, vessel registration records and incident reports into one picture,
scores vessel behaviour with explainable rules plus an Isolation Forest, raises early-warning alerts, and lets agencies
report incidents and share intelligence under role-based access with a full audit trail.

> **Demo data.** Every AIS track, radar detection, registry record, company and incident in this repository is
> **simulated** and labelled as such in the interface. GulfEye has **no access** to NIMASA, Nigerian Navy or any other
> agency system; those feeds appear as "requires authorised integration". Risk scores and alerts are indicators for
> **human investigation**, never proof of wrongdoing, and never trigger enforcement on their own.

## Quick start

```bash
pip install -r requirements.txt
python -m backend                 # http://127.0.0.1:8765
```

Sign in with one of the demo accounts. The password for all of them is `GulfEye-demo-2026`
(change it with `GULFEYE_DEMO_PASSWORD` **before the first start**, or run `python -m backend --reset` after changing it).

| Email | Agency | Role | Can |
|---|---|---|---|
| admin@gulfeye.demo | GulfEye ops cell | Administrator | everything, incl. users, full audit log, AIS ingest |
| analyst@nimasa.demo | NIMASA | Intelligence analyst | alerts, incidents, engine re-run, exports, sharing |
| ops@navy.demo | Nigerian Navy | Operations officer | alerts, incidents, sharing |
| ops@police.demo | Marine Police | Operations officer | alerts, incidents, sharing |
| liaison@npa.demo | Ports Authority | Liaison (read only) | read shared items, add notes |
| ops@airforce.demo | Nigerian Air Force | Operations officer | maritime patrol sightings, alerts, incidents |
| analyst@customs.demo | Nigeria Customs Service | Intelligence analyst | watchlist, smuggling intelligence, API keys for NCS |
| watch@mmcc.demo | Regional maritime coordination centre (MMCC) | Operations officer | regional incident batches |

Admins can add further agencies in **Agencies → Add an agency**; each new agency can then get users and API keys.

On first start the server creates `var/` (SQLite database, uploads, signing key), loads the simulated registry and a
two-year incident history and the partner agencies' demo submissions, and runs the threat-detection engine.
`python -m backend --reset` starts over (**run it once after upgrading**, so the larger dataset is loaded).

### Demo dataset (all simulated)

| Data | Size |
|---|---|
| AIS vessels (12 h, a report every 20 min) | 285: cargo 124, tanker 64, fishing 53, supply 25, passenger/ferry 10, tug 9 |
| Sentinel-1 radar detections | 149 (dark contacts, platforms, clutter, matched ships) |
| Registry records | 285 |
| Incident history | 486 over two years |
| Agency submissions | 17 demo batches, about 145 records from NN, NAF, NIMASA, NCS, MPOL, NPA and MMCC |
| Geography | Natural Earth coastline and 12 country borders from Liberia to Gabon; 17 ports, 10 platforms, 8 watch zones, 8 shipping lanes |

The 47 original vessels and 50 original radar detections keep their IDs and scenarios (MT Gulf Voyager, MV Sea Harmony,
FV Red Lantern, D-041 …); new scenarios include a rendezvous pair, loitering, a speed sprint, an off-lane tanker and two AIS gaps.
`python3 scripts/build_data.py && python3 scripts/build_intel_seed.py` rebuilds everything (seeded, reproducible).

The original static mode still works without the backend: `python3 scripts/serve.py` serves the dashboard with its seven
original screens (no sign-in, no AI alerts, incidents or sharing).

## Putting it online (for judges and reviewers)

The sign-in screen offers **one-click demo access**: reviewers pick a role (Administrator, NIMASA analyst, Navy officer,
Customs analyst, read-only Ports liaison …) and are in, with no password. Roles, agency visibility and the audit log
still apply, so switching roles shows what each agency can see. It only works for the seeded `*.demo` accounts; set
`GULFEYE_DEMO_LOGIN=0` for real use and only email + password remain.

To get a public `https://…onrender.com` link (free):

1. Put this folder in a GitHub repository (github.com → New repository → "uploading an existing file", drag the folder
   contents in; or use GitHub Desktop).
2. On render.com, sign in with GitHub → **New → Blueprint** → choose the repository. Render reads `render.yaml`, builds
   the `Dockerfile` and starts the server (about 5 minutes the first time).
3. Open the link, check the sign-in screen, and give the link to the judges.

Notes: on the free plan the server sleeps after 15 minutes without visitors and the next visit takes up to a minute to
wake it, so open it yourself shortly before judging. Every restart re-seeds the demo data, so anything a judge adds is
temporary. The same `Dockerfile` runs on Railway, Fly.io, Google Cloud Run or Hugging Face Spaces (set `PORT` as they require).

## What is in the app

| Screen | What it does |
|---|---|
| **Dashboard** | KPIs (vessels monitored, suspicious vessels, high-risk alerts, active incidents, 30-day trend), interactive map, the original assistant, priority AI alerts, recent activity across agencies |
| **Live tracking map** | The whole region on one screen (fills the window; ⛶ for full screen). **Time slider and play/pause** replay the last 12 hours, with trails of 30 min to 12 h, speeds 1×–8×; **follow** a selected vessel; searchable list of all vessels with speed or "AIS off" at the chosen time; type chips filter the map. Radar-based alerts appear only after the radar pass time |
| Map | AIS tracks, **vessels as coloured shapes by type** (cargo arrow, tanker hull, fishing circle with bow mark, supply pentagon, tug hexagon, passenger star) pointing to their heading, dashed when AIS is off, with **risk rings**, country borders, radar alerts, incidents (90 days), shipping lanes, zones, patrol assets. **Search** by name/MMSI/IMO/call sign, filter by type and minimum risk, zoom (wheel, buttons) and pan (drag) |
| Vessel profile (click a vessel) | MMSI, IMO, call sign, flag, type, speed, heading, last position, movement history, destination, registration (owner, flag history, inspections, watchlist), identity checks, **AI risk score with every indicator, its evidence and reason**, investigation steps, linked and nearby incidents, owner network, notes, sharing |
| Sea state, Radar, Last reports, Recommendations, Fleet | Unchanged original screens |
| **Alerts** | New *early-warning alerts* section (AI engine): filter by status/level/behaviour; each alert shows vessel, position, time, risk, behaviours, evidence and actions; acknowledge → investigate → close with an outcome; history, notes, sharing. The original radar alerts and incident log stay below |
| **Incidents** | Register of piracy, sea robbery, suspicious movements and other incidents with filters; report form with type, time, position (known place or coordinates), description, up to three vessels with roles, files, and sharing choice; status flow; evidence download; correlated alerts and incidents |
| **Intelligence** | Flagged vessels with their links (incidents, same owner, rendezvous, dark contacts, nearby incidents), a link chart, incident hotspots, and the status of every **data adapter** |
| **Analytics** | Date presets or custom range; incident trend, types, high-risk locations map, zones, alert statistics by level/status/behaviour, time of day, vessel movement patterns; **CSV/JSON exports** and print-to-PDF |
| **Data intake** | How partner agencies feed their own data in: CSV upload or JSON paste with per-record validation results, CSV templates, submissions history, agency watchlist, sightings / patrol reports / radar contacts, **API keys** for agency systems, and the integration contract |
| **Agencies** | Items shared with and sent by your agency, agencies, role/permission matrix, users (admins manage roles and accounts), **audit log** |
| **About & impact** | Environmental and social objectives, SDG 8, 9, 11, 14 and 16, responsible-use principles |

The existing **Log report** voice/text dialog is unchanged; field reports logged there are now also filed in the shared
incident register. Changes made by any user are pushed to everyone over a **WebSocket** (green "Live" in the top bar).

## Agency data intake (prototype of inter-agency integration)

**Which agencies are integrated?** Every agency on the platform: GulfEye ops cell, NIMASA, Nigerian Navy, Marine Police,
Nigerian Ports Authority, Nigerian Air Force, Nigeria Customs Service and the regional MMCC, plus any agency an admin adds.
Each can send data through CSV upload, the forms, or its own API key; everything shared is validated, merged across
agencies (below) and used by the risk engine. What is **not** there yet is an automatic link into those agencies' own
systems: that needs data-sharing agreements and is phase 2. Records an agency marks `visibility: agency` stay with that agency.

Agencies contribute their own data through one intake (`backend/intake.py`), today by hand or from their systems, and in
phase 2 through direct connectors that call the same intake.

| Data type (`kind`) | What it becomes |
|---|---|
| `sightings`, `patrol_reports` | Observations linked to a vessel (MMSI → IMO → name) or kept as unidentified; a shared report on a vessel in the last 3 days adds the *Reported by an agency* indicator |
| `radar_contacts` | Observations; an unidentified contact within 1 km / 45 min of a vessel (and not the vessel itself by size) adds *Unidentified radar contact alongside* |
| `watchlist` | Watchlist entries; `suspect` +15, `monitor` +6 (*Agency reports* group, counts as independent evidence for Critical) |
| `incidents` | Incidents in the shared register, with vessels and roles |
| `ais_positions` | Appended to the AIS tracks |

Channels: **CSV upload** or **JSON paste** in the Data intake screen (signed-in users with `intake:submit`), or
`POST /api/intake/{kind}` / `POST /api/intake/{kind}/csv` from an agency system with header `X-API-Key`. Keys are created
in the same screen by analysts (own agency) or admins (any agency), limited to chosen data types, shown once, stored as a
hash, and revocable.

Rules: each record is validated separately (time not in the future, position inside the Gulf of Guinea area, IMO check
digit, required fields, no unknown columns); bad records are rejected with a reason while good ones are kept; `ref`
(the agency's own id) blocks duplicates; `visibility: agency` keeps a record to its agency and out of the shared risk
engine; every submission is audited with agency, user or key and counts; scores re-run and changes are pushed live.
Templates: `GET /api/intake/templates/{kind}.csv`. Field list and examples: `GET /api/intake/kinds` and `/api/docs`.

The demo includes simulated submissions: Navy patrol reports and an aircraft sighting, Marine Police watchlist (CSV),
NIMASA coastal-radar trial contacts (one rejected for position), Ports Authority incidents (one rejected for type), and
cases to merge: the same anchorage robbery reported by the Ports Authority and Marine Police, the Bonny fairway skiff reported
by NIMASA and the Navy, and MT Gulf Voyager listed by both Marine Police and the Navy.

## Merging data from different agencies (`backend/merge.py`)

| What | How | Who decides |
|---|---|---|
| **Merged contacts** | Shared agency sightings, patrol reports and radar contacts, Sentinel-1 dark contacts and recent incidents within **5 km and 90 minutes** of each other are grouped into one contact (last 7 days). It lists every report and source, counts **independent sources** (several reports from one agency count once) and combines their confidence (each source's best report; capped at 99%). Shown as a dashed ring with ×N on the map, in Intelligence, and as "Corroborated" on the matching radar alert. | Automatic; the original reports are never changed |
| **Duplicate incidents** | Incidents of the same type (attack types grouped), within **10 km and 6 hours**, are suggested as duplicates with a match score and reasons (distance, time, same vessel, agencies). **Merge** keeps the earlier report as primary and attaches the other; lists, analytics, exports, dashboard counts and the prior-incident indicator count the event once; both reports, agencies and vessels stay visible on the primary. **Undo** restores both. **Not a duplicate** is remembered. | A person with `incidents:update`; every decision is audited |
| **Fair scoring of agreement** | Watchlist entries, agency reports and radar contacts about one vessel give **one** indicator each; extra independent agencies add +3, at most +6, instead of stacking. | Automatic |
| **Consolidated vessel record** | Name, MMSI, IMO, flag, type, length, call sign and destination from AIS, the registry and each agency's records side by side, with **disagreements flagged** (e.g. FV Red Lantern: Benin on AIS, Togo in the registry). Nothing is overwritten. | Analyst confirms with the source |

Only shared records are merged, so restricted agency data never surfaces in another agency's view.
API: `GET /api/fused`, `GET /api/fused/{id}`, `GET /api/incidents/duplicates`, `POST /api/incidents/merge`,
`POST /api/incidents/merge/{id}/undo`, `POST /api/incidents/not-duplicate`, `GET /api/incidents/merges`; the consolidated
record is part of `GET /api/vessels/{mmsi}`.

## How the threat detection works

`backend/ml/` — run by `POST /api/engine/run`, after AIS ingest, after an incident names a vessel, and on first start.

1. **Features (pandas)** per vessel from its AIS track: mean and variability of speed, largest speed change, mean course
   change, share of time stopped, longest AIS silence, area covered (radius of gyration), share of time in piracy zones,
   distance from shipping lanes, reporting rate.
2. **Fusion** with the Sentinel-1 pass (a Python copy of the browser's rules, tested to give identical results): is a
   silent vessel still seen at sea? Is a non-reporting contact alongside it?
3. **Indicators**, each with points, evidence, a plain reason and investigation steps: AIS disruption, high-risk zone,
   loitering, unexpected stop, abnormal speed change, erratic course, unusual route, rendezvous with another vessel,
   close contact with a dark vessel, named in a recent incident, watchlist, identity mismatch with the registry,
   frequent flag changes, owner linked to flagged vessels, and an **Isolation Forest** outlier (scikit-learn, trained on
   the features plus vessel type, explained by the features furthest from the vessel's type peers), and, from agency
   submissions, agency watchlist entries, agency patrol or sighting reports, and unidentified agency radar contacts alongside.
4. **Score 0–100 → Low / Medium (25) / High (50) / Critical (75)** with safeguards:
   - AIS disruption as the only indicator group is capped at **Medium**.
   - The Isolation Forest alone is capped at Medium (40).
   - **Critical** needs indicators from at least two independent groups.
5. Vessels at Medium or above get an **alert**; re-runs update the open alert and log the change instead of duplicating it.

Thresholds come from `web/data/assumptions.json` and the constants in `backend/ml/engine.py`; they are rules of thumb
to be validated with operators.

## Architecture

```
web/                    front end: plain ES modules, no build step (existing stack kept)
  js/main.js            boot, sign-in gate, routing, events          js/ops.js        API calls, forms, map search/zoom, live updates
  js/views.js           original screens (+ hooks for new features)  js/intel-views.js  new screens and drawers
  js/model.js rules.js  original AIS/SAR fusion and response advice  js/map.js        SVG map (offline), lanes, incidents, risk, filters
  js/api.js auth.js     token handling, WebSocket, sign-in screen    css/styles.css   original styles + GulfEye section
backend/                FastAPI
  app.py                routes, auth, RBAC, validation, uploads, exports, WebSocket, static files
  security.py           PBKDF2 passwords, HMAC-signed tokens with revocation, roles and permissions
  db.py                 SQLite schema (MVP)     schema_postgis.sql   same schema for PostgreSQL/PostGIS
  adapters/             one adapter per source: AIS file, SAR file, geography, simulated registry, incident reports,
                        and restricted placeholders (NIMASA, Navy, coastal radar, regional centres, licensed registry)
  ml/                   features.py, fusion.py, engine.py (indicators + Isolation Forest + scoring), correlation.py
  seed.py, seed_data/   demo agencies, users, registry and incident history (simulated)
scripts/                existing data pipeline + build_intel_seed.py (regenerates the simulated registry and incidents)
tests/                  pytest (API + engine) and the JS regression test of the original fusion
docs/PLAN.md            phased plan and acceptance criteria
```

### API (all under `/api`, bearer token except `health`, `status`, `auth/login`)

`auth/login`, `auth/logout`, `me` · `summary` · `vessels`, `vessels/{mmsi}` · `alerts`, `alerts/{id}`,
`alerts/{id}/status` · `engine/run` · `incidents` (GET, POST), `incidents/{id}`, `incidents/{id}/status`,
`incidents/{id}/attachments`, `attachments/{id}` · `comments` · `shares`, `shares/inbox`, `shares/{id}/read` · `intel` ·
`sources` · `ingest/ais` · `analytics` · `export/{incidents.csv|alerts.csv|vessels.csv|report.json}` · `agencies`,
`roles`, `users`, `users/{id}`, `audit` · `interpret` (voice notes, unchanged) · WebSocket `/ws?token=…`.
Interactive docs: `http://127.0.0.1:8765/api/docs`.

### Security measures

PBKDF2-SHA256 password hashes (240k rounds) · signed, expiring tokens with server-side revocation on sign-out ·
account lockout after 5 failures and per-IP login throttling · permission check on every route, logged when denied ·
incident visibility per agency, widened only by explicit shares · Pydantic validation (types, ranges, area of interest,
no future dates, known vessels) · uploads limited to PDF/PNG/JPEG/TXT/CSV, 5 MB, checked by content, stored under random
names outside the web root, downloaded only with a token · CSV formula-injection protection · CSP and other security
headers · data files behind sign-in · audit log of sign-ins, views, changes, shares, downloads and exports.

## Tests

```bash
python -m pytest tests            # 64 tests: auth, RBAC, alerts, incidents, uploads, sharing, analytics, exports, WebSocket,
                                  # engine safeguards, agency intake (keys, scopes, validation, duplicates, visibility, CSV, scoring),
                                  # merging (fused contacts, duplicate incidents, merge/undo/reject, counting once, corroboration, conflicts),
                                  # one-click demo sign-in (demo accounts only, roles kept, audited, can be switched off)
npm run test:js                   # original fusion output and all 7 original screens unchanged; tracking map
                                  # (distinct shapes per type, every vessel drawn, replay moves vessels, radar hidden before the pass)
```

## Using real data

| Source | How |
|---|---|
| AIS | `python3 scripts/fetch_ais.py --start … --end …` (Neptune-AIS / Global Fishing Watch key), then `GULFEYE_AIS_FILE=web/data/ais_live.json python -m backend`; or push positions to `POST /api/ingest/ais` (admin) |
| Sentinel-1 | `python3 scripts/detect_sar.py --scene … --weights …` (YOLOv8 trained on SAR ships), then `GULFEYE_SAR_FILE=web/data/sar_live.json` |
| Registry | replace `registry_sim` with a licensed register adapter (`backend/adapters/registry.py`) |
| Agency data now | issue the agency an API key (Data intake screen) and have its system post to `/api/intake/{kind}`, or upload CSV |
| Agency feeds, phase 2 | after a data-sharing agreement: a connector that pulls from the agency system and calls `ingest_records()` (or posts to the intake with its key); then set its `GULFEYE_*_ENDPOINT` so the source shows as connected |
| Weather | `python3 scripts/build_data.py --refresh-forecast` (Open-Meteo) |

## Configuration

`GULFEYE_VAR` (data folder, default `var/`), `GULFEYE_DB`, `GULFEYE_SECRET` (token key; otherwise generated in `var/`),
`GULFEYE_TOKEN_HOURS` (8), `GULFEYE_DEMO_PASSWORD`, `GULFEYE_DEMO_LOGIN` (1 = one-click demo sign-in, 0 = off), `GULFEYE_TRUST_PROXY` (1 behind a hosting proxy, for correct client IPs in throttling and the audit log), `PORT`, `HOST`, `ANTHROPIC_API_KEY` (optional, voice-note reader).

## Still needed before real use

Phase-2 connectors for NIMASA, Navy, coastal radar and regional centres (agreements, interface specs, scheduled pulls on top of the intake) · a licensed vessel register · real AIS and
Sentinel-1 pipelines running on a schedule (the scripts exist, untested against live data) · migration to PostgreSQL/PostGIS
(`backend/schema_postgis.sql`) · single sign-on with the agencies' identity providers, HTTPS, and a secrets manager ·
validation of thresholds and of the Isolation Forest with operators and labelled historical cases · a data-protection and
legal review of the sharing rules.
