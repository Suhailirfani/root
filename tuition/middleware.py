from django.utils.cache import add_never_cache_headers

class NoCacheMiddleware:
    """
    Middleware to disable browser caching for all responses.
    This ensures that users always see the most up-to-date content
    and don't get stuck seeing old cached pages after logging out or updating data.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # We only want to prevent caching for dynamic HTML responses, not static files like images/css
        # However, for simplicity and to guarantee no stale state issues, we can apply it broadly,
        # or selectively check if it's an HTML response or if the request is not for static media.
        if request.path.startswith('/static/') or request.path.startswith('/media/'):
            return response
            
        add_never_cache_headers(response)
        return response
