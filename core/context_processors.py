from .models import SystemConfiguration

def system_branding(request):
    """
    Injects global system branding settings into every template.
    """
    config = SystemConfiguration.objects.filter(is_active=True).first()
    return {
        'system_config': config
    }
