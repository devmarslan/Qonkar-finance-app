import traceback
from .utils import log_activity

class ExceptionLoggingMiddleware:
    """
    Middleware to catch unhandled exceptions and log them into the ActivityLog.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        """
        Captured when a view raises an exception.
        """
        # Get the traceback
        tb = traceback.format_exc()
        
        # Determine the user (if authenticated)
        user = request.user if request.user.is_authenticated else None
        
        # Get the path where it happened
        path = request.path
        
        # Log the error
        log_activity(
            actor=user,
            action_type='Error',
            description=f"Exception at {path}: {str(exception)}",
            metadata={
                'path': path,
                'method': request.method,
                'traceback': tb,
                'error_type': exception.__class__.__name__
            },
            ip=self.get_client_ip(request)
        )
        
        # We return None to let Django continue its normal exception handling (showing the error page)
        return None

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip
