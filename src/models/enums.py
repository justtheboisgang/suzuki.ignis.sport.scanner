"""Enumerations shared across models. Stored as plain strings for portability
between SQLite and PostgreSQL."""

from __future__ import annotations

from enum import Enum


class SourceType(str, Enum):
    MAJOR_MARKETPLACE = "major_marketplace"
    LOCAL_MARKETPLACE = "local_marketplace"
    CLASSIFIEDS = "classifieds"
    DEALER = "dealer"
    SUZUKI_DEALER = "suzuki_dealer"
    GARAGE = "garage"
    YOUNGTIMER_DEALER = "youngtimer_dealer"
    ENTHUSIAST_DEALER = "enthusiast_dealer"
    FORUM = "forum"
    CLUB = "club"
    COMMUNITY = "community"
    AUCTION = "auction"
    AGGREGATOR = "aggregator"
    PRIVATE_SITE = "private_site"
    DEALER_NETWORK = "dealer_network"
    SEARCH_ENGINE_DISCOVERY = "search_engine_discovery"
    OTHER = "other"


class ListingStatus(str, Enum):
    ACTIVE = "ACTIVE"
    MAYBE_ACTIVE = "MAYBE_ACTIVE"
    SOLD = "SOLD"
    EXPIRED = "EXPIRED"
    REMOVED = "REMOVED"
    ARCHIVED = "ARCHIVED"
    UNKNOWN = "UNKNOWN"


class SellerType(str, Enum):
    DEALER = "dealer"
    PRIVATE = "private"
    UNKNOWN = "unknown"


class Classification(str, Enum):
    CONFIRMED_IGNIS_SPORT = "CONFIRMED_IGNIS_SPORT"
    LIKELY_IGNIS_SPORT = "LIKELY_IGNIS_SPORT"
    POSSIBLE_IGNIS_SPORT = "POSSIBLE_IGNIS_SPORT"
    MISLABELLED_SPORT_CANDIDATE = "MISLABELLED_SPORT_CANDIDATE"
    UNCERTAIN = "UNCERTAIN"
    LIKELY_NOT_SPORT = "LIKELY_NOT_SPORT"
    NORMAL_IGNIS = "NORMAL_IGNIS"
    NOT_IGNIS = "NOT_IGNIS"


class Provenance(str, Enum):
    """Where a piece of data came from — so nothing inferred is ever presented
    as a hard fact."""

    FACT = "FACT"                  # parsed directly from the listing
    INFERRED = "INFERRED"          # deterministic inference from facts
    AI_INFERENCE = "AI_INFERENCE"  # produced by the Claude layer
    UNKNOWN = "UNKNOWN"


class SourceLiveStatus(str, Enum):
    """Ground-truth status of a source after a real fetch attempt. A seed
    source is never 'WORKING' just because it's on a static list."""

    WORKING = "WORKING"
    PARTIALLY_WORKING = "PARTIALLY_WORKING"
    BLOCKED = "BLOCKED"
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    JS_REQUIRED = "JS_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    PARSER_BROKEN = "PARSER_BROKEN"
    DEAD = "DEAD"
    UNKNOWN = "UNKNOWN"


class FeedbackLabel(str, Enum):
    CONFIRMED_SPORT = "CONFIRMED_SPORT"
    NOT_SPORT = "NOT_SPORT"
    INTERESTING = "INTERESTING"
    NOT_INTERESTING = "NOT_INTERESTING"
    BOUGHT_SOLD = "BOUGHT_SOLD"
    IGNORE_SELLER = "IGNORE_SELLER"
