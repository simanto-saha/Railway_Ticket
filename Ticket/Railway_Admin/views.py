import secrets
import string

from django.http import JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordChangeForm
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.contrib.auth.decorators import login_required
from .models import TrainInformation, TrainSchedule, TrainDriverInformation
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render, redirect, get_object_or_404
from .models import AdminProfile



def is_superuser(user):
    return user.is_authenticated and user.is_superuser


@user_passes_test(is_superuser, login_url='superuser_login')
def superuser_dashboard(request):
    profiles = AdminProfile.objects.select_related('user').all()
    return render(request, 'Railway_Admin/superuser_dashboard.html', {'profiles': profiles})


def superuser_login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = User.objects.filter(username=username).first()

        if user and user.check_password(password) and (user.is_superuser or hasattr(user, 'adminprofile')):
            login(request, user)
            if user.is_superuser:
                redirect_url = request.POST.get('next') or reverse('superuser_dashboard')
            elif user.adminprofile.one_time_password:
                redirect_url = reverse('admin_dashboard')
            else:
                redirect_url = reverse('admin_password_change')
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': True, 'redirect_url': redirect_url})
            return redirect(redirect_url)

        error = 'Invalid credentials or user account.'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': error})
        return render(request, 'Railway_Admin/superuser_base.html', {
            'error': error,
            'next': request.POST.get('next', ''),
            'open_login': True,
        })

    return render(request, 'Railway_Admin/superuser_base.html', {
        'next': request.GET.get('next', ''),
        'open_login': True,
    })


def superuser_logout(request):
    logout(request)
    return redirect('superuser_login')


@login_required(login_url='superuser_login')
def admin_password_change(request):
    if request.user.is_superuser or not hasattr(request.user, 'adminprofile'):
        return redirect('admin_dashboard')

    profile = request.user.adminprofile
    if profile.one_time_password:
        return redirect('admin_dashboard')

    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            form.save()
            profile.one_time_password = True
            profile.save(update_fields=['one_time_password'])
            login(request, request.user)
            return redirect('admin_dashboard')
    else:
        form = PasswordChangeForm(request.user)

    return render(request, 'Railway_Admin/admin_password_change.html', {'form': form})


@user_passes_test(is_superuser, login_url='superuser_login')
def admin_profile_create(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        full_name = request.POST.get('full_name')
        email = request.POST.get('email')
        designation = request.POST.get('designation')
        phone_number = request.POST.get('phone_number')

        if User.objects.filter(username=username).exists():
            return JsonResponse({'success': False, 'error': 'Username already exists.'})
        if AdminProfile.objects.filter(email=email).exists():
            return JsonResponse({'success': False, 'error': 'Email already exists.'})
        if AdminProfile.objects.filter(phone_number=phone_number).exists():
            return JsonResponse({'success': False, 'error': 'Phone number already exists.'})

        temporary_password = ''.join(
            secrets.choice(string.ascii_letters + string.digits)
            for _ in range(12)
        )

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=temporary_password,
                )
                AdminProfile.objects.create(
                    user=user, full_name=full_name, email=email,
                    designation=designation, phone_number=phone_number
                )
                send_mail(
                    'Your Railway Admin Account',
                    (
                        f'Hello {full_name},\n\n'
                        f'Your admin account has been created.\n'
                        f'Username: {username}\n'
                        f'One-time password: {temporary_password}\n\n'
                        'Please log in and change this password immediately.'
                    ),
                    settings.DEFAULT_FROM_EMAIL,
                    [email],
                    fail_silently=False,
                )
        except Exception:
            return JsonResponse({
                'success': False,
                'error': 'Admin profile could not be created or the email could not be sent.',
            }, status=500)

        return JsonResponse({'success': True})

    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@user_passes_test(is_superuser, login_url='superuser_login')
def admin_profile_get(request, pk):
    profile = get_object_or_404(AdminProfile, pk=pk)
    return JsonResponse({
        'id': profile.pk,
        'username': profile.user.username,
        'full_name': profile.full_name,
        'email': profile.email,
        'designation': profile.designation,
        'phone_number': profile.phone_number,
    })


@user_passes_test(is_superuser, login_url='superuser_login')
def admin_profile_edit(request, pk):
    profile = get_object_or_404(AdminProfile, pk=pk)

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        if User.objects.filter(username=username).exclude(pk=profile.user.pk).exists():
            return JsonResponse({'success': False, 'error': 'Username already exists.'})

        profile.user.username = username
        if password:
            profile.user.set_password(password)
        profile.user.save()

        profile.full_name = request.POST.get('full_name')
        profile.email = request.POST.get('email')
        profile.designation = request.POST.get('designation')
        profile.phone_number = request.POST.get('phone_number')
        profile.save()

        return JsonResponse({'success': True})

    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@user_passes_test(is_superuser, login_url='superuser_login')
