from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


class Friendly404Middleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if response.status_code == 404 and not request.path.startswith("/internal-admin/"):
            accept = request.headers.get("Accept", "")
            if "text/html" in accept or "*/*" in accept or not accept:
                return render(request, "404.html", status=404)
        return response
