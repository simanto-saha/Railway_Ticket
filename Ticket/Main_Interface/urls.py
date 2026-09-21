from django.urls import path
from . import views

urlpatterns = [
    path('', views.home_page, name='home_page'),
    path('register/', views.register, name='register'),
    path('verify/', views.varification_code, name='varification_code'),
    path('resend-code/', views.resend_code, name='resend_code'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
]