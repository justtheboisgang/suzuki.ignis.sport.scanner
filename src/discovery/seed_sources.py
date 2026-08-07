"""Initial Europe-wide source inventory.

A broad starting list across marketplaces, national classifieds, aggregators,
enthusiast/youngtimer channels, forums and clubs. This is the *floor*, not the
ceiling — the discovery engine keeps expanding it. Big aggregators are included
as discovery sources but carry a LOW discovery_value: the real prize is the
long-tail dealers found from here.

`search_url` uses each site's public used-Ignis search where a stable query URL
is known; otherwise it's left None and the crawler falls back to base_url +
sitemap. Everything is fetched politely and subject to robots.txt at crawl time.
"""

from __future__ import annotations

from ..database.base import session_scope
from ..models.source import Source
from ..models.enums import SourceType
from ..utils.hashing import domain_of
from ..utils.logging import get_logger

log = get_logger("discovery.seed")


def S(domain, name, country, stype, base=None, search=None, dv=50, prio=50,
      parser="html_generic", lang=None):
    return dict(domain=domain, name=name, country=country, source_type=stype,
                base_url=base or f"https://{domain}/",
                search_url=search, discovery_value=dv, priority=prio,
                parser_type=parser, language=lang)


# ---------------------------------------------------------------------------
# Aggregators / pan-European (discovery entry points; low discovery_value).
# ---------------------------------------------------------------------------
AGGREGATORS = [
    S("theparking.eu", "TheParking", None, SourceType.AGGREGATOR.value,
      search="https://www.theparking.eu/used-cars/used-cars/Suzuki_Ignis.html",
      dv=25, prio=60),
    S("autouncle.com", "AutoUncle", None, SourceType.AGGREGATOR.value,
      search="https://www.autouncle.com/en/used-cars/Suzuki/Ignis", dv=25, prio=55),
    S("carsonice.com", "Cars On Ice", None, SourceType.AGGREGATOR.value, dv=30, prio=40),
]

