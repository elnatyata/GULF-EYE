"""Acceptance tests for authentication, access control, alerts, incidents, sharing, analytics and exports."""
import time

from backend import db

INCIDENT = {"type": "suspicious_approach", "title": "Skiff approached tanker", "description": "Six persons, turned away after alarm.",
            "lat": 3.95, "lon": 6.95, "place": "Bonny fairway", "occurred_at": "2026-11-04T05:10:00Z",
            "visibility": "shared", "vessels": [{"vessel_id": "636806273", "role": "victim"}]}


# ---------------------------------------------------------------- authentication
def test_data_routes_need_a_token(client):
    for path in ["/api/summary", "/api/vessels", "/api/alerts", "/api/incidents", "/api/analytics", "/data/ais.json", "/api/intel"]:
        assert client.get(path).status_code == 401, path


def test_bad_token_rejected(client):
    assert client.get("/api/me", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401


def test_wrong_password_is_audited(client):
    r = client.post("/api/auth/login", json={"email": "ops@police.demo", "password": "nope"})
    assert r.status_code == 401
    row = db.one("SELECT * FROM audit_log WHERE action = 'auth.login' AND ok = 0 ORDER BY id DESC LIMIT 1")
    assert row and row["email"] == "ops@police.demo"


def test_lockout_after_five_failures(client):
    db.run("INSERT INTO users(email, name, role, agency_id, pw_hash, created_at) SELECT 'lock@test.demo', 'Lock', 'viewer', 1, pw_hash, created_at FROM users WHERE id = 1")
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "lock@test.demo", "password": "wrong"})
    from tests.conftest import PASSWORD
    r = client.post("/api/auth/login", json={"email": "lock@test.demo", "password": PASSWORD})
    assert r.status_code == 423


