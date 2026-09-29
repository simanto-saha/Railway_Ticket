from django.urls import path
from . import views

urlpatterns = [
    path('', views.home_page, name='home_page'),
    path('register/', views.register, name='register'),
    path('verify/', views.varification_code, name='varification_code'),
    path('resend-code/', views.resend_code, name='resend_code'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/image/', views.update_profile_image, name='update_profile_image'),
    path('profile/', views.profile_view, name='profile'),
    path('profile/password/', views.change_password, name='change_password'),
    path('ticket/', views.ticket_page, name='ticket_page'),
    path('train-schedule/', views.train_schedule, name='train_schedule'),
    path("book-seat/<int:schedule_id>/", views.book_seat, name="book_seat"),
    path("seat-hold/<int:schedule_id>/", views.hold_seat, name="hold_seat"),
    path("payment/start/", views.start_ticket_payment, name="start_ticket_payment"),
    path("payment/abandon/", views.abandon_ticket_payment, name="abandon_ticket_payment"),
    path("payment/success/", views.payment_success, name="payment_success"),
    path("payment/failure/", views.payment_failure, name="payment_failure"),
    path("seat-confarmation/<int:schedule_id>/", views.sit_confarmation_page, name="seat_confarmation"),

    path("varification-ticket/", views.varification_ticket, name="varification_ticket"),
   
]