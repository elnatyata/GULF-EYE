"""First-start seeding: agencies, demo users, simulated registry and incident history, first engine run.

Runs only on an empty database. `python -m backend --reset` wipes var/ and seeds again.
"""
import json

from . import audit, config, db, security
from .adapters import REGISTRY

AGENCIES = [
    ("GULFEYE", "GulfEye operations cell", "platform"),
    ("NIMASA", "Nigerian Maritime Administration and Safety Agency", "regulator"),
    ("NN", "Nigerian Navy", "navy"),
    ("MPOL", "Marine Police", "police"),
    ("NPA", "Nigerian Ports Authority", "ports"),
    ("NAF", "Nigerian Air Force (maritime patrol)", "air force"),
    ("NCS", "Nigeria Customs Service (marine)", "customs"),
    ("MMCC", "Regional maritime coordination centre (Zone E)", "regional centre"),
]
# Demo accounts. Agency names are used to show how sharing works; GulfEye has no link to these organisations.
USERS = [
    ("admin@gulfeye.demo", "Nathan El-nathan", "admin", "GULFEYE"),
    ("analyst@nimasa.demo", "Nathan El-nathan", "analyst", "NIMASA"),
    ("ops@navy.demo", "Lt Cdr Nathan El-nathan", "operator", "NN"),
    ("ops@police.demo", "Insp. Nathan EL-nathan", "operator", "MPOL"),
    ("liaison@npa.demo", "Nathan El-nathan", "viewer", "NPA"),
    ("ops@airforce.demo", "Flt Lt Nathan El-nathan", "operator", "NAF"),
    ("analyst@customs.demo", "Nathan El-nathan", "analyst", "NCS"),
    ("watch@mmcc.demo", "Nathan El-nathan", "operator", "MMCC"),
]


def agency_id(code):
    return db.one("SELECT id FROM agencies WHERE code = ?", (code,))["id"]


def seed(run_engine=True):
    if db.one("SELECT COUNT(*) AS n FROM users")["n"]:
        return False
    now = audit.now_iso()
    with db.tx() as c:
        for code, name, kind in AGENCIES:
            c.execute("INSERT INTO agencies(code, name, kind) VALUES(?,?,?)", (code, name, kind))
    pw = security.hash_password(config.DEMO_PASSWORD)
    with db.tx() as c:
        for email, name, role, ag in USERS:
            c.execute("INSERT INTO users(email, name, role, agency_id, pw_hash, created_at) VALUES(?,?,?,?,?,?)",
                      (email, name, role, agency_id(ag), pw, now))
        for r in REGISTRY.load():
            c.execute("""INSERT INTO registry(vessel_id, imo, mmsi, name, flag, call_sign, owner, manager, built, gross_tonnage, length_m,
                         class_society, flag_history, former_names, last_psc, watchlist, notes, source, synthetic)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                      (r["vessel_id"], r["imo"], r["mmsi"], r["name"], r["flag"], r["call_sign"], r["owner"], r["manager"], r["built"],
                       r["gross_tonnage"], r["length_m"], r["class_society"], json.dumps(r["flag_history"]),
                       json.dumps(r.get("former_names") or []), json.dumps(r["last_psc"]), r["watchlist"], r["notes"], REGISTRY.key))
        with open(config.SEED_DIR / "incidents_history.json", encoding="utf-8") as f:
            history = json.load(f)["incidents"]
        for n, inc in enumerate(history, 1):
            iid = f"INC-{inc['occurred_at'][:4]}-{n:04d}"
            c.execute("""INSERT INTO incidents(id, type, title, description, lat, lon, place, occurred_at, status, visibility, agency_id,
                         reporter_id, created_at, updated_at, source, synthetic) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                      (iid, inc["type"], inc["title"], inc["description"], inc["lat"], inc["lon"], inc["place"], inc["occurred_at"],
                       inc["status"], inc["visibility"], agency_id(inc["agency"]), None, inc["occurred_at"], inc["occurred_at"],
                       "simulated_history"))
            c.execute("INSERT INTO incident_history(incident_id, user_id, from_status, to_status, note, at) VALUES(?,?,?,?,?,?)",
                      (iid, None, None, inc["status"], "Imported from the simulated incident history.", inc["occurred_at"]))
            for vid, role in inc["vessels"]:
                c.execute("INSERT INTO incident_vessels(incident_id, vessel_id, role) VALUES(?,?,?)", (iid, vid, role))
    db.set_meta("seeded_at", now)
    audit.log(None, "system.seed", None, {"users": len(USERS), "incidents": len(history)}, email="system")
    if run_engine:
        from .ml.engine import run_engine as _run
        _run(None)
    return True
