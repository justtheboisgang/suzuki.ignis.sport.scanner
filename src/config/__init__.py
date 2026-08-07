"""Configuration package: settings loaded from the environment plus static
reference data (countries, languages, the target-vehicle profile)."""

from .settings import Settings, get_settings
from .target import TARGET, IgnisSportProfile

__all__ = ["Settings", "get_settings", "TARGET", "IgnisSportProfile"]
