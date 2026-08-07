"""The Source Discovery Engine.

Runs periodically and asks, in effect: "Where might an Ignis Sport be for sale
today that my system probably doesn't watch yet?" It:
  1. generates multilingual queries (with an experimental slice),
  2. runs them through the configured search provider (or no-op if none),
  3. extracts new domains from the results,
  4. classifies each via the AI Source Hunter (or heuristic fallback),
  5. registers worthwhile ones as new sources and records query effectiveness.

Everything degrades gracefully: with SEARCH_PROVIDER=none it still records the
query catalogue and relies on seeds + sitemap discovery.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..ai.source_hunter import assess_source
from ..database.base import session_scope
from ..models.discovery import DiscoveryQuery
from ..models.source import Source
from ..utils.hashing import domain_of
from ..utils.logging import get_logger
from .providers import get_search_provider
from .queries import generate_queries

log = get_logger("discovery.engine")

# Domains that are already "known giants" — recorded but never treated as a
# high-value new find.
_KNOWN_BIG = {
    "autoscout24.de", "mobile.de", "theparking.eu", "autouncle.com",
    "ebay.com", "ebay.de", "facebook.com", "google.com", "youtube.com",
    "wikipedia.org", "leboncoin.fr", "marktplaats.nl", "autotrader.co.uk",
}


class DiscoveryEngine:
    def __init__(self, settings=None):
        self.provider = get_search_provider(settings)

    def _existing_domains(self, session) -> set[str]:
        return {d for (d,) in session.query(Source.domain).all()}

    def run(self, countries: list[str] | None = None, max_queries: int = 40,
            experimental_ratio: float = 0.2) -> dict:
        """Execute a discovery pass. Returns a summary dict."""
        gen = generate_queries(countries, experimental_ratio=experimental_ratio)
        gen = gen[:max_queries]
        new_domains: set[str] = set()
        assessed = 0
        added_sources = 0

        with session_scope() as session:
            known = self._existing_domains(session)

            for gq in gen:
                dq = (session.query(DiscoveryQuery)
                      .filter(DiscoveryQuery.query == gq.query,
                              DiscoveryQuery.country == gq.country).first())
                if not dq:
                    dq = DiscoveryQuery(query=gq.query, country=gq.country,
                                        language=gq.language,
                                        provider=self.provider.name,
                                        experimental=gq.experimental)
                    session.add(dq)
                    session.flush()

                hits = self.provider.search(gq.query, count=20, country=gq.country)
                dq.last_run_at = datetime.now(timezone.utc)
                dq.run_count += 1
                dq.result_count = len(hits)

                query_new_domains = 0
                for hit in hits:
                    dom = domain_of(hit.url)
                    if not dom or dom in known or dom in _KNOWN_BIG:
                        continue
                    known.add(dom)
                    new_domains.add(dom)
                    query_new_domains += 1

                    # Classify the newly found domain.
                    assessment = assess_source(dom, snippet=hit.snippet,
                                               country=gq.country)
                    assessed += 1
                    if assessment.is_relevant:
                        session.add(Source(
                            domain=dom,
                            name=hit.title[:120] or dom,
                            country=assessment.country or gq.country,
                            language=assessment.language or gq.language,
                            source_type=assessment.source_type,
                            base_url=f"https://{dom}/",
                            discovery_method=f"search:{self.provider.name}",
                            discovery_value=assessment.discovery_value,
                            priority=assessment.priority,
                            parser_type=assessment.recommended_parser,
                            requires_browser=assessment.requires_browser,
                            manual_review=not assessment.automatable,
                            notes=assessment.reasoning[:500],
                        ))
                        added_sources += 1

                dq.new_domains_found += query_new_domains
                # Simple effectiveness: reward queries that surface new domains.
                dq.effectiveness_score = round(
                    0.7 * dq.effectiveness_score + 0.3 * query_new_domains, 3)

        summary = {
            "queries_run": len(gen),
            "provider": self.provider.name,
            "new_domains": len(new_domains),
            "domains_assessed": assessed,
            "sources_added": added_sources,
        }
        log.info("Discovery pass: %s", summary)
        return summary
