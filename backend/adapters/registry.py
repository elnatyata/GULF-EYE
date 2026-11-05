"""Vessel registration records and incident reports."""
import json
import os

from .. import config, db
from .base import Adapter


class SimulatedRegistryAdapter(Adapter):
    key = "registry_sim"
    name = "Vessel registry (simulated)"
    kind = "registry"
    synthetic = True
    description = ("Stand-in for a licensed ship register (IMO numbers, owners, flag history, port-state inspections). "
                   "All companies and records are invented for the demo.")

    def __init__(self):
        self.path = config.SEED_DIR / "registry.json"

    def configured(self):
        return self.path.exists()

    def extra_status(self):
        n = db.one("SELECT COUNT(*) AS n FROM registry")
        return {"records": n["n"] if n else 0}

    def load(self) -> list:
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)["records"]


class IncidentReportsAdapter(Adapter):
    key = "incidents"
    name = "Incident reports (GulfEye users)"
    kind = "incidents"
    description = "Reports filed in GulfEye by authorised users, plus a simulated 12-month history for the demo."

    def extra_status(self):
        n = db.one("SELECT COUNT(*) AS n, SUM(synthetic) AS s FROM incidents")
        return {"records": n["n"] or 0, "simulated_records": n["s"] or 0}

    def load(self) -> list:
        return db.q("SELECT * FROM incidents")


class RestrictedFeedAdapter(Adapter):
    """Placeholder for a feed owned by another agency. It never returns data until an agreement and a connector exist."""
    restricted = True

    def __init__(self, key, name, kind, env, description):
        self.key, self.name, self.kind, self.env, self.description = key, name, kind, env, description

    def configured(self):
        return bool(os.environ.get(self.env))

    def extra_status(self):
        return {"env": self.env}

    def load(self):
        raise PermissionError(f"{self.name}: no authorised connector is installed. Set up a data-sharing agreement, "
                              f"implement load() for the agreed interface, then set {self.env}.")
