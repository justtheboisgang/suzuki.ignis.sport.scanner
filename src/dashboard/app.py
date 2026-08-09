"""FastAPI dashboard app.

Server-rendered (Jinja2) so it's fully self-contained — no build step, no
external assets. Provides the home overview, filterable listing views, a vehicle
detail page, source coverage, system health, plus small POST endpoints for
feedback, manual source/URL submission and triggering scans.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..database.base import SessionLocal
from ..database.init_db import init_db
from ..models.feedback import Feedback
from ..models.listing import Listing
from ..models.source import Source
from . import queries as Q

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Manual scan/discovery kicked off from the dashboard runs in a daemon thread,
# coordinated by the shared job lock so discovery and scan never collide.
import threading  # noqa: E402

from ..pipeline import jobs as _jobs  # noqa: E402
from ..utils.logging import get_logger  # noqa: E402

_log = get_logger("dashboard")


def _start_job(job_type: str, fn) -> bool:
    """Start a coordinated heavy job in the background. Returns False if a job
    is already running (discovery or scan)."""
    if _jobs.is_any_job_running():
        return False

    def _worker():
        _jobs.run_exclusive(job_type, fn)

    threading.Thread(target=_worker, name=f"job-{job_type}", daemon=True).start()
    return True


def create_app() -> FastAPI:
    app = FastAPI(title="Suzuki Ignis Sport Europe Hunter")
    init_db()

    def db():
        return SessionLocal()

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        s = db()
        try:
            ctx = {
                "request": request,
                "stats": Q.home_stats(s),
                "metrics": Q.secondary_metrics(s),
                "health": Q.source_health(s),
                "new_today": Q.filter_listings(s, min_confidence=60,
                                               order_by="newest", limit=15),
                "best": Q.filter_listings(s, min_confidence=60,
                                          order_by="opportunity", limit=15),
                "uncertain": Q.filter_listings(s, min_confidence=40, limit=15,
                                               order_by="confidence"),
                "notifications": Q.recent_notifications(s, 12),
                "new_sources": Q.newest_sources(s, 12),
                "price_drops": Q.price_drops(s, 12),
                "jobs": _jobs.all_job_status(),
            }
            return templates.TemplateResponse(request, "home.html", ctx)
        finally:
            s.close()

    @app.get("/listings", response_class=HTMLResponse)
    def listings(request: Request,
                 min_confidence: int = Query(40),
                 country: str | None = None,
                 max_price: float | None = None,
                 min_price: float | None = None,
                 max_mileage: int | None = None,
                 min_year: int | None = None,
                 seller_type: str | None = None,
                 status: str | None = None,
                 lhd: str | None = None,
                 order_by: str = "opportunity"):
        s = db()
        try:
            rows = Q.filter_listings(
                s, min_confidence=min_confidence, country=country,
                max_price=max_price, min_price=min_price,
                max_mileage=max_mileage, min_year=min_year,
                seller_type=seller_type, status=status, lhd=lhd,
                order_by=order_by, limit=400)
            return templates.TemplateResponse(request, "listings.html", {
                "request": request, "listings": rows,
                "filters": {
                    "min_confidence": min_confidence, "country": country,
                    "max_price": max_price, "min_price": min_price,
                    "max_mileage": max_mileage, "min_year": min_year,
                    "seller_type": seller_type, "status": status, "lhd": lhd,
                    "order_by": order_by,
                },
            })
        finally:
            s.close()

    @app.get("/listing/{internal_id}", response_class=HTMLResponse)
    def listing_detail(request: Request, internal_id: str):
        s = db()
        try:
            lst = s.query(Listing).filter(Listing.internal_id == internal_id).first()
            if not lst:
                return HTMLResponse("Not found", status_code=404)
            price_hist = sorted(lst.price_history, key=lambda p: p.changed_at)
            status_hist = sorted(lst.status_history, key=lambda p: p.changed_at)
            source = s.query(Source).filter(Source.id == lst.source_id).first()
            return templates.TemplateResponse(request, "detail.html", {
                "request": request, "l": lst, "price_hist": price_hist,
                "status_hist": status_hist, "source": source,
            })
        finally:
            s.close()

    @app.get("/sources", response_class=HTMLResponse)
    def sources(request: Request, country: str | None = None):
        s = db()
        try:
            q = s.query(Source)
            if country:
                q = q.filter(Source.country == country.upper())
            rows = q.order_by(Source.discovery_value.desc()).limit(500).all()
            return templates.TemplateResponse(request, "sources.html", {
                "request": request, "sources": rows,
                "coverage": Q.country_coverage(s),
                "health": Q.source_health(s),
                "country": country,
            })
        finally:
            s.close()

    @app.get("/health", response_class=HTMLResponse)
    def health(request: Request):
        s = db()
        try:
            return templates.TemplateResponse(request, "health.html", {
                "request": request,
                "health": Q.source_health(s),
                "coverage": Q.country_coverage(s),
                "metrics": Q.secondary_metrics(s),
                "discovery": Q.discovery_effectiveness(s, 25),
                "manual_sources": s.query(Source).filter(
                    Source.manual_review.is_(True)).limit(100).all(),
                "failed_sources": s.query(Source).filter(
                    Source.health.in_(["failed", "degraded"])).limit(100).all(),
            })
        finally:
            s.close()

    # --- Actions --------------------------------------------------------
    @app.post("/feedback")
    def feedback(internal_id: str = Form(...), label: str = Form(...),
                 note: str = Form("")):
        from ..database.writer import run_write

        def _do(s):
            lst = s.query(Listing).filter(Listing.internal_id == internal_id).first()
            s.add(Feedback(listing_id=lst.id if lst else None,
                           seller_name=lst.seller_name if lst else None,
                           label=label, note=note or None))
        run_write(_do, label="feedback")
        return RedirectResponse(f"/listing/{internal_id}", status_code=303)

    @app.post("/add-source")
    def add_source(url: str = Form(...), country: str = Form("")):
        from ..pipeline.manual import add_watch_source
        result = add_watch_source(url, country=country or None)
        return JSONResponse(result)

    @app.post("/analyze-url")
    def analyze_url(url: str = Form(...), country: str = Form("")):
        from ..pipeline.manual import analyze_manual_url
        return JSONResponse(analyze_manual_url(url, country=country or None))

    @app.post("/run-scan")
    def trigger_scan(limit: int = Form(20)):
        from ..pipeline.scan import run_scan
        started = _start_job("scan", lambda: run_scan(
            limit=limit, force=True, backup=False,
            progress_cb=_jobs.make_progress_updater("scan")))
        return JSONResponse({"started": started,
                             "note": "Scan running in background; watch the Jobs "
                                     "panel." if started
                                     else "A job is already running."})

    @app.post("/run-discovery")
    def trigger_discovery(max_queries: int = Form(60)):
        from ..pipeline.scan import run_discovery
        started = _start_job("discovery", lambda: run_discovery(
            max_queries=max_queries,
            progress_cb=_jobs.make_progress_updater("discovery")))
        return JSONResponse({"started": started,
                             "note": "Discovery running in background; watch the "
                                     "Jobs panel." if started
                                     else "A job is already running."})

    @app.get("/api/jobs")
    def api_jobs():
        return JSONResponse(_jobs.all_job_status())

    @app.get("/healthz")
    def healthz():
        """Liveness probe for Docker/systemd — confirms the DB is reachable."""
        s = db()
        try:
            s.query(Source).count()
            return JSONResponse({"status": "ok"})
        except Exception as exc:  # pragma: no cover
            return JSONResponse({"status": "error", "detail": str(exc)},
                                status_code=500)
        finally:
            s.close()

    @app.get("/api/stats")
    def api_stats():
        s = db()
        try:
            return JSONResponse({
                "home": _jsonable(Q.home_stats(s)),
                "metrics": Q.secondary_metrics(s),
                "health": Q.source_health(s),
            })
        finally:
            s.close()

    return app


def _jsonable(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if hasattr(v, "scan_id"):
            out[k] = getattr(v, "scan_id", None)
        else:
            out[k] = v
    return out


app = create_app()
