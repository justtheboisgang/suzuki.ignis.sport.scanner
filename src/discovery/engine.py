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
from .providers import get_multi_provider
from .queries import BIG_PLATFORMS, generate_queries, GeneratedQuery

log = get_logger("discovery.engine")

_KNOWN_BIG = {
    "autoscout24.de", "autoscout24.com", "mobile.de", "theparking.eu",
    "autouncle.com", "ebay.com", "ebay.de", "facebook.com", "google.com",
    "youtube.com", "wikipedia.org", "leboncoin.fr", "marktplaats.nl",
    "autotrader.co.uk", "instagram.com", "pinterest.com", "x.com", "twitter.com",
    "reddit.com", "amazon.com", "gumtree.com",
}


def is_aggregator_domain(dom: str) -> bool:
    return any(b in dom for b in BIG_PLATFORMS) or dom in _KNOWN_BIG


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


def select_queries(gen: list[GeneratedQuery], max_queries: int) -> list[GeneratedQuery]:
    """Rotation: rank by (high-value, historical yield, staleness). Read-only."""
    now = datetime.now(timezone.utc)
    scored: list[tuple[float, GeneratedQuery]] = []
    with session_scope() as s:
        for gq in gen:
            row = (s.query(DiscoveryQuery)
                   .filter(DiscoveryQuery.query == gq.query,
                           DiscoveryQuery.country == gq.country).first())
            score = 0.0
            if gq.high_value:
                score += 3.0
            if gq.family in ("B", "E"):
                score += 2.0
            if row is None:
                score += 4.0
            else:
                yield_rate = row.new_domains_found / max(1, row.run_count)
                score += min(5.0, yield_rate * 2.0)
                if row.last_run_at:
                    last = row.last_run_at
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    hours = (now - last).total_seconds() / 3600
                    score += min(3.0, hours / 24)
                    if hours < 20:
                        score -= 5.0
                if row.run_count >= 5 and row.new_domains_found == 0:
                    score -= 3.0
            if gq.experimental:
                score += 0.5
            scored.append((score, gq))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [gq for _, gq in scored[:max_queries]]


class DiscoveryEngine:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.multi = get_multi_provider(self.settings)

    def _existing_domains(self) -> set[str]:
        with session_scope() as session:  # short read, no lock
            return {d for (d,) in session.query(Source.domain).all()}

    def run(self, countries: list[str] | None = None, max_queries: int = 40,
            families: str = "ABCDEFG", experimental_ratio: float = 0.2,
            progress_cb=None) -> dict:
        summary = DiscoverySummary(providers=self.multi.names)
        summary.budget_states = budget.all_budget_states()

        gen = generate_queries(countries, families=families,
                               provider=(self.multi.names[0] if self.multi.names
                                         else "generic"),
                               experimental_ratio=experimental_ratio)
        planned = select_queries(gen, max_queries)
        summary.queries_planned = len(planned)
        total = len(planned)
        if progress_cb:
            progress_cb(queries_total=total, queries_done=0, new_domains=0,
                        sources_added=0)

        if not self.multi.available:
            self._record_queries_only(planned)
            log.info("Discovery: no search provider enabled — recorded %d "
                     "queries only (configure Brave/SerpApi to go live).", total)
            return summary.as_dict()

        known = self._existing_domains()
        new_domains: set[str] = set()
        seen_dom_prov: set[tuple[str, str]] = set()

        for idx, gq in enumerate(planned, start=1):
            if not any(budget.can_request(p, 1) for p in self.multi.names):
                summary.skipped_budget = True
                log.warning("All search budgets exhausted; stopping discovery at "
                            "query %d/%d.", idx, total)
                break

            pages = (self.settings.search_pages_high_value if gq.high_value
                     else self.settings.search_pages_default)
            log.info("Discovery query %d/%d [%s/%s]: %s", idx, total, gq.country,
                     gq.family, gq.query[:60])

            # 1) NETWORK: provider search (provider_usage written by short txns).
            res = self.multi.search(gq.query, pages=pages, country=gq.country,
                                    language=gq.language)
            summary.queries_run += 1
            log.info("  results: %d (providers: %s)", len(res.hits),
                     ",".join(self.multi.names))

            # 2) NETWORK: classify new domains — NO DB txn open here.
            domain_rows: list[tuple] = []
            sources_to_add: list[tuple] = []
            query_new = 0
            for hit in res.hits:
                dom = domain_of(hit.url)
                if not dom:
                    continue
                providers = sorted(
                    res.providers_by_url.get(normalize_url(hit.url), set())) \
                    or [hit.provider]
                was_new = dom not in known and not is_aggregator_domain(dom)
                for prov in providers:
                    key = (dom, prov)
                    if key not in seen_dom_prov:
                        seen_dom_prov.add(key)
                        domain_rows.append((dom, prov, hit, was_new))
                if dom in known or is_aggregator_domain(dom):
                    continue
                known.add(dom)
                new_domains.add(dom)
                query_new += 1
                for prov in providers:
                    summary.per_provider_new[prov] = \
                        summary.per_provider_new.get(prov, 0) + 1
                assessment = assess_source(dom, snippet=hit.snippet,
                                           country=gq.country)  # network
                summary.domains_assessed += 1
                if assessment.is_relevant:
                    dv = compute_source_discovery_value(dom, assessment, hit.rank,
                                                        hit.page, was_new)
                    sources_to_add.append((dom, hit, providers, assessment, dv))
                    summary.sources_added += 1

            # 3) SHORT WRITE TXN: persist everything for this query atomically.
            result_count = len(res.hits)
            run_write(lambda s, gq=gq, dr=domain_rows, sa=sources_to_add,
                      qn=query_new, rc=result_count:
                      self._persist_query(s, gq, dr, sa, qn, rc),
                      label="discovery.persist")

            log.info("  new domains so far: %d | sources added: %d",
                     len(new_domains), summary.sources_added)
            if progress_cb:
                progress_cb(queries_done=idx, new_domains=len(new_domains),
                            sources_added=summary.sources_added,
                            results=summary.queries_run)

        summary.new_domains = len(new_domains)
        summary.budget_states = budget.all_budget_states()
        log.info("Discovery pass complete: %s", summary.as_dict())
        return summary.as_dict()

    # ------------------------------------------------------------------ #
    def _persist_query(self, session, gq: GeneratedQuery, domain_rows, sources_to_add,
                       query_new: int, result_count: int) -> None:
        dq = self._upsert_query(session, gq, result_count)
        for dom, prov, hit, was_new in domain_rows:
            session.add(DomainDiscovery(
                domain=dom, provider=prov, query=gq.query, country=gq.country,
                rank=hit.rank, page=hit.page, url=(hit.url or "")[:1000],
                was_new=was_new))
        for dom, hit, providers, assessment, dv in sources_to_add:
            # Guard against a concurrent run having added it meanwhile.
            if session.query(Source.id).filter(Source.domain == dom).first():
                continue
            session.add(Source(
                domain=dom, name=(hit.title[:120] or dom),
                country=assessment.country or gq.country,
                language=assessment.language or gq.language,
                source_type=assessment.source_type, base_url=f"https://{dom}/",
                discovery_method=f"search:{'+'.join(providers)}",
                discovery_value=dv, priority=assessment.priority,
                parser_type=assessment.recommended_parser,
                requires_browser=assessment.requires_browser,
                manual_review=not assessment.automatable,
                notes=assessment.reasoning[:500], discovered_by=providers,
                first_provider=providers[0], discovery_query=gq.query,
                search_rank=hit.rank, search_page=hit.page,
                is_aggregator=is_aggregator_domain(dom)))
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
