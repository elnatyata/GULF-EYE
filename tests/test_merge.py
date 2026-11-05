"""Merging data from different agencies: fused contacts, duplicate incidents, counting once, fair corroboration, conflicts."""
from backend import db

ROBBERY_NPA, ROBBERY_MPOL = None, None


def _ids(client, h):
    rows = {r["title"]: r["id"] for r in db.q("SELECT id, title FROM incidents")}
    return rows["Mooring ropes stolen at Lagos anchorage"], rows["Robbery at anchor reported by MV Mono Bay"]


# ---------------------------------------------------------------- fused contacts
def test_fused_contact_across_agencies_and_satellite(client, analyst):
    fused = client.get("/api/fused", headers=analyst).json()["fused"]
    bonny = next(f for f in fused if "D-041" in [m["id"] for m in f["members"]])
    assert set(bonny["sources"]) == {"NN", "NIMASA", "Sentinel-1"} and bonny["independent_sources"] == 3
    assert bonny["confidence"] > bonny["single_best"]            # agreement raises confidence
    sts = next(f for f in fused if "D-017" in [m["id"] for m in f["members"]])
    assert "NIMASA" in sts["sources"]                              # coastal radar corroborates the STS barge


def test_same_agency_twice_is_one_source(client, navy):
    r = client.post("/api/intake/sightings", headers=navy, json={"records": [
        {"ref": "M-1", "observed_at": "2026-11-04T03:00:00Z", "lat": 2.0, "lon": 4.0, "description": "skiff"},
        {"ref": "M-2", "observed_at": "2026-11-04T03:20:00Z", "lat": 2.01, "lon": 4.01, "description": "same skiff"}]})
    assert r.json()["accepted"] == 2
    f = next(f for f in client.get("/api/fused", headers=navy).json()["fused"] if {m["text"] for m in f["members"]} == {"skiff", "same skiff"})
    assert f["independent_sources"] == 1 and f["confidence"] == f["single_best"]


def test_fused_contacts_ignore_restricted_records(client, police, analyst):
    client.post("/api/intake/sightings", headers=police, json={"records": [
        {"ref": "M-SECRET", "observed_at": "2026-11-04T05:42:00Z", "lat": 3.841, "lon": 6.881, "description": "secret source",
         "visibility": "agency"}]})
    for f in client.get("/api/fused", headers=analyst).json()["fused"]:
        assert all(m["text"] != "secret source" for m in f["members"])


def test_radar_alert_link_and_fused_detail(client, analyst):
    fid = client.get("/api/fused", headers=analyst).json()["fused"][0]["id"]
    assert client.get(f"/api/fused/{fid}", headers=analyst).status_code == 200
    assert client.get("/api/fused/FC-000000", headers=analyst).status_code == 404


# ---------------------------------------------------------------- duplicate incidents
def test_duplicates_found_only_for_real_candidates(client, analyst):
    npa, mpol = _ids(client, analyst)
    cands = client.get("/api/incidents/duplicates", headers=analyst).json()["candidates"]
    pair = next(c for c in cands if {c["primary"]["id"], c["duplicate"]["id"]} == {npa, mpol})
    assert pair["primary"]["id"] == npa and any("same vessel" in r for r in pair["reasons"])
    assert pair["score"] >= 0.8
    # the 150 simulated history incidents produce no false matches
    hist = {r["id"] for r in db.q("SELECT id FROM incidents WHERE source = 'simulated_history'")}
    assert all(not ({c["primary"]["id"], c["duplicate"]["id"]} <= hist) for c in cands)


def test_viewer_cannot_merge(client, viewer):
    npa, mpol = _ids(client, viewer)
    assert client.post("/api/incidents/merge", headers=viewer, json={"primary": npa, "duplicate": mpol}).status_code == 403


