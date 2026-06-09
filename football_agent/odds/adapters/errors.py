"""Exceptions for live odds adapters."""


class OddsServiceError(Exception):
    """Base class for odds adapter failures."""


class OddsServiceConfigurationError(OddsServiceError):
    """Missing base URL or invalid adapter configuration."""


class OddsServiceUnavailableError(OddsServiceError):
    """Odds endpoint unreachable or returned an error response."""
