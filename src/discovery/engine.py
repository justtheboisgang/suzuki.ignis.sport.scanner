"""The Source Discovery Engine (concurrency-safe edition).

CRITICAL DESIGN RULE (fixes the production "database is locked" bug): no write
transaction is ever held open across a network/API call. For each query we:

    1. run the provider search (network)              — no DB txn open
    2. classify genuinely new domains via the hunter  — network, no DB txn open
    3. persist everything in ONE short write txn       — via run_write (locked)

All writes go through the serialised writer (src/database/writer.py); telemetry
(provider_usage, ai_usage) is written by its own short transactions between the
network steps, so nothing competes with a long-held lock any more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..ai.source_hunter import assess_source
from ..config.settings import get_settings
from ..database.base import session_scope
from ..database.writer import run_write
from ..models.discovery import DiscoveryQuery
from ..models.provider import DomainDiscovery
from ..models.source import Source
from ..utils.hashing import domain_of, normalize_url
from ..utils.logging import get_logger
from . import budget
from .classify import is_aggregator_domain  # re-exported for callers/tests
from .providers import get_multi_provider
from .queries import BIG_PLATFORMS, generate_queries, GeneratedQuery

log = get_logger("discovery.engine")


def compute_source_discovery_value(dom: str, assessment, rank: int | None,
                                   page: int | None, was_new: bool) -> int:
    if is_aggregator_domain(dom):
        return 20
    v = assessment.discovery_value or 50
    if assessment.source_type in ("dealer", "suzuki_dealer", "youngtimer_dealer",
                                  "enthusiast_dealer", "garage"):
        v += 15
    if assessment.is_suzuki_dealer:
        v += 10
    if assessment.handles_japanese:
        v += 5
    if rank and rank > 20:
        v += 8
    if page and page > 1:
        v += 5
    if was_new:
        v += 5
    return max(0, min(100, v))


@dataclass
class DiscoverySummary:
    queries_planned: int = 0
    queries_run: int = 0
    providers: list = field(default_factory=list)
    new_domains: int = 0
    domains_assessed: int = 0
    sources_added: int = 0
    per_provider_new: dict = field(default_factory=dict)
    budget_states: dict = field(default_factory=dict)
    skipped_budget: bool = False

    def as_dict(self) -> dict:
        return self.__dict__


def select_queries(gen: list[GeneratedQuery], max_queries: int,
                   recent_hours: int = 20) -> tuple[list[GeneratedQuery], dict]:
    """Rotation with transparency. Prefer fresh + high-yield queries; queries
    run in the last `recent_hours` are held back (and only used to backfill if
    we don't have enough fresh ones). Returns (selected, stats) where stats
    explains planned/skipped_recently/skipped_duplicate (P7)."""
    now = datetime.now(timezone.utc)
    seen: set[tuple[str, str]] = set()
    fresh: list[tuple[float, GeneratedQuery]] = []
    recent: list[tuple[float, GeneratedQuery]] = []
    dup = 0

    with session_scope() as s:
        for gq in gen:
            key = (gq.query.lower(), gq.country)
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            row = (s.query(DiscoveryQuery)
                   .filter(DiscoveryQuery.query == gq.query,
                           DiscoveryQuery.country == gq.country).first())
            score = 0.0
            if gq.high_value:
                score += 3.0
            if gq.family in ("B", "E"):
                score += 2.0
            is_recent = False
            if row is None:
                score += 4.0
            else:
                score += min(5.0, (row.new_domains_found / max(1, row.run_count)) * 2.0)
                if row.last_run_at:
                    last = row.last_run_at
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    hours = (now - last).total_seconds() / 3600
                    score += min(3.0, hours / 24)
                    is_recent = hours < recent_hours
                if row.run_count >= 5 and row.new_domains_found == 0:
                    score -= 3.0
            if gq.experimental:
                score += 0.5
            (recent if is_recent else fresh).append((score, gq))

    fresh.sort(key=lambda t: t[0], reverse=True)
    recent.sort(key=lambda t: t[0], reverse=True)
    selected = [gq for _, gq in fresh[:max_queries]]
    used_recent = 0
    if len(selected) < max_queries:
        need = max_queries - len(selected)
        selected += [gq for _, gq in recent[:need]]
        used_recent = min(need, len(recent))

    stats = {
        "generated": len(gen),
        "unique": len(fresh) + len(recent),
        "skipped_duplicate": dup,
        "skipped_recently": max(0, len(recent) - used_recent),
        "planned": len(selected),
    }
    return selected, stats


class DiscoveryEngine:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.multi = get_multi_provider(self.settings)

    def _existing_domains(self) -> set[str]:
        with session_scope() as session:  # short read, no lock
            return {d for (d,) in session.query(Source.domain).all()}

    def run(self, countries: list[str] | None = None, max_queries: int = 40,
            families: str = "ABCDEFG", experimental_ratio: float = 0.2,
            progress_cb=None, max_hits_per_query: int = 12,
            max_hits_total: int = 150) -> dict:
        from ..crawlers.base import HttpFetcher   # lazy: avoids import cycle
        from .hits import process_search_hit

        gen = generate_queries(countries, families=families,
                               provider=(self.multi.names[0] if self.multi.names
                                         else "generic"),
                               experimental_ratio=experimental_ratio)
        planned, qstats = select_queries(gen, max_queries)
        total = len(planned)

        # Transparent counters (P7 + P1-style discovery metrics).
        m = {"planned": total, "executed": 0,
             "skipped_recently": qstats["skipped_recently"],
             "skipped_duplicate": qstats["skipped_duplicate"],
             "skipped_budget": 0, "skipped_other": 0,
             "providers": self.multi.names, "hits_examined": 0,
             "new_domains": 0, "sources_registered": 0, "listings_ingested": 0,
             "blocked": 0, "hit_types": {}}
        if progress_cb:
            progress_cb(metrics=dict(m), message="discovery starting")

        if not self.multi.available:
            self._record_queries_only(planned)
            m["skipped_other"] = total
            log.info("Discovery: no search provider enabled — recorded %d "
                     "queries only (configure Brave/SerpApi to go live).", total)
            if progress_cb:
                progress_cb(metrics=dict(m), message="no provider configured")
            return {**m, "queries_planned": total, "queries_run": 0}

        known = self._existing_domains()
        new_domains: set[str] = set()
        seen_dom_prov: set[tuple[str, str]] = set()
        processed_urls: set[str] = set()

        with HttpFetcher(self.settings) as fetcher:
            for idx, gq in enumerate(planned, start=1):
                if not any(budget.can_request(p, 1) for p in self.multi.names):
                    m["skipped_budget"] = total - m["executed"]
                    states = {p: budget.budget_state(p) for p in self.multi.names}
                    log.warning("Search budget exhausted at query %d/%d; states=%s",
                                idx, total, states)
                    break

                pages = (self.settings.search_pages_high_value if gq.high_value
                         else self.settings.search_pages_default)
                log.info("Discovery query %d/%d [%s/%s]: %s", idx, total,
                         gq.country, gq.family, gq.query[:60])
                res = self.multi.search(gq.query, pages=pages, country=gq.country,
                                        language=gq.language)
                m["executed"] += 1
                log.info("  results: %d (providers: %s)", len(res.hits),
                         ",".join(self.multi.names))

                domain_rows: list[tuple] = []
                examined_this_query = 0
                for hit in res.hits:
                    dom = domain_of(hit.url)
                    if not dom:
                        continue
                    providers = sorted(
                        res.providers_by_url.get(normalize_url(hit.url), set())) \
                        or [hit.provider]
                    was_new = dom not in known and not is_aggregator_domain(dom)
                    for prov in providers:
                        k = (dom, prov)
                        if k not in seen_dom_prov:
                            seen_dom_prov.add(k)
                            domain_rows.append((dom, prov, hit, was_new))
                    if was_new:
                        new_domains.add(dom)
                        for prov in providers:
                            m.setdefault("per_provider_new", {})
                            m["per_provider_new"][prov] = \
                                m["per_provider_new"].get(prov, 0) + 1

                    # Investigate the EXACT hit URL directly (P2/P3), bounded.
                    nurl = normalize_url(hit.url)
                    if (nurl not in processed_urls
                            and examined_this_query < max_hits_per_query
                            and m["hits_examined"] < max_hits_total):
                        processed_urls.add(nurl)
                        examined_this_query += 1
                        m["hits_examined"] += 1
                        # A single malformed page (odd JSON-LD, broken markup)
                        # must never abort the whole discovery pass — isolate it.
                        try:
                            outcome = process_search_hit(
                                fetcher, hit.url, hit.title, hit.snippet, gq.query,
                                providers, hit.rank, hit.page, gq.country)
                        except Exception as exc:  # noqa: BLE001
                            m["skipped_other"] += 1
                            m["hit_types"]["ERROR"] = m["hit_types"].get("ERROR", 0) + 1
                            log.warning("hit failed %s: %s: %s", hit.url,
                                        type(exc).__name__, exc)
                            continue
                        ht = outcome.get("hit_type", "UNKNOWN")
                        m["hit_types"][ht] = m["hit_types"].get(ht, 0) + 1
                        if outcome.get("ingested"):
                            m["listings_ingested"] += 1
                        if outcome.get("source_registered") and outcome.get("monitorable"):
                            m["sources_registered"] += 1
                        if outcome.get("status") == "BLOCKED":
                            m["blocked"] += 1
                        known.add(dom)

                # Short write: DiscoveryQuery + provider-comparison rows.
                run_write(lambda s, gq=gq, dr=domain_rows,
                          rc=len(res.hits), qn=len([d for d in domain_rows if d[3]]):
                          self._persist_query(s, gq, dr, qn, rc),
                          label="discovery.persist", swallow=True)

                m["new_domains"] = len(new_domains)
                log.info("  new domains: %d | sources registered: %d | "
                         "listings ingested: %d", len(new_domains),
                         m["sources_registered"], m["listings_ingested"])
                if progress_cb:
                    progress_cb(metrics=dict(m),
                                message=f"query {idx}/{total}")

        m["new_domains"] = len(new_domains)
        m["budget_states"] = budget.all_budget_states()
        if progress_cb:
            progress_cb(metrics=dict(m), message="discovery finished")
        result = {**m, "queries_planned": total, "queries_run": m["executed"],
                  "sources_added": m["sources_registered"]}
        log.info("Discovery pass complete: %s", {k: result[k] for k in
                 ("planned", "executed", "skipped_recently", "skipped_budget",
                  "new_domains", "sources_registered", "listings_ingested",
                  "hits_examined", "blocked")})
        return result

    # ------------------------------------------------------------------ #
    def _persist_query(self, session, gq: GeneratedQuery, domain_rows,
                       query_new: int, result_count: int) -> None:
        dq = self._upsert_query(session, gq, result_count)
        for dom, prov, hit, was_new in domain_rows:
            session.add(DomainDiscovery(
                domain=dom, provider=prov, query=gq.query, country=gq.country,
                rank=hit.rank, page=hit.page, url=(hit.url or "")[:1000],
                was_new=was_new))
        dq.new_domains_found += query_new
        dq.effectiveness_score = round(
            0.7 * dq.effectiveness_score + 0.3 * query_new, 3)

    def _upsert_query(self, session, gq: GeneratedQuery, result_count: int
                      ) -> DiscoveryQuery:
        dq = (session.query(DiscoveryQuery)
              .filter(DiscoveryQuery.query == gq.query,
                      DiscoveryQuery.country == gq.country).first())
        if not dq:
            dq = DiscoveryQuery(query=gq.query, country=gq.country,
                                language=gq.language,
                                provider="+".join(self.multi.names) or "none",
                                experimental=gq.experimental)
            session.add(dq)
            session.flush()
        dq.last_run_at = datetime.now(timezone.utc)
        dq.run_count += 1
        dq.result_count = result_count
        return dq

    def _record_queries_only(self, planned: list[GeneratedQuery]) -> None:
        def _do(session):
            for gq in planned:
                self._upsert_query(session, gq, result_count=0)
        run_write(_do, label="discovery.record_queries")
