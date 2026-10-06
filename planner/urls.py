from django.urls import path

from . import views

app_name = 'planner'

urlpatterns = [
    path('', views.month, name='month'),
    path('<int:year>/<int:month>/', views.month, name='month'),
    path('events/new/', views.event_create, name='event_create'),
    path('events/<int:pk>/edit/', views.event_edit, name='event_edit'),
    path('events/<int:pk>/delete/', views.event_delete, name='event_delete'),
    path('notifications/', views.notification_settings, name='notifications'),
    path('notifications/test/', views.notification_test, name='notification_test'),
    path('notifications/devices/<int:pk>/remove/', views.device_remove, name='device_remove'),
    path('push/subscribe/', views.push_subscribe, name='push_subscribe'),
    path('push/unsubscribe/', views.push_unsubscribe, name='push_unsubscribe'),
    path('sw.js', views.service_worker, name='service_worker'),
]
