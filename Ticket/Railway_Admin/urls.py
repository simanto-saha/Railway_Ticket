from django.urls import path
from . import views


urlpatterns = [
    path('superuser-dashboard/IT-Division/', views.superuser_dashboard, name='superuser_dashboard'),
    path('superuser/admin-profiles/create/', views.admin_profile_create, name='admin_profile_create'),
    path('superuser/admin-profiles/<int:pk>/', views.admin_profile_get, name='admin_profile_get'),
    path('superuser/admin-profiles/<int:pk>/edit/', views.admin_profile_edit, name='admin_profile_edit'),
    path('superuser/admin-profiles/<int:pk>/delete/', views.admin_profile_delete, name='admin_profile_delete'),


    path('super-admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('super-admin-password-change/', views.admin_password_change, name='admin_password_change'),
    path('super-admin/train/create/', views.train_create, name='train_create'),
    path('super-admin/train/<int:pk>/', views.train_get, name='train_get'),
    path('super-admin/train/<int:pk>/edit/', views.train_edit, name='train_edit'),
    path('super-admin/train/<int:pk>/delete/', views.train_delete, name='train_delete'),
    path('super-admin/schedule/create/', views.schedule_create, name='schedule_create'),
    path('super-admin/schedule/<int:pk>/', views.schedule_get, name='schedule_get'),
    path('super-admin/schedule/<int:pk>/edit/', views.schedule_edit, name='schedule_edit'),
    path('super-admin/schedule/<int:pk>/delete/', views.schedule_delete, name='schedule_delete'),
    path('super-admin/driver/create/', views.driver_create, name='driver_create'),
    path('super-admin/driver/<int:pk>/', views.driver_get, name='driver_get'),
    path('super-admin/driver/<int:pk>/edit/', views.driver_edit, name='driver_edit'),
    path('super-admin/driver/<int:pk>/delete/', views.driver_delete, name='driver_delete'),
    path('super-admin/profile/', views.admin_profile, name='admin_profile'),
    path('super-admin/profile/edit/', views.admin_profile_edit, name='admin_profile_edit'),
    path('super-admin/profile/password-change/', views.admin_password_change, name='admin_password_change'),
    path('super-admin/ticket/create/', views.admin_profile_update, name='admin_profile_update'),
    path('super-admin/schedule/<int:pk>/delete/', views.schedule_delete, name='schedule_delete'),

    path('super-admin-login/', views.superuser_login, name='superuser_login'),
    path('superuser-logout/', views.superuser_logout, name='superuser_logout'),
]