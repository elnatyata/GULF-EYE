"""One-click demo sign-in for judges: only the seeded demo accounts, audited, roles unchanged, and it can be switched off."""
from backend import config


def test_demo_accounts_listed(client):
    r = client.get("/api/auth/demo").json()
    assert r["enabled"] and len(r["accounts"]) == 8
    assert {"admin", "analyst", "operator", "viewer"} == {a["role"] for a in r["accounts"]}
    assert all("pw_hash" not in a for a in r["accounts"])


def test_demo_login_keeps_role_limits(client, admin):
    r = client.post("/api/auth/demo", json={"email": "liaison@npa.demo"})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/api/me", headers=h).json()["role"] == "viewer"
    assert client.post("/api/engine/run", headers=h).status_code == 403          # read-only stays read-only
    log = client.get("/api/audit?action=auth.login", headers=admin).json()
    rows = log["entries"]
    assert any("one-click demo" in (x.get("detail") or "") for x in rows)


def test_demo_login_only_for_demo_accounts(client, admin):
    client.post("/api/users", headers=admin, json={"email": "real.person@nimasa.gov.ng", "name": "Real Person", "role": "analyst",
                                                    "agency": "NIMASA", "password": "Long-enough-pass-2026"})
    assert client.post("/api/auth/demo", json={"email": "real.person@nimasa.gov.ng"}).status_code == 403
    assert client.post("/api/auth/demo", json={"email": "nobody@x.demo"}).status_code == 403


def test_demo_login_can_be_switched_off(client, monkeypatch):
    monkeypatch.setattr(config, "DEMO_LOGIN", False)
    assert client.get("/api/auth/demo").json() == {"enabled": False, "accounts": []}
    assert client.post("/api/auth/demo", json={"email": "admin@gulfeye.demo"}).status_code == 403
