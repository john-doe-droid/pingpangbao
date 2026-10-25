"""Request middleware for privacy-minimized page analytics."""
from django.conf import settings

from .models import VisitorEvent
from .visitors import record_visitor_event


class VisitorTrackingMiddleware:
    EXCLUDED_PREFIXES = (
        "/static/",
        "/health/",
        "/files/",
        "/favicon.ico",
        "/admin/",
        "/manage/visits/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            getattr(settings, "VISITOR_ANALYTICS_ENABLED", True)
            and request.method == "GET"
            and response.status_code == 200
            and not request.path.startswith(self.EXCLUDED_PREFIXES)
            and response.get("Content-Type", "").startswith("text/html")
        ):
            record_visitor_event(
                request, VisitorEvent.EventType.PAGE,
            )
        return response
