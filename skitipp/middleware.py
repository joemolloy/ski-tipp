from django.contrib import messages
from django.http import HttpResponseRedirect, JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme

from skitipp.fis_connector import FISRateLimited


class FISRateLimitMiddleware:
    """Shows a warning message instead of a server error when FIS rate limits an update."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, FISRateLimited):
            return None

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'error': str(exception)}, status=429)

        messages.warning(request, str(exception))
        back = request.META.get('HTTP_REFERER')
        if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
            back = '/'
        return HttpResponseRedirect(back)
