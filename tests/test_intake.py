"""Agency data intake: authentication by user or API key, scopes, validation, duplicates, visibility, CSV, engine effect."""
from backend import db

SIGHTING = {"ref": "T-SGT-1", "observed_at": "2026-11-04T06:30:00Z", "lat": 4.1, "lon": 6.2, "mmsi": "657267003",
            "description": "Seen underway, normal"}


def _key(client, headers, scopes, name="test feed", agency=None):
    body = {"name": name, "scopes": scopes}
    if agency:
        body["agency"] = agency
    r = client.post("/api/apikeys", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()["key"]


def test_demo_submissions_seeded(client, admin):
    subs = client.get("/api/intake/submissions", headers=admin).json()
    agencies = {s["agency"] for s in subs["by_agency"]}
    assert {"NN", "NIMASA", "MPOL", "NPA"} <= agencies
    rejected = [e for s in subs["submissions"] for e in s["errors"]]
    assert any("outside the Gulf of Guinea" in e["error"] for e in rejected)


def test_intake_needs_auth(client):
    assert client.post("/api/intake/sightings", json={"records": [SIGHTING]}).status_code == 401
    assert client.post("/api/intake/sightings", headers={"X-API-Key": "ge_deadbeef_nope"}, json={"records": [SIGHTING]}).status_code == 401


def test_viewer_cannot_submit_or_make_keys(client, viewer):
    assert client.post("/api/intake/sightings", headers=viewer, json={"records": [SIGHTING]}).status_code == 403
    assert client.post("/api/apikeys", headers=viewer, json={"name": "x feed", "scopes": ["sightings"]}).status_code == 403


def test_user_json_submission_partial_accept(client, navy):
    bad_time = {**SIGHTING, "ref": "T-SGT-2", "observed_at": "2099-01-01T00:00:00Z"}
    bad_pos = {**SIGHTING, "ref": "T-SGT-3", "lat": 30}
    extra = {**SIGHTING, "ref": "T-SGT-4", "colour": "red"}
    r = client.post("/api/intake/sightings", headers=navy, json={"records": [SIGHTING, bad_time, bad_pos, extra]})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["accepted"] == 1 and res["rejected"] == 3 and [e["record"] for e in res["errors"]] == [2, 3, 4]
    dup = client.post("/api/intake/sightings", headers=navy, json={"records": [SIGHTING]}).json()
    assert dup["accepted"] == 0 and "duplicate" in dup["errors"][0]["error"]
    obs = db.one("SELECT * FROM observations WHERE ref = 'T-SGT-1'")
    assert obs["vessel_id"] == "657267003" and obs["match"] == "mmsi"


def test_api_key_scopes_and_revocation(client, police, admin):
    assert client.post("/api/apikeys", headers=police, json={"name": "x feed", "scopes": ["watchlist"]}).status_code == 403
    key = _key(client, admin, ["watchlist"], name="MPOL records system", agency="MPOL")
    h = {"X-API-Key": key}
    assert client.post("/api/intake/sightings", headers=h, json={"records": [SIGHTING]}).status_code == 403
    r = client.post("/api/intake/watchlist", headers=h, json={"records": [
        {"ref": "T-WL-1", "mmsi": "657267003", "level": "monitor", "reason": "Test listing"},
        {"ref": "T-WL-2", "imo": "1234568", "level": "suspect", "reason": "bad check digit"},
        {"ref": "T-WL-3", "level": "monitor", "reason": "no identifier"}]})
    res = r.json()
    assert res["accepted"] == 1 and res["rejected"] == 2
    assert "check digit" in res["errors"][0]["error"] and "at least one" in res["errors"][1]["error"]
    sub = db.one("SELECT * FROM submissions ORDER BY id DESC LIMIT 1")
    assert sub["api_key_id"] and sub["channel"] == "api"
    assert db.one("SELECT 1 AS x FROM audit_log WHERE action = 'intake.submit' AND email LIKE 'api-key:%'")
    kid = db.one("SELECT id FROM api_keys WHERE name = 'MPOL records system'")["id"]
    assert db.one("SELECT agency FROM audit_log WHERE action = 'intake.submit' ORDER BY id DESC LIMIT 1") is not None
    assert client.delete(f"/api/apikeys/{kid}", headers=admin).status_code == 200
    assert client.post("/api/intake/watchlist", headers=h, json={"records": [{"mmsi": "657267003", "reason": "again"}]}).status_code == 401


def test_keys_stored_hashed_and_scoped_to_agency(client, analyst, admin):
    key = _key(client, analyst, ["sightings"], name="NIMASA feed")
    row = db.one("SELECT * FROM api_keys WHERE name = 'NIMASA feed'")
    assert key not in row["key_hash"] and len(row["key_hash"]) == 64
    assert client.post("/api/apikeys", headers=analyst, json={"name": "navy feed", "scopes": ["sightings"], "agency": "NN"}).status_code == 403
    _key(client, admin, ["radar_contacts"], name="admin-made navy feed", agency="NN")
    assert all(k["agency"] == "NIMASA" for k in client.get("/api/apikeys", headers=analyst).json()["keys"])


def test_csv_upload_and_template(client, analyst):
    t = client.get("/api/intake/templates/radar_contacts.csv")
    assert t.status_code == 200 and t.text.splitlines()[0].startswith("ref,visibility,observed_at")
    csv_text = ("ref,observed_at,lat,lon,length_m,confidence,sensor\n"
                "T-CR-1,2026-11-04T05:45:00Z,3.70,6.70,12,0.7,test radar\n"
                "T-CR-2,not-a-time,3.70,6.70,12,0.7,test radar\n")
    r = client.post("/api/intake/radar_contacts/csv", headers=analyst, files={"file": ("radar.csv", csv_text.encode(), "text/csv")})
    assert r.status_code == 200 and r.json()["accepted"] == 1 and r.json()["rejected"] == 1
    bad = client.post("/api/intake/radar_contacts/csv", headers=analyst, files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert bad.status_code == 422 and "unknown column" in bad.json()["detail"]


def test_restricted_records_hidden_and_not_scored(client, police, analyst):
    r = client.post("/api/intake/watchlist", headers=police, json={"records": [
        {"ref": "T-WL-SECRET", "mmsi": "563455657", "level": "suspect", "reason": "Confidential lead", "visibility": "agency"}]})
    assert r.json()["accepted"] == 1
    assert not any(w["ref"] == "T-WL-SECRET" for w in client.get("/api/watchlist", headers=analyst).json()["entries"])
    assert any(w["ref"] == "T-WL-SECRET" for w in client.get("/api/watchlist", headers=police).json()["entries"])
    risk = client.get("/api/vessels/563455657", headers=analyst).json()["risk"]
    assert not any(i["code"] == "agency_watchlist" for i in risk["indicators"])


def test_agency_data_changes_risk(client, analyst):
    voyager = client.get("/api/vessels/351559081", headers=analyst).json()
    codes = {i["code"] for i in voyager["risk"]["indicators"]}
    assert "agency_watchlist" in codes and voyager["agency"]["watchlist"]
    pearl = client.get("/api/vessels/636806273", headers=analyst).json()
    assert any(i["code"] == "agency_contact" for i in pearl["risk"]["indicators"])
    lantern = client.get("/api/vessels/610438987", headers=analyst).json()
    assert any(i["code"] == "agency_report" for i in lantern["risk"]["indicators"])


def test_same_size_contact_is_not_a_second_craft(client, analyst):
    # a 245 m contact on top of MT Atlantic Pearl is the tanker itself, not a craft alongside
    client.post("/api/intake/radar_contacts", headers=analyst, json={"records": [
        {"ref": "T-CR-SELF", "observed_at": "2026-11-04T05:40:00Z", "lat": 5.9012, "lon": 3.8995, "length_m": 240}]})
    ind = [i for i in client.get("/api/vessels/636806273", headers=analyst).json()["risk"]["indicators"] if i["code"] == "agency_contact"]
    assert all("T-CR-SELF" not in " ".join(i["evidence"]) for i in ind)
    assert len(ind) == 1


def test_incident_batch_creates_incidents(client, navy):
    r = client.post("/api/intake/incidents", headers=navy, json={"records": [
        {"ref": "T-INC-1", "type": "boarding", "title": "Boarding attempt", "observed_at": "2026-11-04T03:00:00Z",
         "lat": 4.0, "lon": 7.0, "description": "Repelled", "mmsi": "657899021", "role": "victim"}]})
    inc_id = r.json()["created"][0]
    inc = client.get(f"/api/incidents/{inc_id}", headers=navy).json()
    assert inc["agency"] == "NN" and inc["vessels"][0]["role"] == "victim" and "T-INC-1" in inc["description"]


def test_intel_and_sources_show_agency_data(client, analyst):
    it = client.get("/api/intel", headers=analyst).json()
    assert any(u["kind"] == "watchlist" and "Ocean Star 7" in u["text"] for u in it["agency_unmatched"])
    src = {s["key"]: s for s in client.get("/api/sources", headers=analyst).json()["sources"]}
    assert src["agency_intake"]["records"] > 0
    assert src["nimasa_mda"]["state"] == "requires authorised integration"


def test_watchlist_toggle_only_by_owner(client, police, analyst):
    wid = db.one("SELECT id FROM watchlist WHERE ref = 'MPOL-WL-21'")["id"]
    assert client.patch(f"/api/watchlist/{wid}", headers=analyst, json={"active": False}).status_code == 403
    assert client.patch(f"/api/watchlist/{wid}", headers=police, json={"active": False}).status_code == 200
    assert db.one("SELECT active FROM watchlist WHERE id = ?", (wid,))["active"] == 0