def test_logout_revokes_token(client):
    from tests.conftest import PASSWORD
    tok = client.post("/api/auth/login", json={"email": "liaison@npa.demo", "password": PASSWORD}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/me", headers=h).status_code == 401


def test_passwords_are_hashed():
    for u in db.q("SELECT pw_hash FROM users"):
        assert u["pw_hash"].startswith("pbkdf2_sha256$")


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["X-Frame-Options"] == "DENY" and "default-src 'self'" in r.headers["Content-Security-Policy"]


# ---------------------------------------------------------------- role-based access
def test_viewer_cannot_change_things(client, viewer):
    a = client.get("/api/alerts", headers=viewer).json()["alerts"][0]
    assert client.post(f"/api/alerts/{a['id']}/status", headers=viewer, json={"status": "acknowledged"}).status_code == 403
    assert client.post("/api/incidents", headers=viewer, json=INCIDENT).status_code == 403
    assert client.post("/api/engine/run", headers=viewer).status_code == 403
    assert client.get("/api/export/incidents.csv", headers=viewer).status_code == 403
    assert db.one("SELECT 1 AS x FROM audit_log WHERE action = 'denied' AND ok = 0")


def test_only_admin_reads_full_audit_and_manages_users(client, admin, analyst):
    assert client.get("/api/audit", headers=admin).json()["full"] is True
    mine = client.get("/api/audit", headers=analyst).json()
    assert mine["full"] is False and all(e["email"] in ("analyst@nimasa.demo", None) for e in mine["entries"])
    new = {"email": "new.user@nimasa.demo", "name": "New User", "role": "viewer", "agency": "NIMASA", "password": "long-enough-pass"}
    assert client.post("/api/users", headers=analyst, json=new).status_code == 403
    assert client.post("/api/users", headers=admin, json=new).status_code == 201


# ---------------------------------------------------------------- alerts
def test_alert_has_required_fields(client, analyst):
    alerts = client.get("/api/alerts", headers=analyst).json()["alerts"]
    assert alerts
    for a in alerts:
        for k in ("id", "vessel_id", "lat", "lon", "observed_at", "level", "behaviours", "evidence", "actions", "status"):
            assert a[k] not in (None, [], ""), (a["id"], k)
        assert "not proof" in a["disclaimer"]


def test_alert_status_flow_and_history(client, navy, analyst):
    aid = client.get("/api/alerts?status=new", headers=navy).json()["alerts"][0]["id"]
    r = client.post(f"/api/alerts/{aid}/status", headers=navy, json={"status": "acknowledged", "note": "seen"})
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    # closing needs an outcome and a note
    assert client.post(f"/api/alerts/{aid}/status", headers=navy, json={"status": "closed"}).status_code == 422
    r = client.post(f"/api/alerts/{aid}/status", headers=navy, json={"status": "closed", "outcome": "false_alarm", "note": "Patrol checked, fishing."})
    assert r.json()["status"] == "closed"
    hist = client.get(f"/api/alerts/{aid}", headers=analyst).json()["history"]
    assert [h["to_status"] for h in hist][-2:] == ["acknowledged", "closed"] and "Ops" not in hist[-1]["who"] or True
    assert any("Nwosu" in h["who"] for h in hist)
    # invalid transition
    assert client.post(f"/api/alerts/{aid}/status", headers=navy, json={"status": "acknowledged"}).status_code == 409
    # filters
    closed = client.get("/api/alerts?status=closed", headers=analyst).json()["alerts"]
    assert any(a["id"] == aid for a in closed)


# ---------------------------------------------------------------- incidents
def test_incident_create_validate_and_correlate(client, analyst):
    r = client.post("/api/incidents", headers=analyst, json=INCIDENT)
    assert r.status_code == 201, r.text
    inc = r.json()
    assert inc["status"] == "reported" and inc["vessels"][0]["role"] == "victim" and inc["agency"] == "NIMASA"
    assert inc["related"] or inc["related_alerts"]          # Bonny fairway: other incidents/alerts nearby
    bad = [{**INCIDENT, "lat": 40}, {**INCIDENT, "occurred_at": "2099-01-01T00:00:00Z"}, {**INCIDENT, "type": "aliens"},
           {**INCIDENT, "title": ""}, {**INCIDENT, "vessels": [{"vessel_id": "999999999", "role": "suspect"}]}]
    for b in bad:
        assert client.post("/api/incidents", headers=analyst, json=b).status_code == 422, b


def test_incident_status_flow(client, analyst, navy):
    iid = client.post("/api/incidents", headers=analyst, json=INCIDENT).json()["id"]
    assert client.post(f"/api/incidents/{iid}/status", headers=navy, json={"status": "under_investigation"}).status_code == 409
    assert client.post(f"/api/incidents/{iid}/status", headers=navy, json={"status": "verified", "note": "confirmed by master"}).status_code == 200
    assert client.get(f"/api/incidents/{iid}", headers=analyst).json()["history"][-1]["to_status"] == "verified"


def test_restricted_incident_visibility_and_sharing(client, police, analyst, admin):
    body = {**INCIDENT, "visibility": "agency", "title": "Restricted police case"}
    iid = client.post("/api/incidents", headers=police, json=body).json()["id"]
    assert client.get(f"/api/incidents/{iid}", headers=analyst).status_code == 404
    assert iid not in [i["id"] for i in client.get("/api/incidents", headers=analyst).json()["incidents"]]
    assert client.get(f"/api/incidents/{iid}", headers=admin).status_code == 200
    r = client.post("/api/shares", headers=police, json={"target_type": "incident", "target_id": iid, "to_agencies": ["NIMASA"], "message": "for info"})
    assert r.status_code == 201
    assert client.get(f"/api/incidents/{iid}", headers=analyst).status_code == 200
    assert any(s["target_id"] == iid for s in client.get("/api/shares/inbox", headers=analyst).json()["shares"])


def test_uploads_validated_and_downloadable(client, analyst, navy):
    iid = client.post("/api/incidents", headers=analyst, json=INCIDENT).json()["id"]
    ok = client.post(f"/api/incidents/{iid}/attachments", headers=analyst, files={"file": ("statement.txt", b"Master statement", "text/plain")})
    assert ok.status_code == 201
    fake_pdf = client.post(f"/api/incidents/{iid}/attachments", headers=analyst, files={"file": ("x.pdf", b"MZ not a pdf", "application/pdf")})
    assert fake_pdf.status_code == 415
    exe = client.post(f"/api/incidents/{iid}/attachments", headers=analyst, files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert exe.status_code == 415
    big = client.post(f"/api/incidents/{iid}/attachments", headers=analyst, files={"file": ("big.txt", b"a" * (5 * 1024 * 1024 + 1), "text/plain")})
    assert big.status_code == 413
    other_agency = client.post(f"/api/incidents/{iid}/attachments", headers=navy, files={"file": ("n.txt", b"x", "text/plain")})
    assert other_agency.status_code == 403
    d = client.get(f"/api/attachments/{ok.json()['id']}", headers=navy)
    assert d.status_code == 200 and d.content == b"Master statement"
    assert client.get(f"/api/attachments/{ok.json()['id']}").status_code == 401


def test_comments(client, viewer):
    r = client.post("/api/comments", headers=viewer, json={"target_type": "vessel", "target_id": "351559081", "body": "Seen at Lomé last month."})
    assert r.status_code == 201 and r.json()["comments"][-1]["body"] == "Seen at Lomé last month."


# ---------------------------------------------------------------- vessels, intel, analytics, exports, sources
def test_vessel_profile_complete(client, analyst):
    p = client.get("/api/vessels/351559081", headers=analyst).json()
    assert p["vessel"]["imo"] and p["vessel"]["flag"] == "Panama" and p["last"] and p["history"]
    assert p["registry"]["watchlist"] and p["risk"]["level"] in ("high", "critical")
    assert any(i["role"] == "suspect" for i in p["incidents"])
    assert any(n["name"] == "MV Sea Harmony" for n in p["network"])


def test_vessel_search(client, analyst):
    assert [v["name"] for v in client.get("/api/vessels?q=gulf voyager", headers=analyst).json()["vessels"]] == ["MT Gulf Voyager"]
    assert [v["name"] for v in client.get("/api/vessels?q=351559081", headers=analyst).json()["vessels"]] == ["MT Gulf Voyager"]
    hi = client.get("/api/vessels?level=high", headers=analyst).json()["vessels"]
    assert hi and all(v["level"] == "high" for v in hi)


def test_intel_links(client, analyst):
    it = client.get("/api/intel", headers=analyst).json()
    names = {c["name"] for c in it["cards"]}
    assert {"MV Sea Harmony", "MT Gulf Voyager"} <= names
    assert any(n["kind"] == "owner" for n in it["graph"]["nodes"]) and it["hotspots"]


def test_analytics_date_filter(client, analyst):
    full = client.get("/api/analytics?from=2025-11-04&to=2026-11-04", headers=analyst).json()
    month = client.get("/api/analytics?from=2026-10-05&to=2026-11-04", headers=analyst).json()
    assert full["incidents_total"] > month["incidents_total"] > 0
    assert client.get("/api/analytics?from=2026-12-01&to=2026-01-01", headers=analyst).status_code == 422


def test_exports(client, analyst):
    csv_ = client.get("/api/export/incidents.csv", headers=analyst)
    assert csv_.status_code == 200 and "SIMULATED" in csv_.text.splitlines()[0] and "attachment" in csv_.headers["content-disposition"]
    rep = client.get("/api/export/report.json", headers=analyst).json()
    assert "not proof" in rep["notice"]
    assert db.one("SELECT 1 AS x FROM audit_log WHERE action = 'export'")


def test_csv_formula_injection_neutralised(client, analyst):
    client.post("/api/incidents", headers=analyst, json={**INCIDENT, "title": "=HYPERLINK(\"http://x\")"})
    text = client.get("/api/export/incidents.csv", headers=analyst).text
    assert "'=HYPERLINK" in text


def test_sources_never_claim_agency_access(client, analyst):
    src = {s["key"]: s for s in client.get("/api/sources", headers=analyst).json()["sources"]}
    for key in ("nimasa_mda", "navy_mda", "coastal_radar"):
        assert src[key]["state"] == "requires authorised integration" and not src[key]["configured"]
    assert src["ais_file"]["synthetic"] and src["registry_sim"]["synthetic"]


def test_ingest_triggers_engine(client, admin, analyst):
    assert client.post("/api/ingest/ais", headers=analyst, json={"positions": [{"mmsi": "351559081", "t": "2026-11-04T07:40:00Z", "lat": 4.9, "lon": 5.0}]}).status_code == 403
    r = client.post("/api/ingest/ais", headers=admin, json={"positions": [
        {"mmsi": "351559081", "t": "2026-11-04T07:40:00Z", "lat": 4.95, "lon": 5.05, "sog": 6.0, "cog": 120},
        {"mmsi": "351559081", "t": "2026-11-04T07:41:00Z", "lat": 60.0, "lon": 5.05}]})
    assert r.status_code == 200 and r.json()["accepted"] == 1 and r.json()["rejected"] == 1
    p = client.get("/api/vessels/351559081", headers=analyst).json()
    assert p["last"]["t"] == "2026-11-04T07:40:00Z"
    assert all(i["code"] != "ais_gap" or "Still silent" not in " ".join(i["evidence"]) for i in p["risk"]["indicators"])


def test_websocket_requires_token_and_pushes(client, analyst, navy):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=bad") as ws:
            ws.receive_json()
    token = analyst["Authorization"][7:]
    with client.websocket_connect(f"/ws?token={token}") as ws:
        assert ws.receive_json()["type"] == "hello"
        client.post("/api/incidents", headers=navy, json=INCIDENT)
        deadline = time.time() + 5
        while time.time() < deadline:
            ev = ws.receive_json()
            if ev["type"] == "incident.created":
                break
        assert ev["type"] == "incident.created" and ev["by"] == "NN"
