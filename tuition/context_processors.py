from .models import Centre

def multi_centre_context(request):
    if not request.user or not request.user.is_authenticated:
        return {}
        
    is_admin = request.user.is_superuser or getattr(getattr(request.user, 'profile', None), 'role', None) == 'admin'
    
    if is_admin:
        centres = Centre.objects.all().order_by('name')
        active_centre_id = request.session.get('active_centre_id')
        active_centre = None
        if active_centre_id:
            try:
                active_centre = Centre.objects.get(id=active_centre_id)
            except Centre.DoesNotExist:
                request.session['active_centre_id'] = None
                
        return {
            'all_centres': centres,
            'active_centre': active_centre,
            'is_global_admin': True
        }
    return {}
