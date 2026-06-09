"""Exceptions for live OpenClaw context adapters."""


class OpenClawContextError(Exception):
    """Base class for OpenClaw context adapter failures."""


class OpenClawContextConfigurationError(OpenClawContextError):
    """Missing base URL or invalid adapter configuration."""


class OpenClawContextUnavailableError(OpenClawContextError):
    """OpenClaw context endpoint unreachable or returned an error response."""
