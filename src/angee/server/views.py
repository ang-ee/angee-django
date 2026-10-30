"""Database readiness projection for stack probes."""

from django.core.management.base import CommandError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET
from django_compose.provision import require_provisioned


@require_GET
def readiness(request: HttpRequest) -> JsonResponse:
    """Report the composition provision predicate without mutating state."""

    del request
    try:
        require_provisioned(using="default", timeout=0)
    except CommandError as error:
        return JsonResponse({"ready": False, "error": str(error)}, status=503)
    return JsonResponse({"ready": True})
