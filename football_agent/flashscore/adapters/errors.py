"""Exceptions for live Flashscore scraper adapters."""


class FlashscoreScraperError(Exception):
    """Base class for Flashscore scraper adapter failures."""


class FlashscoreScraperConfigurationError(FlashscoreScraperError):
    """Missing base URL or invalid adapter configuration."""


class FlashscoreScraperUnavailableError(FlashscoreScraperError):
    """Scraper endpoint unreachable or returned an error response."""
