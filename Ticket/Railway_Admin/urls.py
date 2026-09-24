from django.urls import path
from . import views


urlpatterns = [
    path('superuser-dashboard/IT-Division/', views.superuser_dashboard, name='superuser_dashboard'),
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),

    path('superuser-login/', views.superuser_login, name='superuser_login'),
    path('superuser-logout/', views.superuser_logout, name='superuser_logout'),
]