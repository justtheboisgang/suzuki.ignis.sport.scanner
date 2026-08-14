"""Phase 17 tests: multi-provider search dedup, budgets, query families,
source discovery value, seller→source expansion and provider comparison."""

from src.discovery import budget
from src.discovery.engine import compute_source_discovery_value, is_aggregator_domain
from src.discovery.providers import MultiProvider, SearchHit, SearchProvider
from src.discovery.queries import generate_queries
from src.ai.schemas import SourceAssessment
from src.models.listing import Listing
from src.models.provider import DomainDiscovery
from src.models.source import Source
from src.pipeline.expansion import (
    expand_seller_to_source,
    is_long_tail_source,
    set_provenance,
)


class _FakeProvider(SearchProvider):
    def __init__(self, name, hits):
        self._name = name
        self._hits = hits

    @property
    def name(self):
        return self._name

    @property
    def available(self):
        return True

    def _search_page(self, query, page, country):
        return list(self._hits)


def test_query_families_cover_mislabelled_chassis_and_exclusion():
    qs = generate_queries(["DE", "IT", "FR"], seed=7)
    fams = {q.family for q in qs}
    assert fams == set("ABCDEFG")
    # FAMILY B — mislabelled hunt with local power spellings.
    assert any("109 cv" in q.query for q in qs)
    assert any("80 kW" in q.query for q in qs)
    # FAMILY E — chassis code, high value.
    assert any(q.query == "HT81S" and q.high_value for q in qs)
    # FAMILY D — big-platform exclusion.
    assert any("-autoscout24" in q.query or "-site:autoscout24.de" in q.query
               for q in qs)
    # High-value families flagged.
    assert any(q.high_value for q in qs if q.family in "ABE")


def test_discovery_isolates_a_single_bad_hit(session, monkeypatch):
    """Regression: one malformed page (e.g. odd JSON-LD) raised inside
    process_search_hit and aborted the ENTIRE discovery run at query N/M. The
    engine must now isolate per-hit failures and keep going."""
    from src.discovery import engine as eng
    from src.discovery import hits as hits_mod

    hit = SearchHit(title="Suzuki Ignis Sport",
                    url="https://tiny-dealer.de/ignis-sport-1")
    e = eng.DiscoveryEngine()
    e.multi = MultiProvider([_FakeProvider("brave", [hit])])

    def boom(*a, **k):
        raise AttributeError("'list' object has no attribute 'get'")
    monkeypatch.setattr(hits_mod, "process_search_hit", boom)

    # Must NOT raise; the run completes and records the failed hit transparently.
    result = e.run(countries=["DE"], max_queries=1, max_hits_per_query=5)
    assert result["executed"] >= 1
    assert result["hit_types"].get("ERROR", 0) >= 1


def test_multiprovider_dedup_and_provenance(session):
    # `session` fixture ensures the ProviderUsage rows written by the fake
    # providers are cleaned up so they don't leak into budget tests.
    common = SearchHit(title="Autohaus X", url="https://autohaus-x.de/")
    brave = _FakeProvider("brave", [common,
                                    SearchHit(title="only brave",
                                              url="https://brave-only.de/")])
    serp = _FakeProvider("serpapi", [SearchHit(title="Autohaus X",
                                               url="https://autohaus-x.de/"),
                                     SearchHit(title="only serp",
                                               url="https://serp-only.it/")])
    multi = MultiProvider([brave, serp])
    res = multi.search("Suzuki Ignis Sport", pages=1)
    urls = {h.domain for h in res.hits}
    assert urls == {"autohaus-x.de", "brave-only.de", "serp-only.it"}
    # The shared domain must record BOTH providers.
    from src.utils.hashing import normalize_url
    provs = res.providers_by_url[normalize_url("https://autohaus-x.de/")]
    assert provs == {"brave", "serpapi"}


def test_budget_math(session):
    # No budget configured for a made-up provider -> unlimited.
    assert budget.can_request("nonexistent", 1) is True
    # Record requests for brave and check counting/state.
    for _ in range(3):
        budget.record_request("brave", "q", "DE", 1, 5)
    assert budget.provider_request_count("brave") == 3
    st = budget.budget_state("brave")
    assert st["used"] == 3
    assert st["budget"] >= 1  # default brave budget > 0


def test_source_discovery_value_prefers_long_tail():
    dealer = SourceAssessment(is_relevant=True, source_type="dealer",
                              is_suzuki_dealer=True, handles_japanese=True,
                              discovery_value=60)
    agg = SourceAssessment(is_relevant=True, source_type="aggregator",
                           discovery_value=60)
    dv_dealer = compute_source_discovery_value("kleiner-haendler.pl", dealer,
                                               rank=45, page=3, was_new=True)
    dv_agg = compute_source_discovery_value("autoscout24.de", agg, rank=1,
                                            page=1, was_new=False)
    assert dv_dealer > dv_agg
    assert is_aggregator_domain("autoscout24.de")
    assert not is_aggregator_domain("kleiner-haendler.pl")


def test_seller_to_source_expansion(session):
    # A listing found on an aggregator whose description reveals the dealer site.
    agg = Source(domain="autoscout24.de", source_type="major_marketplace",
                 base_url="https://autoscout24.de/", is_aggregator=True,
                 discovery_method="seed")
    session.add(agg)
    session.flush()
    lst = Listing(internal_id="p1", listing_url="https://autoscout24.de/x",
                  vehicle_match_confidence=90, country="IT",
                  seller_name="Autosalone Rossi",
                  description_original="Contattaci su https://autosalone-rossi.it "
                                       "per il nostro parco auto.")
    session.add(lst)
    session.flush()

    set_provenance(lst, agg)
    assert lst.aggregator == "autoscout24.de"
    assert lst.is_long_tail is False  # came from a big platform

    dealer = expand_seller_to_source(session, lst, agg)
    session.commit()
    assert dealer == "autosalone-rossi.it"
    assert lst.dealer_domain == "autosalone-rossi.it"
    # The dealer domain must now be a monitorable, seller-derived source.
    src = session.query(Source).filter(Source.domain == "autosalone-rossi.it").one()
    assert src.seller_derived is True
    assert src.source_type == "dealer"


def test_is_long_tail_source():
    dealer = Source(domain="x-garage.fr", source_type="dealer", discovery_value=70)
    agg = Source(domain="autoscout24.de", source_type="major_marketplace",
                 discovery_value=30)
    assert is_long_tail_source(dealer) is True
    assert is_long_tail_source(agg) is False


def test_provider_comparison(session):
    for dom, prov in [("a.de", "brave"), ("a.de", "serpapi"),  # overlap
                      ("b.de", "brave"),                          # brave only
                      ("c.it", "serpapi")]:                       # serp only
        session.add(DomainDiscovery(domain=dom, provider=prov, query="q"))
    session.commit()
    from src.pipeline.reporting import provider_comparison
    cmp = provider_comparison()
    assert cmp["overlap_domains"] == 1
    assert cmp["brave_unique_domains"] == 1
    assert cmp["serpapi_unique_domains"] == 1
    assert cmp["total_domains_seen"] == 3
