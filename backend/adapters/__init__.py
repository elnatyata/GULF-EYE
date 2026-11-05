"""Adapter registry. Add a new source here and it shows up in /api/sources."""
from .base import Adapter
from .files import AISFileAdapter, GeoAdapter, SARFileAdapter
from .registry import IncidentReportsAdapter, RestrictedFeedAdapter, SimulatedRegistryAdapter

AIS = AISFileAdapter()
SAR = SARFileAdapter()
GEO = GeoAdapter()
REGISTRY = SimulatedRegistryAdapter()
INCIDENTS = IncidentReportsAdapter()


class AgencyIntakeAdapter(Adapter):
    key = "agency_intake"
    name = "Agency data intake (prototype)"
    kind = "agency"
    description = ("Sightings, patrol reports, radar contacts, watchlists, incident batches and AIS positions pushed by "
                   "agencies with an API key or uploaded as CSV (Data intake screen). Phase 2 connectors will call the same intake.")

    def extra_status(self):
        from .. import db
        n = db.one("SELECT COUNT(*) AS n, SUM(accepted) AS a, MAX(received_at) AS last FROM submissions")
        return {"records": n["a"] or 0, "as_of": n["last"], "submissions": n["n"]}

    def load(self):
        from .. import db
        return db.q("SELECT * FROM observations")


INTAKE = AgencyIntakeAdapter()

RESTRICTED = [
    RestrictedFeedAdapter("nimasa_mda", "NIMASA maritime domain awareness feed", "agency", "GULFEYE_NIMASA_ENDPOINT",
                          "Phase 2: direct connector to NIMASA systems (needs a data-sharing agreement). Until then NIMASA can push data through the agency intake."),
    RestrictedFeedAdapter("navy_mda", "Nigerian Navy MDA feed", "agency", "GULFEYE_NAVY_ENDPOINT",
                          "Phase 2: direct connector to the Navy surface picture (needs authorisation). Until then the Navy can push patrol reports through the agency intake."),
    RestrictedFeedAdapter("coastal_radar", "Coastal radar network", "radar", "GULFEYE_RADAR_ENDPOINT",
                          "Phase 2: live radar plots (interface to be agreed). Until then contacts can be pushed as radar_contacts through the agency intake."),
    RestrictedFeedAdapter("regional_reporting", "Regional incident reporting centres", "incidents", "GULFEYE_REGIONAL_ENDPOINT",
                          "Incident reports from regional maritime security centres (Yaoundé architecture)."),
    RestrictedFeedAdapter("licensed_registry", "Licensed ship register", "registry", "GULFEYE_REGISTRY_ENDPOINT",
                          "Commercial or flag-state register to replace the simulated registry."),
]

ALL = [AIS, SAR, GEO, REGISTRY, INCIDENTS, INTAKE, *RESTRICTED]


def statuses() -> list:
    return [a.status() for a in ALL]
