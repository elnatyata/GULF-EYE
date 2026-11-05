# GulfEye upgrade: plan, phases and acceptance criteria

## 1. What the codebase already had (inspected before any change)

| Area | Where | What it does |
|---|---|---|
| Data pipeline | `scripts/build_data.py`, `fetch_ais.py`, `detect_sar.py` | Builds simulated AIS tracks (54 vessels, 12 h, 20-min steps), a simulated Sentinel-1 pass (50 detections) and a sea-state forecast. Real-data scripts for Neptune-AIS and YOLOv8 exist but are untested. |
| AIS/SAR fusion | `web/js/model.js` | Matches radar detections to AIS positions, removes platforms, finds AIS-gap vessels by dead reckoning, flags dark vessels, ship-to-ship transfers and skiff clusters. |
| Response advice | `web/js/rules.js` | Rule-based: which alert first, which patrol asset, fuel, sea-state windows, zone baselines. |
| Screens | `web/js/views.js`, `map.js`, `charts.js` | Sea map (SVG), Sea state, Alerts & log, Radar, Last reports, Recommendations, Fleet, detail drawer. |
| Voice/text logging | `capture.js`, `parser.js`, `scripts/serve.py` | Dispatch, field report, close alert, refuel; rule parser offline, optional language model on the server. |
| Server | `scripts/serve.py` | Static files + `/api/status`, `/api/interpret`. No login, no database. |

**Stack decision.** The front end stays plain ES modules with no build step (the brief says keep the stack where possible). The
server moves to **FastAPI** (Python) with **SQLite** for the MVP; `backend/schema_postgis.sql` holds the PostgreSQL/PostGIS
schema for production. The SVG map is kept (works offline, already themed) and extended rather than replaced by Leaflet.

## 2. Gaps against the brief

No authentication or roles · no database or audit log · no vessel-behaviour ML (only AIS/SAR fusion) · no vessel registry or
IMO numbers · no incident reporting with files · no alert acknowledgement or history beyond the browser · no analytics over
time or exports · no inter-agency sharing · no data-adapter layer · no WebSocket updates · no search/filter on the map ·
no Impact/SDG section.

## 3. Phases

| Phase | Scope | Exit check |
|---|---|---|
| **P1 Foundation** | FastAPI app, SQLite schema, users/agencies/roles, signed tokens, password hashing, audit log, validation, error handling | Auth + RBAC tests pass |
| **P2 Data integration** | Adapter framework: AIS file, SAR file, simulated registry, internal incidents; disabled placeholders for NIMASA / Navy / radar feeds | `/api/sources` lists every adapter with status; restricted ones say "not configured" |
| **P3 AI engine** | Pandas feature extraction per vessel, Isolation Forest, rule indicators, explainable risk (Low → Critical), alert generation, correlation | Engine tests: gap-only vessel never above Medium; every score has evidence |
| **P4 API + realtime** | Vessels, alerts (ack/assign/close + history), incidents (+ uploads), analytics + CSV/JSON exports, sharing, audit, WebSocket | API tests pass |
| **P5 Front end** | Login, dashboard KPIs, map search/filter, risk colours, incidents and lanes layers, vessel profile, new views (Incidents, Intelligence, Analytics, Agencies, Impact) | Every nav tile and button works in the browser; no console errors |
| **P6 Hardening** | Regression test of the original fusion, README, run script | Baseline fusion output unchanged |

## 4. Acceptance criteria

**Authentication and access**
- A user cannot read any `/api/*` data route without a valid token (401).
- Wrong password → 401, and the attempt is written to the audit log. Five failures lock the account for 5 minutes.
- `viewer` cannot acknowledge alerts or create incidents (403). `analyst` and `operator` can. Only `admin` can read the full audit log and manage users.
- Incidents marked `agency` sharing are visible only to the reporting agency and admins.

**Threat detection and risk**
- Every vessel in the AIS baseline gets a score 0–100, a level (Low/Medium/High/Critical), a list of indicators with evidence, and investigation steps.
- A vessel whose only indicator is an AIS gap is never rated above **Medium**.
- Critical requires at least two independent indicator groups.
- Isolation Forest output is one indicator among several; it cannot raise a vessel above Medium on its own.
- Every alert text says it is an indicator for human investigation, not proof.

**Alerts**
- Alerts carry vessel id, position, date/time, risk, behaviour, evidence and recommended actions.
- Status flow `new → acknowledged → investigating → closed (confirmed | false_alarm)`; each change records user, time and note in the history.
- Alert list can be filtered by status, level and behaviour.
- New alerts and status changes are pushed over the WebSocket to all connected users.

