from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView, RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('favicon.ico', RedirectView.as_view(url='/static/images/logo-removebg.png', permanent=True)),
    path('manifest.json', TemplateView.as_view(template_name='tuition/manifest.json', content_type='application/json')),
    path('sw.js', TemplateView.as_view(template_name='tuition/sw.js', content_type='application/javascript')),
    path('', include('tuition.urls')),
]
