"""Data adapters: one class per source, all with the same small interface.

    status() -> dict      what the source is, whether it is configured, simulated or restricted
    load()   -> object    the data, in the shape the engine expects (adapter-specific)

To add a real feed, subclass Adapter, implement load(), and register it in adapters/__init__.py. The engine and the
API only talk to adapters, never to files or remote systems directly.
"""


class Adapter:
    key = "base"
    name = "Base adapter"
    kind = "other"            # ais | sar | registry | incidents | radar | agency
    synthetic = False
    restricted = False        # needs an agreement/credentials with the data owner
    description = ""

    def configured(self) -> bool:
        return True

    def status(self) -> dict:
        ok = self.configured()
        if self.restricted and not ok:
            state = "requires authorised integration"
        elif ok:
            state = "simulated data" if self.synthetic else "connected"
        else:
            state = "not configured"
        return {"key": self.key, "name": self.name, "kind": self.kind, "synthetic": self.synthetic,
                "restricted": self.restricted, "configured": ok, "state": state, "description": self.description,
                **self.extra_status()}

    def extra_status(self) -> dict:
        return {}

    def load(self):
        raise NotImplementedError