def admin_profile_delete(request, pk):
    profile = get_object_or_404(AdminProfile, pk=pk)
    if request.method == 'POST':
        profile.user.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


def is_admin(user):
    return user.is_authenticated and hasattr(user, 'adminprofile')


@user_passes_test(is_admin, login_url='superuser_login')
def admin_dashboard(request):
    if not request.user.adminprofile.one_time_password:
        return redirect('admin_password_change')
    context = {
        'trains': TrainInformation.objects.all(),
        'schedules': TrainSchedule.objects.select_related('train').all(),
        'drivers': TrainDriverInformation.objects.select_related('train').all(),
    }
    return render(request, 'Railway_Admin/admin_dashboard.html', context)


@user_passes_test(is_admin, login_url='superuser_login')
def train_create(request):
    if request.method == 'POST':
        train_number = request.POST.get('train_number')
        train_name = request.POST.get('train_name')
        total_seats = request.POST.get('total_seats')

        if TrainInformation.objects.filter(train_number=train_number).exists():
            return JsonResponse({'success': False, 'error': 'Train number already exists.'})

        train = TrainInformation.objects.create(
            train_number=train_number, train_name=train_name, total_seats=total_seats
        )
        return JsonResponse({
            'success': True,
            'id': train.pk,
            'train_number': train.train_number,
            'train_name': train.train_name,
            'total_seats': train.total_seats,
        })

    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@user_passes_test(is_admin, login_url='superuser_login')
def train_get(request, pk):
    train = get_object_or_404(TrainInformation, pk=pk)
    return JsonResponse({
        'id': train.pk,
        'train_number': train.train_number,
        'train_name': train.train_name,
        'total_seats': train.total_seats,
    })


@user_passes_test(is_admin, login_url='superuser_login')
def train_edit(request, pk):
    train = get_object_or_404(TrainInformation, pk=pk)
    if request.method == 'POST':
        train_number = request.POST.get('train_number')
        if TrainInformation.objects.filter(train_number=train_number).exclude(pk=pk).exists():
            return JsonResponse({'success': False, 'error': 'Train number already exists.'})
        train.train_number = train_number
        train.train_name = request.POST.get('train_name')
        train.total_seats = request.POST.get('total_seats')
        train.save()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@user_passes_test(is_admin, login_url='superuser_login')
def train_delete(request, pk):
    if request.method == 'POST':
        get_object_or_404(TrainInformation, pk=pk).delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@user_passes_test(is_admin, login_url='superuser_login')
def schedule_create(request):
    if request.method == 'POST':
        departure_time = parse_datetime(request.POST.get('departure_time', ''))
        arrival_time = parse_datetime(request.POST.get('arrival_time', ''))

        if not departure_time or not arrival_time:
            return JsonResponse({
                'success': False,
                'error': 'Valid departure and arrival times are required.',
            })

        if timezone.is_naive(departure_time):
            departure_time = timezone.make_aware(departure_time)
        if timezone.is_naive(arrival_time):
            arrival_time = timezone.make_aware(arrival_time)

        TrainSchedule.objects.create(
            train_id=request.POST.get('train'),
            departure_time=departure_time,
            arrival_time=arrival_time,
            source_station=request.POST.get('source_station'),
            destination_station=request.POST.get('destination_station'),
        )
        return JsonResponse({'success': True})

    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@user_passes_test(is_admin, login_url='superuser_login')
def schedule_get(request, pk):
    schedule = get_object_or_404(TrainSchedule, pk=pk)
    return JsonResponse({
        'id': schedule.pk,
        'train': schedule.train_id,
        'source_station': schedule.source_station,
        'destination_station': schedule.destination_station,
        'departure_time': timezone.localtime(schedule.departure_time).strftime('%Y-%m-%dT%H:%M'),
        'arrival_time': timezone.localtime(schedule.arrival_time).strftime('%Y-%m-%dT%H:%M'),
    })