def test_merge_counts_once_keeps_reports_and_undo(client, analyst, navy):
    npa, mpol = _ids(client, analyst)
    before_list = client.get("/api/incidents?from=2026-11-01", headers=analyst).json()["total"]
    before_stats = client.get("/api/analytics", headers=analyst).json()["incidents_total"]
    r = client.post("/api/incidents/merge", headers=navy, json={"primary": npa, "duplicate": mpol, "note": "Same robbery"})
    assert r.status_code == 201, r.text
    mid = r.json()["merge_id"]
    assert client.get("/api/incidents?from=2026-11-01", headers=analyst).json()["total"] == before_list - 1
    assert client.get("/api/analytics", headers=analyst).json()["incidents_total"] == before_stats - 1
    p = client.get(f"/api/incidents/{npa}", headers=analyst).json()
    assert p["agencies"] == ["MPOL", "NPA"] and len(p["reports"]) == 2 and p["merged_reports"] == [mpol]
    d = client.get(f"/api/incidents/{mpol}", headers=analyst).json()
    assert d["merged_into"] == npa                                   # the duplicate is still readable
    csv_text = client.get("/api/export/incidents.csv?from=2026-11-01", headers=analyst).text
    assert mpol not in csv_text.split(npa)[0] and "MPOL; NPA" in csv_text
    # rules: no merging a merged report, no chains
    assert client.post("/api/incidents/merge", headers=navy, json={"primary": mpol, "duplicate": npa}).status_code == 409
    assert db.one("SELECT 1 AS x FROM audit_log WHERE action = 'incident.merge'")
    # the pair is no longer suggested while merged
    cands = client.get("/api/incidents/duplicates", headers=analyst).json()["candidates"]
    assert not any({c["primary"]["id"], c["duplicate"]["id"]} == {npa, mpol} for c in cands)
    # undo restores both
    assert client.post(f"/api/incidents/merge/{mid}/undo", headers=navy).status_code == 200
    assert client.get("/api/incidents?from=2026-11-01", headers=analyst).json()["total"] == before_list
    assert client.get(f"/api/incidents/{mpol}", headers=analyst).json()["merged_into"] is None
    assert client.post(f"/api/incidents/merge/{mid}/undo", headers=navy).status_code == 404


def test_not_a_duplicate_is_remembered(client, analyst):
    rows = {r["title"]: r["id"] for r in db.q("SELECT id, title FROM incidents")}
    a = rows["Fast skiff without lights near the Bonny fairway buoy"]
    b = rows["Unlit skiff shadowed fishing vessels near the fairway buoy"]
    cands = client.get("/api/incidents/duplicates", headers=analyst).json()["candidates"]
    assert any({c["primary"]["id"], c["duplicate"]["id"]} == {a, b} for c in cands)
    assert client.post("/api/incidents/not-duplicate", headers=analyst, json={"a": a, "b": b, "note": "different skiffs"}).status_code == 201
    cands = client.get("/api/incidents/duplicates", headers=analyst).json()["candidates"]
    assert not any({c["primary"]["id"], c["duplicate"]["id"]} == {a, b} for c in cands)


def test_merged_reports_do_not_double_count_prior_incident(client, analyst, navy, police):
    # two agencies report the same event naming the same suspect; after the merge the indicator lists it once
    rec = {"type": "oil_theft", "title": "Barge alongside", "observed_at": "2026-10-20T02:00:00Z", "lat": 4.3, "lon": 5.9,
           "description": "Hoses connected", "mmsi": "563452857", "role": "suspect"}
    i1 = client.post("/api/intake/incidents", headers=navy, json={"records": [{**rec, "ref": "DC-1"}]}).json()["created"][0]
    i2 = client.post("/api/intake/incidents", headers=police, json={"records": [{**rec, "ref": "DC-2", "lat": 4.31}]}).json()["created"][0]
    before = [i for i in client.get("/api/vessels/563452857", headers=analyst).json()["risk"]["indicators"] if i["code"] == "prior_incident"]
    assert len(before[0]["evidence"]) == 2
    client.post("/api/incidents/merge", headers=analyst, json={"primary": i1, "duplicate": i2})
    after = [i for i in client.get("/api/vessels/563452857", headers=analyst).json()["risk"]["indicators"] if i["code"] == "prior_incident"]
    assert len(after[0]["evidence"]) == 1 and i1 in after[0]["evidence"][0]


# ---------------------------------------------------------------- fair corroboration and conflicts
def test_agreeing_agencies_add_a_capped_bonus_not_a_stack(client, analyst):
    ind = [i for i in client.get("/api/vessels/351559081", headers=analyst).json()["risk"]["indicators"] if i["code"] == "agency_watchlist"]
    assert len(ind) == 1                                  # one indicator for all listings
    assert ind[0]["points"] == 15 + 3                      # suspect (MPOL) + one extra agency (NN)
    assert set(ind[0]["agencies"]) == {"MPOL", "NN"}


def test_consolidated_record_shows_disagreement(client, analyst):
    c = client.get("/api/vessels/610438987", headers=analyst).json()["consolidated"]
    flag = next(f for f in c["fields"] if f["field"] == "flag")
    assert flag["conflict"] and {v["value"] for v in flag["values"]} == {"Benin", "Togo"}
    ok = client.get("/api/vessels/657267003", headers=analyst).json()["consolidated"]
    assert ok["conflicts"] == 0
