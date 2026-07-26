from __future__ import annotations


class DomainError(Exception):
    """Base class for errors safe to map to an API response."""


class NotFoundError(DomainError):
    pass


class ValidationError(DomainError):
    pass


class CapabilityUnavailableError(DomainError):
    pass


class ConfirmationRequiredError(DomainError):
    pass


class UploadTooLargeError(ValidationError):
    pass


class JobCancelledError(DomainError):
    pass
