"""Central application settings.

All secrets and deployment-specific values come from the environment (a `.env`
file is loaded if present). Nothing here is hardcoded that ought to be
configurable, and no secret is ever written back out. See `.env.example`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = two levels up from this file (src/config/settings.py).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
CACHE_DIR = DATA_DIR / "cache"
BACKUP_DIR = DATA_DIR / "backups"


class Settings(BaseSettings):
    """Runtime configuration, populated from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- AI layer ---------------------------------------------------------
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-opus-4-8", alias="ANTHROPIC_MODEL")
    anthropic_model_fast: str = Field(
        default="claude-haiku-4-5-20251001", alias="ANTHROPIC_MODEL_FAST"
    )
    ai_monthly_budget_usd: float = Field(default=25.0, alias="AI_MONTHLY_BUDGET_USD")

    # --- Database ---------------------------------------------------------
    database_url: str = Field(
        default="sqlite:///data/ignis_hunter.db", alias="DATABASE_URL"
    )

    # --- Scheduler --------------------------------------------------------
    scan_times: str = Field(default="00:00,06:00,12:00,18:00", alias="SCAN_TIMES")
    timezone: str = Field(default="Europe/Berlin", alias="TIMEZONE")
    discovery_hour: int = Field(default=3, alias="DISCOVERY_HOUR")

    # --- Search / discovery ----------------------------------------------
    search_provider: str = Field(default="none", alias="SEARCH_PROVIDER")
    serpapi_key: str | None = Field(default=None, alias="SERPAPI_KEY")
    brave_api_key: str | None = Field(default=None, alias="BRAVE_API_KEY")
    google_cse_id: str | None = Field(default=None, alias="GOOGLE_CSE_ID")
    google_cse_key: str | None = Field(default=None, alias="GOOGLE_CSE_KEY")
    bing_api_key: str | None = Field(default=None, alias="BING_API_KEY")

    # Preferred, explicit multi-provider config (Brave AND optionally SerpApi).
    brave_search_api_key: str | None = Field(default=None, alias="BRAVE_SEARCH_API_KEY")
    serpapi_api_key: str | None = Field(default=None, alias="SERPAPI_API_KEY")
    provider_brave_enabled: bool = Field(default=False, alias="SEARCH_PROVIDER_BRAVE_ENABLED")
    provider_serpapi_enabled: bool = Field(default=False, alias="SEARCH_PROVIDER_SERPAPI_ENABLED")

    # Monthly budgets. 0 disables the guard.
    brave_monthly_request_budget: int = Field(default=2000, alias="BRAVE_MONTHLY_REQUEST_BUDGET")
    serpapi_monthly_request_budget: int = Field(default=100, alias="SERPAPI_MONTHLY_REQUEST_BUDGET")
    anthropic_monthly_cost_budget: float | None = Field(
        default=None, alias="ANTHROPIC_MONTHLY_COST_BUDGET")

    # How deep to paginate. High-value (chassis/exact) queries may go deeper.
    search_pages_default: int = Field(default=1, alias="SEARCH_PAGES_DEFAULT")
    search_pages_high_value: int = Field(default=3, alias="SEARCH_PAGES_HIGH_VALUE")

    # --- Crawler ----------------------------------------------------------
    user_agent: str = Field(
        default="IgnisSportHunter/1.0 (+https://github.com/justtheboisgang/suzuki.ignis.sport.scanner)",
        alias="USER_AGENT",
    )
    request_timeout: float = Field(default=20.0, alias="REQUEST_TIMEOUT")
    crawl_delay_seconds: float = Field(default=2.0, alias="CRAWL_DELAY_SECONDS")
    max_pages_per_source: int = Field(default=40, alias="MAX_PAGES_PER_SOURCE")
    respect_robots: bool = Field(default=True, alias="RESPECT_ROBOTS")

    # --- Classification ---------------------------------------------------
    match_confidence_threshold: int = Field(default=60, alias="MATCH_CONFIDENCE_THRESHOLD")
    hot_alert_confidence: int = Field(default=85, alias="HOT_ALERT_CONFIDENCE")

    # --- Notifications ----------------------------------------------------
    notify_channels: str = Field(default="console,database", alias="NOTIFY_CHANNELS")
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID")
    discord_webhook_url: str | None = Field(default=None, alias="DISCORD_WEBHOOK_URL")
    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str | None = Field(default=None, alias="SMTP_USER")
    smtp_password: str | None = Field(default=None, alias="SMTP_PASSWORD")
    smtp_from: str | None = Field(default=None, alias="SMTP_FROM")
    smtp_to: str | None = Field(default=None, alias="SMTP_TO")

    # --- Dashboard --------------------------------------------------------
    dashboard_host: str = Field(default="0.0.0.0", alias="DASHBOARD_HOST")
    dashboard_port: int = Field(default=8000, alias="DASHBOARD_PORT")

    # ---------------------------------------------------------------------
    @field_validator("discovery_hour")
    @classmethod
    def _valid_hour(cls, v: int) -> int:
        return max(0, min(23, v))

    @property
    def scan_time_list(self) -> list[tuple[int, int]]:
        """Parse SCAN_TIMES ("HH:MM,HH:MM") into (hour, minute) tuples."""
        out: list[tuple[int, int]] = []
        for chunk in self.scan_times.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                h, m = chunk.split(":")
                out.append((int(h) % 24, int(m) % 60))
            except ValueError:
                continue
        return out or [(0, 0), (6, 0), (12, 0), (18, 0)]

    @property
    def notify_channel_list(self) -> list[str]:
        return [c.strip().lower() for c in self.notify_channels.split(",") if c.strip()]

    # --- Search provider resolution (new explicit vars win, old ones are a
    #     backward-compatible fallback) -----------------------------------
    @property
    def brave_key(self) -> str | None:
        return self.brave_search_api_key or self.brave_api_key

    @property
    def serpapi_key_resolved(self) -> str | None:
        return self.serpapi_api_key or self.serpapi_key

    @property
    def anthropic_budget(self) -> float:
        """Effective monthly AI cost budget (new var overrides the old one)."""
        if self.anthropic_monthly_cost_budget is not None:
            return self.anthropic_monthly_cost_budget
        return self.ai_monthly_budget_usd

    def enabled_providers(self) -> list[str]:
        """Names of search providers that are both enabled AND have a key.
        Supports Brave AND SerpApi simultaneously (multi-provider)."""
        out: list[str] = []
        brave_on = self.provider_brave_enabled or (
            self.search_provider or "").lower() == "brave"
        serp_on = self.provider_serpapi_enabled or (
            self.search_provider or "").lower() == "serpapi"
        if brave_on and self.brave_key:
            out.append("brave")
        if serp_on and self.serpapi_key_resolved:
            out.append("serpapi")
        # Google CSE remains available via the legacy single-provider path.
        if not out and (self.search_provider or "").lower() == "google_cse" \
                and self.google_cse_id and self.google_cse_key:
            out.append("google_cse")
        return out

    @property
    def ai_enabled(self) -> bool:
        """AI is enabled only when a key is present AND the anthropic SDK is
        importable. All AI modules must degrade gracefully otherwise."""
        if not self.anthropic_api_key:
            return False
        try:  # pragma: no cover - trivial import guard
            import anthropic  # noqa: F401
        except Exception:
            return False
        return True

    def ensure_dirs(self) -> None:
        for d in (DATA_DIR, LOGS_DIR, CACHE_DIR, BACKUP_DIR):
            d.mkdir(parents=True, exist_ok=True)

    def resolved_database_url(self) -> str:
        """Turn a relative sqlite path into an absolute one anchored at the
        project root so the DB location is stable regardless of CWD."""
        url = self.database_url
        prefix = "sqlite:///"
        if url.startswith(prefix):
            raw = url[len(prefix):]
            p = Path(raw)
            if not p.is_absolute():
                p = PROJECT_ROOT / p
                return f"{prefix}{p}"
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton."""
    s = Settings()
    s.ensure_dirs()
    return s
