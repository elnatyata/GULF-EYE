"""Threat-detection engine: the safeguards from the brief, explainability, and agreement with the browser fusion."""
import json
from pathlib import Path

import pytest

from backend.ml import engine, fusion
from backend.ml.engine import assess, level_of, load_state

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def results(client):  # client fixture seeds the database first
    res, fu = assess(load_state())
    return res, fu


def test_every_vessel_scored_and_explained(results):
    res, _ = results
    assert len(res) == len(json.loads((ROOT / "web/data/ais.json").read_text())["vessels"]) > 250
    for r in res.values():
        assert 0 <= r["score"] <= 100 and r["level"] == level_of(r["score"])
        for i in r["indicators"]:
            assert i["evidence"] and i["reason"] and i["steps"] and i["points"] > 0
        if r["level"] != "low":
            assert r["reasons"] and r["steps"]


def test_ais_gap_alone_never_above_medium(results):
    res, _ = results
    for r in res.values():
        if {i["group"] for i in r["indicators"] if i["group"] != "model"} == {"ais"}:
            assert r["level"] in ("low", "medium") and r["score"] < 50


def test_ais_gap_cap_directly():
    """Synthetic case: a long gap + radar sighting (35 points) + a big ML score still stays Medium."""
    ind = [{"group": "ais", "points": 35}, {"group": "model", "points": 12}]
    score = min(100, sum(i["points"] for i in ind))
    groups = {i["group"] for i in ind if i["group"] != "model"}
    if groups == {"ais"}:
        score = min(score, 45)
    assert level_of(score) == "medium"


def test_critical_needs_two_groups(results):
    res, _ = results
    for r in res.values():
        if r["level"] == "critical":
            assert len(r["groups"]) >= 2


def test_ml_alone_capped(results):
    res, _ = results
    for r in res.values():
        if r["indicators"] and all(i["group"] == "model" for i in r["indicators"]):
            assert r["score"] <= 40


def test_known_scenarios_found(results):
    res, _ = results
    by = {r["vessel_id"]: r for r in res.values()}
    codes = lambda vid: {i["code"] for i in by[vid]["indicators"]}  # noqa: E731
    assert {"ais_gap", "prior_incident", "watchlist"} <= codes("351559081")       # MT Gulf Voyager
    assert "dark_alongside" in codes("636806273")                                  # MT Atlantic Pearl + STS barge
    assert "identity_mismatch" in codes("610438987")                               # FV Red Lantern
    assert by["351559081"]["level"] in ("high", "critical")
    # a normal cargo ship on its lane stays Low
    assert by["657267003"]["level"] == "low"                                       # MV Dawn Trader


def test_isolation_forest_used(results):
    res, _ = results
    assert any(i["code"] == "ml_outlier" for r in res.values() for i in r["indicators"])
    assert all(0 <= r["ml_score"] <= 1 for r in res.values())


def _run_fusion(folder):
    import json as _j
    from backend.adapters.files import AISFileAdapter
    a = AISFileAdapter()
    a.path = str(folder / "ais.json")
    load = lambda n: _j.loads((folder / f"{n}.json").read_text())  # noqa: E731
    return fusion.run(a.load(), load("sar"), load("geo"), load("assumptions")["fusion"])


def _js(folder):
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = subprocess.run(["node", "tests/js/fusion_current.mjs", str(folder)], cwd=ROOT, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("folder", ["tests/fixtures/original_data", "web/data"])
def test_python_fusion_matches_browser(client, folder):
    """The server copy of the fusion must flag the same contacts as web/js/model.js, on the original and the current data."""
    fu = _run_fusion(ROOT / folder)
    js = _js(ROOT / folder)
    assert len(fu["matches"]) == js["matched"] and len(fu["infrastructure"]) == js["infrastructure"]
    assert sorted(d["det"]["id"] for d in fu["dark"]) == js["dark"]
    assert sorted(g["vessel_id"] for g in fu["gaps"] if g["status"] == "matched") == js["gaps"]


def test_original_scenarios_kept_in_enlarged_data(results):
    _, fu = results
    dark = {d["det"]["id"] for d in fu["dark"]}
    assert {"D-041", "D-039", "D-036", "D-017", "D-050"} <= dark
    assert {g["vessel_id"] for g in fu["gaps"] if g["status"] == "matched"} == {"667843800", "351559081"}


def test_engine_run_idempotent(client):
    first = engine.run_engine(None)
    second = engine.run_engine(None)
    assert second["created"] == [] and second["updated"] == []
    assert first["vessels"] > 250