**Incidents**
- Create with type, position (inside the Gulf of Guinea box), time (not in the future), description, vessels, files.
- Files: PDF/JPEG/PNG/TXT/CSV only, ≤ 5 MB each, stored with a random name; downloads need a token.
- Status flow `reported → verified → under_investigation → closed`.

**Analytics and reporting**
- Incident trend, alerts by level/status, high-risk locations, vessel types and speeds; date filters change every chart.
- CSV export for incidents, alerts and vessel risk; JSON summary report.

**Preserved behaviour**
- The original fusion produces the same alerts as before (`tests/js/baseline.expected.json`).
- All seven original screens render; the voice/text log dialog still works; the app still loads with the static `serve.py` (without the new features).

**Labelling**
- Every simulated data set shows a "Simulated" badge; nothing is presented as live intelligence.
- Restricted agency sources are shown as "requires authorised integration", never as connected.

## 5. Agency data integration: prototype now, connectors later

| Phase | Scope | Exit check |
|---|---|---|
| **P7 Intake prototype (done)** | One intake for sightings, patrol reports, radar contacts, watchlist, incident batches and AIS positions; CSV and JSON; per-agency API keys with scopes; per-record validation; duplicate refs; visibility; audit; engine indicators; Data intake screen and map layer | 13 intake tests pass; a CSV and an API-key submission show up on the map, in profiles and in scores |
| **P8 Agency onboarding** | Data-sharing agreements, field mapping per agency, key handover to each agency's IT, a test week with real (non-sensitive) records | Each agency submits at least one batch without rejections |
| **P9 Direct connectors** | Scheduled pull or push from NIMASA / Navy / coastal radar systems written as adapters that call `ingest_records()`; mutual TLS or OAuth client credentials instead of static keys; retry and dead-letter queue | Feed latency and completeness agreed per source; source shows "connected" |

**Intake acceptance criteria**
- No submission without a user token or a valid, unrevoked API key; a key only sends the data types it was created for.
- Keys are shown once and stored hashed; operators cannot create keys; analysts only for their own agency.
- Bad records are rejected individually with a reason; good records in the same batch are kept; duplicate `ref` rejected.
- Positions outside the area, future times, invalid IMO check digits and unknown CSV columns are rejected.
- `visibility: agency` records are hidden from other agencies and never used by the shared risk engine.
- A radar contact the same size as the vessel it sits on is not counted as a second craft.
- Every submission is in the audit log with agency, user or key, and counts.

## 6. Merging data from different agencies (done)

**Acceptance criteria**
- Reports from different agencies and Sentinel-1 within 5 km / 90 min form one merged contact listing every report; the originals are unchanged.
- Independent sources are counted per agency or sensor; two reports from one agency count once. Combined confidence is above the best single source when sources agree, and never shown as 100%.
- Restricted records are never part of a merged contact.
- Likely duplicate incidents (same type family, ≤ 10 km, ≤ 6 h) are suggested with reasons; the 150-incident history yields no false suggestions.
- Only users with `incidents:update` can merge, undo or mark "not a duplicate"; each decision is audited.
- After a merge, lists, analytics, exports, dashboard counts and the prior-incident indicator count the event once; the primary shows all reports, agencies and vessels; undo restores both; chains and re-merging a merged report are refused; rejected pairs are not suggested again.
- Several agencies agreeing about a vessel give one indicator with a capped bonus (+3 each, max +6), not stacked points.
- The vessel profile shows each source's values side by side and flags disagreements without overwriting.

**Later:** tunable thresholds per area and contact type; track-level fusion of radar plots once live coastal radar is connected (phase 2); a review queue that assigns duplicate suggestions to analysts.


## 7. Larger dataset and live tracking map (done)

**Scope:** 285 simulated vessels (was 47) across the whole Gulf of Guinea from Liberia to Gabon, 149 radar detections,
486 historical incidents, agency submissions from eight agencies (Nigerian Air Force, Customs and MMCC added), Natural
Earth coastline and borders; a full-window tracking map with 12-hour replay, trails, follow mode and a vessel list;
a distinct coloured shape for each vessel type on every map.

**Acceptance criteria**
- The original 47 vessels and 50 detections keep their IDs and scenarios; the browser fusion output on the original data is unchanged; Python and browser fusion agree on both datasets.
- No vessel, port, lane or platform lies on land.
- The tracking map shows the whole region by default and fills the window; zoom, pan and full screen work; symbols keep their size when zoomed.
- Playing or dragging the slider moves vessels along their tracks; trails stop at the chosen time; radar-based alerts appear only after the radar pass.
- Each vessel type has its own shape and colour, explained in the legend; AIS-off vessels are dashed; risk rings stay.
- Works in static mode (no backend) and at phone width without horizontal scrolling.
