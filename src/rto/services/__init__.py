"""Application services: transactional use cases on top of the data model and the pure domain logic."""


class ServiceError(Exception):
    """A business-rule violation that should be shown to the user (HTTP 400/403/409)."""

    status_code = 400


class PermissionDenied(ServiceError):
    status_code = 403


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409
