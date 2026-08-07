"""European market reference data: countries, primary language, currency, TLD,
and localized "for sale / used" search phrases used by the discovery engine.

Coverage is intentionally broad. The system reports exactly which countries it
searches — this table is that ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Country:
    code: str            # ISO-3166 alpha-2
    name_en: str
    language: str        # primary language code
    currency: str
    tld: str
    # Localized phrases meaning roughly "for sale", "used", "buy".
    for_sale: tuple[str, ...]
    used: tuple[str, ...]
    dealer: tuple[str, ...]


COUNTRIES: dict[str, Country] = {
    "DE": Country("DE", "Germany", "de", "EUR", ".de",
                  ("zu verkaufen", "kaufen", "zum verkauf"),
                  ("gebraucht", "gebrauchtwagen"), ("autohaus", "händler")),
    "AT": Country("AT", "Austria", "de", "EUR", ".at",
                  ("zu verkaufen", "kaufen"), ("gebraucht", "gebrauchtwagen"),
                  ("autohaus", "händler")),
    "CH": Country("CH", "Switzerland", "de", "CHF", ".ch",
                  ("zu verkaufen", "kaufen", "occasion"), ("gebraucht", "occasion"),
                  ("garage", "händler")),
    "FR": Country("FR", "France", "fr", "EUR", ".fr",
                  ("à vendre", "occasion"), ("occasion", "d'occasion"),
                  ("garage", "concessionnaire")),
    "BE": Country("BE", "Belgium", "nl", "EUR", ".be",
                  ("te koop", "à vendre"), ("tweedehands", "occasion"),
                  ("garage", "dealer")),
    "NL": Country("NL", "Netherlands", "nl", "EUR", ".nl",
                  ("te koop", "kopen"), ("tweedehands", "gebruikt", "occasion"),
                  ("dealer", "garage", "autobedrijf")),
    "LU": Country("LU", "Luxembourg", "fr", "EUR", ".lu",
                  ("à vendre", "occasion"), ("occasion",), ("garage",)),
    "IT": Country("IT", "Italy", "it", "EUR", ".it",
                  ("in vendita", "vendita"), ("usata", "usato", "km0"),
                  ("concessionaria", "autosalone")),
    "ES": Country("ES", "Spain", "es", "EUR", ".es",
                  ("en venta", "venta"), ("segunda mano", "ocasión", "usado"),
                  ("concesionario", "taller")),
    "PT": Country("PT", "Portugal", "pt", "EUR", ".pt",
                  ("à venda", "venda"), ("usado", "usada", "segunda mão"),
                  ("stand", "concessionário")),
    "PL": Country("PL", "Poland", "pl", "PLN", ".pl",
                  ("na sprzedaż", "sprzedam"), ("używany", "używane"),
                  ("komis", "dealer")),
    "CZ": Country("CZ", "Czechia", "cs", "CZK", ".cz",
                  ("na prodej", "prodej"), ("ojeté", "ojetý"),
                  ("autobazar", "autosalon")),
    "SK": Country("SK", "Slovakia", "sk", "EUR", ".sk",
                  ("na predaj", "predaj"), ("ojazdené", "jazdené"),
                  ("autobazár", "autosalón")),
    "HU": Country("HU", "Hungary", "hu", "HUF", ".hu",
                  ("eladó",), ("használt",), ("autókereskedés", "autószalon")),
    "SI": Country("SI", "Slovenia", "sl", "EUR", ".si",
                  ("naprodaj", "prodam"), ("rabljeno", "rabljen"),
                  ("avtohiša", "prodajalec")),
    "HR": Country("HR", "Croatia", "hr", "EUR", ".hr",
                  ("na prodaju", "prodajem"), ("rabljeno", "polovni"),
                  ("autokuća", "trgovac")),
    "RO": Country("RO", "Romania", "ro", "RON", ".ro",
                  ("de vânzare", "vând"), ("second hand", "rulat"),
                  ("dealer", "parc auto")),
    "BG": Country("BG", "Bulgaria", "bg", "BGN", ".bg",
                  ("за продажба", "продава"), ("употребяван", "втора употреба"),
                  ("автокъща", "дилър")),
    "GR": Country("GR", "Greece", "el", "EUR", ".gr",
                  ("πωλείται", "προς πώληση"), ("μεταχειρισμένο", "μεταχειρισμένα"),
                  ("αντιπροσωπεία", "έμπορος")),
    "DK": Country("DK", "Denmark", "da", "DKK", ".dk",
                  ("til salg", "sælges"), ("brugt", "brugtbil"),
                  ("forhandler", "bilhus")),
    "SE": Country("SE", "Sweden", "sv", "SEK", ".se",
                  ("till salu", "säljes"), ("begagnad", "begagnat"),
                  ("bilhandlare", "handlare")),
    "NO": Country("NO", "Norway", "no", "NOK", ".no",
                  ("til salgs", "selges"), ("bruktbil", "brukt"),
                  ("forhandler", "bilforhandler")),
    "FI": Country("FI", "Finland", "fi", "EUR", ".fi",
                  ("myydään", "myytävänä"), ("käytetty", "vaihtoauto"),
                  ("autoliike", "jälleenmyyjä")),
    "EE": Country("EE", "Estonia", "et", "EUR", ".ee",
                  ("müüa", "müügiks"), ("kasutatud",), ("autode müük", "esindus")),
    "LV": Country("LV", "Latvia", "lv", "EUR", ".lv",
                  ("pārdod", "pārdošanā"), ("lietots", "lietota"),
                  ("auto tirdzniecība", "dīleris")),
    "LT": Country("LT", "Lithuania", "lt", "EUR", ".lt",
                  ("parduodama", "parduoda"), ("naudotas", "naudoti"),
                  ("autosalonas", "prekyba")),
    "IE": Country("IE", "Ireland", "en", "EUR", ".ie",
                  ("for sale",), ("used", "second hand"), ("dealer", "garage")),
    "GB": Country("GB", "United Kingdom", "en", "GBP", ".uk",
                  ("for sale",), ("used", "second hand"), ("dealer", "garage")),
}

# The priority markets the target vehicle is most likely to surface in.
PRIORITY_COUNTRIES = ("DE", "AT", "CH", "NL", "BE", "FR", "IT", "ES", "GB")


def all_countries() -> list[Country]:
    return list(COUNTRIES.values())


def get_country(code: str) -> Country | None:
    return COUNTRIES.get(code.upper())
