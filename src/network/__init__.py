"""Real-world network validation — proves (on the eventual VPS) that outbound
connectivity, DNS, HTTPS, the AI + search APIs, a sample of European car sources,
Playwright/Chromium and robots.txt access all actually work."""

from .validate import NetworkValidator, AccessStatus, run_network_test

__all__ = ["NetworkValidator", "AccessStatus", "run_network_test"]
