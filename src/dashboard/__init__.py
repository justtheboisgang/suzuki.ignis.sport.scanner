"""FastAPI dashboard. Function over form: fast, filterable views of hits,
candidates, sources, coverage and system health.

`app`/`create_app` are imported lazily so that read-only helpers
(`src.dashboard.queries`) can be used — e.g. by the CLI `report`/`stats`
commands — without constructing the FastAPI app (which pulls in web-only
dependencies like python-multipart)."""


def __getattr__(name):  # PEP 562 lazy attribute access
    if name in ("app", "create_app"):
        from .app import app, create_app
        return {"app": app, "create_app": create_app}[name]
    raise AttributeError(name)


__all__ = ["app", "create_app"]
