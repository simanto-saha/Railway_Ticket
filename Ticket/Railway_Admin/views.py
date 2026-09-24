from django.http import JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.urls import reverse
from django.shortcuts import render, redirect


def superuser_dashboard(request):
    return render(request, 'Railway_Admin/superuser_dashboard.html')


def superuser_login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = User.objects.filter(username=username).first()

        if user and user.check_password(password) and user.is_superuser:
            login(request, user)
            return JsonResponse({
                'success': True,
                'redirect_url': reverse('superuser_dashboard')
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'Invalid credentials or not a superuser.'
            })

    return render(request, 'Railway_Admin/superuser_login.html')


def superuser_logout(request):
    logout(request)
    return redirect('superuser_dashboard')


def admin_dashboard(request):
    return render(request, 'Railway_Admin/admin_dashboard.html')