# ---------------------------------------------------------------------------
# Major national marketplaces (broad reach, modest discovery value).
# ---------------------------------------------------------------------------
MARKETPLACES = [
    # DACH
    S("mobile.de", "mobile.de", "DE", SourceType.MAJOR_MARKETPLACE.value,
      search="https://suchen.mobile.de/fahrzeuge/search.html?makeModelVariant1.makeId=24900&makeModelVariant1.modelId=25&vehicleCategory=Car",
      dv=30, prio=70, lang="de"),
    S("autoscout24.de", "AutoScout24 DE", "DE", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.autoscout24.de/lst/suzuki/ignis", dv=30, prio=70, lang="de"),
    S("kleinanzeigen.de", "Kleinanzeigen", "DE", SourceType.CLASSIFIEDS.value,
      search="https://www.kleinanzeigen.de/s-autos/suzuki-ignis/k0c216", dv=55, prio=65, lang="de"),
    S("autoscout24.at", "AutoScout24 AT", "AT", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.autoscout24.at/lst/suzuki/ignis", dv=35, prio=60, lang="de"),
    S("willhaben.at", "willhaben", "AT", SourceType.CLASSIFIEDS.value,
      search="https://www.willhaben.at/iad/gebrauchtwagen/auto/suzuki/ignis", dv=55, prio=60, lang="de"),
    S("autoscout24.ch", "AutoScout24 CH", "CH", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.autoscout24.ch/de/autos/suzuki--ignis", dv=35, prio=55, lang="de"),
    S("tutti.ch", "tutti.ch", "CH", SourceType.CLASSIFIEDS.value, dv=55, prio=45, lang="de"),
    S("comparis.ch", "Comparis", "CH", SourceType.MAJOR_MARKETPLACE.value, dv=35, prio=45, lang="de"),
    # France / Benelux
    S("lacentrale.fr", "La Centrale", "FR", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.lacentrale.fr/listing?makesModelsCommercialNames=SUZUKI%3AIGNIS", dv=35, prio=65, lang="fr"),
    S("leboncoin.fr", "Leboncoin", "FR", SourceType.CLASSIFIEDS.value,
      search="https://www.leboncoin.fr/recherche?category=2&text=suzuki%20ignis%20sport", dv=60, prio=65, lang="fr"),
    S("largus.fr", "L'argus", "FR", SourceType.MAJOR_MARKETPLACE.value, dv=35, prio=45, lang="fr"),
    S("autoscout24.fr", "AutoScout24 FR", "FR", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.autoscout24.fr/lst/suzuki/ignis", dv=35, prio=55, lang="fr"),
    S("2ememain.be", "2ememain", "BE", SourceType.CLASSIFIEDS.value, dv=55, prio=50, lang="fr"),
    S("autoscout24.be", "AutoScout24 BE", "BE", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.autoscout24.be/nl/lst/suzuki/ignis", dv=35, prio=50, lang="nl"),
    S("marktplaats.nl", "Marktplaats", "NL", SourceType.CLASSIFIEDS.value,
      search="https://www.marktplaats.nl/l/auto-s/suzuki/#q:ignis+sport", dv=60, prio=65, lang="nl"),
    S("autotrack.nl", "AutoTrack", "NL", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.autotrack.nl/aanbod/suzuki/ignis", dv=40, prio=50, lang="nl"),
    S("gaspedaal.nl", "Gaspedaal", "NL", SourceType.AGGREGATOR.value,
      search="https://www.gaspedaal.nl/suzuki/ignis", dv=40, prio=50, lang="nl"),
    S("automobile.lu", "automobile.lu", "LU", SourceType.MAJOR_MARKETPLACE.value, dv=45, prio=40, lang="fr"),
    # Southern Europe
    S("autoscout24.it", "AutoScout24 IT", "IT", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.autoscout24.it/lst/suzuki/ignis", dv=35, prio=60, lang="it"),
    S("subito.it", "Subito", "IT", SourceType.CLASSIFIEDS.value,
      search="https://www.subito.it/annunci-italia/vendita/auto/?q=suzuki+ignis+sport", dv=60, prio=60, lang="it"),
    S("automobile.it", "Automobile.it", "IT", SourceType.MAJOR_MARKETPLACE.value, dv=35, prio=45, lang="it"),
    S("coches.net", "Coches.net", "ES", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.coches.net/suzuki-ignis-segunda-mano/", dv=35, prio=60, lang="es"),
    S("milanuncios.com", "Milanuncios", "ES", SourceType.CLASSIFIEDS.value,
      search="https://www.milanuncios.com/coches-de-segunda-mano/suzuki-ignis.htm", dv=60, prio=55, lang="es"),
    S("wallapop.com", "Wallapop", "ES", SourceType.CLASSIFIEDS.value, dv=55, prio=45, lang="es"),
    S("standvirtual.com", "StandVirtual", "PT", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.standvirtual.com/carros/suzuki/ignis", dv=45, prio=55, lang="pt"),
    S("olx.pt", "OLX Portugal", "PT", SourceType.CLASSIFIEDS.value, dv=55, prio=45, lang="pt"),
    # Central / Eastern Europe
    S("otomoto.pl", "Otomoto", "PL", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.otomoto.pl/osobowe/suzuki/ignis", dv=45, prio=60, lang="pl"),
    S("olx.pl", "OLX Poland", "PL", SourceType.CLASSIFIEDS.value,
      search="https://www.olx.pl/motoryzacja/samochody/suzuki/q-ignis-sport/", dv=60, prio=55, lang="pl"),
    S("sauto.cz", "Sauto.cz", "CZ", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.sauto.cz/inzerce/osobni/suzuki/ignis", dv=45, prio=55, lang="cs"),
    S("tipcars.com", "TipCars", "CZ", SourceType.MAJOR_MARKETPLACE.value, dv=45, prio=45, lang="cs"),
    S("autobazar.eu", "Autobazar.eu", "SK", SourceType.MAJOR_MARKETPLACE.value, dv=45, prio=45, lang="sk"),
    S("hasznaltauto.hu", "Használtautó", "HU", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.hasznaltauto.hu/talalatilista/PCOG2VG3R3RDAI...suzuki/ignis", dv=45, prio=55, lang="hu"),
    S("avto.net", "Avto.net", "SI", SourceType.MAJOR_MARKETPLACE.value, dv=45, prio=45, lang="sl"),
    S("njuskalo.hr", "Njuškalo", "HR", SourceType.CLASSIFIEDS.value, dv=55, prio=45, lang="hr"),
    S("autovit.ro", "Autovit", "RO", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.autovit.ro/autoturisme/suzuki/ignis", dv=45, prio=50, lang="ro"),
    S("olx.ro", "OLX Romania", "RO", SourceType.CLASSIFIEDS.value, dv=55, prio=45, lang="ro"),
    S("mobile.bg", "Mobile.bg", "BG", SourceType.MAJOR_MARKETPLACE.value, dv=45, prio=45, lang="bg"),
    S("car.gr", "Car.gr", "GR", SourceType.MAJOR_MARKETPLACE.value, dv=45, prio=45, lang="el"),
    # Nordics + Baltics + British Isles
    S("bilbasen.dk", "Bilbasen", "DK", SourceType.MAJOR_MARKETPLACE.value, dv=40, prio=50, lang="da"),
    S("dba.dk", "DBA", "DK", SourceType.CLASSIFIEDS.value, dv=55, prio=45, lang="da"),
    S("blocket.se", "Blocket", "SE", SourceType.CLASSIFIEDS.value,
      search="https://www.blocket.se/annonser/hela_sverige/fordon/bilar?q=suzuki%20ignis", dv=60, prio=50, lang="sv"),
    S("bytbil.com", "Bytbil", "SE", SourceType.MAJOR_MARKETPLACE.value, dv=40, prio=45, lang="sv"),
    S("finn.no", "Finn.no", "NO", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.finn.no/car/used/search.html?q=suzuki%20ignis", dv=45, prio=50, lang="no"),
    S("nettiauto.com", "Nettiauto", "FI", SourceType.MAJOR_MARKETPLACE.value, dv=45, prio=50, lang="fi"),
    S("tori.fi", "Tori.fi", "FI", SourceType.CLASSIFIEDS.value, dv=55, prio=45, lang="fi"),
    S("auto24.ee", "Auto24", "EE", SourceType.MAJOR_MARKETPLACE.value, dv=45, prio=45, lang="et"),
    S("ss.com", "SS.com", "LV", SourceType.CLASSIFIEDS.value, dv=50, prio=40, lang="lv"),
    S("autoplius.lt", "Autoplius", "LT", SourceType.MAJOR_MARKETPLACE.value, dv=45, prio=45, lang="lt"),
    S("donedeal.ie", "DoneDeal", "IE", SourceType.CLASSIFIEDS.value,
      search="https://www.donedeal.ie/cars/Suzuki/Ignis", dv=55, prio=50, lang="en"),
    S("carzone.ie", "Carzone", "IE", SourceType.MAJOR_MARKETPLACE.value, dv=40, prio=45, lang="en"),
    S("autotrader.co.uk", "Auto Trader UK", "GB", SourceType.MAJOR_MARKETPLACE.value,
      search="https://www.autotrader.co.uk/car-search?make=SUZUKI&model=IGNIS", dv=35, prio=60, lang="en"),
    S("pistonheads.com", "PistonHeads", "GB", SourceType.ENTHUSIAST_DEALER.value,
      search="https://www.pistonheads.com/classifieds?Category=used-cars&M=1&Model=Ignis", dv=55, prio=50, lang="en"),
    S("gumtree.com", "Gumtree", "GB", SourceType.CLASSIFIEDS.value, dv=55, prio=45, lang="en"),
    S("carandclassic.com", "Car & Classic", "GB", SourceType.YOUNGTIMER_DEALER.value,
      search="https://www.carandclassic.com/search?q=suzuki%20ignis%20sport", dv=65, prio=55, lang="en"),
]

# ---------------------------------------------------------------------------
# Enthusiast / youngtimer / forum / club — the highest discovery value.
# ---------------------------------------------------------------------------
ENTHUSIAST = [
    S("classic-trader.com", "Classic Trader", None, SourceType.YOUNGTIMER_DEALER.value,
      search="https://www.classic-trader.com/uk/cars/search?q=suzuki+ignis", dv=65, prio=50),
    S("oldtimer-markt.de", "Oldtimer Markt", "DE", SourceType.YOUNGTIMER_DEALER.value, dv=60, prio=45, lang="de"),
    S("suzukiignisclub.co.uk", "Suzuki Ignis Club UK", "GB", SourceType.CLUB.value, dv=80, prio=60, lang="en"),
    S("suzuki-forum.de", "Suzuki Forum DE", "DE", SourceType.FORUM.value, dv=75, prio=55, lang="de"),
    S("suzukiclub.de", "Suzuki Club DE", "DE", SourceType.CLUB.value, dv=75, prio=55, lang="de"),
    S("ignisforum.com", "Ignis Forum", None, SourceType.FORUM.value, dv=80, prio=55),
    S("clubalpine.com", "JDM/kei enthusiast index", None, SourceType.COMMUNITY.value, dv=55, prio=35),
    S("japanclassic.eu", "Japan Classic EU", None, SourceType.YOUNGTIMER_DEALER.value, dv=70, prio=45),
]

SEED_SOURCES = AGGREGATORS + MARKETPLACES + ENTHUSIAST


def seed_sources_into_db() -> int:
    """Insert any seed sources not already present. Returns number added."""
    added = 0
    with session_scope() as s:
        for spec in SEED_SOURCES:
            dom = domain_of(spec["domain"]) or spec["domain"]
            exists = s.query(Source).filter(Source.domain == dom).first()
            if exists:
                continue
            s.add(Source(
                domain=dom,
                name=spec["name"],
                country=spec["country"],
                language=spec.get("language"),
                source_type=spec["source_type"],
                base_url=spec["base_url"],
                search_url=spec.get("search_url"),
                discovery_method="seed_list",
                discovery_value=spec["discovery_value"],
                priority=spec["priority"],
                parser_type=spec.get("parser_type", "html_generic"),
                check_frequency="6h",
            ))
            added += 1
    log.info("Seeded %d new sources (of %d in list)", added, len(SEED_SOURCES))
    return added