@user_passes_test(is_admin, login_url='superuser_login')
def schedule_edit(request, pk):
    schedule = get_object_or_404(TrainSchedule, pk=pk)
    if request.method == 'POST':
        departure_time = parse_datetime(request.POST.get('departure_time', ''))
        arrival_time = parse_datetime(request.POST.get('arrival_time', ''))
        if not departure_time or not arrival_time:
            return JsonResponse({'success': False, 'error': 'Valid departure and arrival times are required.'})
        schedule.train_id = request.POST.get('train')
        schedule.source_station = request.POST.get('source_station')
        schedule.destination_station = request.POST.get('destination_station')
        schedule.departure_time = timezone.make_aware(departure_time) if timezone.is_naive(departure_time) else departure_time
        schedule.arrival_time = timezone.make_aware(arrival_time) if timezone.is_naive(arrival_time) else arrival_time
        schedule.save()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@user_passes_test(is_admin, login_url='superuser_login')
def schedule_delete(request, pk):
    if request.method == 'POST':
        get_object_or_404(TrainSchedule, pk=pk).delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@user_passes_test(is_admin, login_url='superuser_login')
def driver_create(request):
    if request.method == 'POST':
        license_number = request.POST.get('license_number')

        if TrainDriverInformation.objects.filter(license_number=license_number).exists():
            return JsonResponse({'success': False, 'error': 'License number already exists.'})

        TrainDriverInformation.objects.create(
            train_id=request.POST.get('train'),
            driver_name=request.POST.get('driver_name'),
            license_number=license_number,
            contact_number=request.POST.get('contact_number'),
        )
        return JsonResponse({'success': True})

    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@user_passes_test(is_admin, login_url='superuser_login')
def driver_get(request, pk):
    driver = get_object_or_404(TrainDriverInformation, pk=pk)
    return JsonResponse({
        'id': driver.pk,
        'train': driver.train_id,
        'driver_name': driver.driver_name,
        'license_number': driver.license_number,
        'contact_number': driver.contact_number,
    })


@user_passes_test(is_admin, login_url='superuser_login')
def driver_edit(request, pk):
    driver = get_object_or_404(TrainDriverInformation, pk=pk)
    if request.method == 'POST':
        license_number = request.POST.get('license_number')
        if TrainDriverInformation.objects.filter(license_number=license_number).exclude(pk=pk).exists():
            return JsonResponse({'success': False, 'error': 'License number already exists.'})
        driver.train_id = request.POST.get('train')
        driver.driver_name = request.POST.get('driver_name')
        driver.license_number = license_number
        driver.contact_number = request.POST.get('contact_number')
        driver.save()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})


@user_passes_test(is_admin, login_url='superuser_login')
def driver_delete(request, pk):
    if request.method == 'POST':
        get_object_or_404(TrainDriverInformation, pk=pk).delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Invalid request.'})

@user_passes_test(is_admin, login_url='superuser_login')
def admin_profile(request):
    profile = request.user.adminprofile
    return render(request, 'Railway_Admin/admin_profile.html', {'profile': profile})


@user_passes_test(is_admin, login_url='superuser_login')
def admin_profile_update(request):
    profile = request.user.adminprofile

    if request.method == 'POST':
        full_name = request.POST.get('full_name')
        email = request.POST.get('email')
        designation = request.POST.get('designation')
        phone_number = request.POST.get('phone_number')

        current_password = request.POST.get('current_password')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')

        # Email/phone uniqueness check (exclude nijer profile)
        if AdminProfile.objects.filter(email=email).exclude(pk=profile.pk).exists():
            return JsonResponse({'success': False, 'error': 'Email already in use.'})
        if AdminProfile.objects.filter(phone_number=phone_number).exclude(pk=profile.pk).exists():
            return JsonResponse({'success': False, 'error': 'Phone number already in use.'})

        # Password change (optional)
        if new_password or current_password or confirm_password:
            if not request.user.check_password(current_password):
                return JsonResponse({'success': False, 'error': 'Current password is incorrect.'})
            if new_password != confirm_password:
                return JsonResponse({'success': False, 'error': 'New passwords do not match.'})
            if len(new_password) < 8:
                return JsonResponse({'success': False, 'error': 'Password must be at least 8 characters.'})

            request.user.set_password(new_password)
            request.user.save()

        profile.full_name = full_name
        profile.email = email
        profile.designation = designation
        profile.phone_number = phone_number
        profile.save()

        # Password change hoile session invalidate hoye jay, tai re-login lagbe
        if new_password:
            return JsonResponse({'success': True, 'password_changed': True, 'redirect_url': reverse('superuser_login')})

        return JsonResponse({'success': True, 'password_changed': False})

    return JsonResponse({'success': False, 'error': 'Invalid request.'})


def superuser_logout(request):
    logout(request)
    return redirect('superuser_dashboard')


