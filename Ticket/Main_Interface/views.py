import random
import string
from django.shortcuts import redirect, render
from django.http import JsonResponse
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.hashers import make_password
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from django.urls import reverse
from .models import Profile, VarificationCode
from Railway_Admin.models import TrainInformation, TrainSchedule, TrainTicket


def home_page(request):
    schedules = TrainSchedule.objects.select_related('train').order_by('departure_time')
    return render(request, "Main_Interface/home_page.html", {
        'featured_schedules': schedules[:3],
        'has_more_schedules': schedules.count() > 3,
    })


def _varification_code():
    return random.randint(100000, 999999)


def send_verification_code(email):
    code = _varification_code()

    VarificationCode.objects.update_or_create(
        email=email,
        defaults={'code': code, 'is_used': False}
    )

    send_mail(
        'Your Verification Code',
        f'Your verification code is: {code}',
        settings.DEFAULT_FROM_EMAIL,
        [email],
        fail_silently=False,
    )


def register(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=405)

    full_name = request.POST.get('full_name')
    email = request.POST.get('email')
    phone_number = request.POST.get('phone_number')
    nid = request.POST.get('nid_number')
    password = request.POST.get('password')
    confirm_password = request.POST.get('confirm_password')

    if not all([full_name, email, phone_number, nid, password, confirm_password]):
        return JsonResponse({'success': False, 'message': 'All fields are required.'})

    if password != confirm_password:
        return JsonResponse({'success': False, 'message': 'Passwords do not match.'})

    if User.objects.filter(email=email).exists():
        return JsonResponse({'success': False, 'message': 'Email already exists.'})
    if Profile.objects.filter(phone_number=phone_number).exists():
        return JsonResponse({'success': False, 'message': 'Phone number already exists.'})
    if Profile.objects.filter(nid=nid).exists():
        return JsonResponse({'success': False, 'message': 'NID already exists.'})

     
    request.session['pending_registration'] = {
        'full_name': full_name,
        'email': email,
        'phone_number': phone_number,
        'nid': nid,
        'password': make_password(password),   
    }
    request.session['pending_email'] = email

    send_verification_code(email)

    return JsonResponse({
        'success': True,
        'message': 'A verification code has been sent to your email.',
        'next_modal': 'verifyModal'
    })


def varification_code(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=405)

    code = request.POST.get('code')
    pending_email = request.session.get('pending_email')
    pending_data = request.session.get('pending_registration')

    if not pending_email or not pending_data:
        return JsonResponse({'success': False, 'message': 'Session expired. Please register again.'})

    try:
        entry = VarificationCode.objects.get(email=pending_email, code=code, is_used=False)
    except VarificationCode.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Invalid or expired verification code.'})

    if entry.created_at < timezone.now() - timedelta(minutes=10):
        return JsonResponse({'success': False, 'message': 'Verification code expired. Please resend.'})

    entry.is_used = True
    entry.save()

     
    user = User.objects.create(
        username=pending_data['email'],
        email=pending_data['email'],
        first_name=pending_data['full_name'],
        password=pending_data['password'],   
    )

    Profile.objects.create(
        user=user,
        phone_number=pending_data['phone_number'],
        nid=pending_data['nid'],
    )

    # session cleanup
    del request.session['pending_registration']
    del request.session['pending_email']

    login(request, user)

    return JsonResponse({
        'success': True,
        'message': 'Account created and verified successfully!',
        'redirect_url': '/'
    })


def resend_code(request):
    pending_email = request.session.get('pending_email')
    if not pending_email:
        return JsonResponse({'success': False, 'message': 'No pending email found. Please register again.'})

    send_verification_code(pending_email)
    return JsonResponse({'success': True, 'message': 'A new code has been sent.'})


def login_view(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=405)

    phone_number = request.POST.get('phone_number')
    password = request.POST.get('password')

    try:
        profile = Profile.objects.get(phone_number=phone_number)
        username = profile.user.username
    except Profile.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Invalid phone number or password.'})

    user = authenticate(request, username=username, password=password)
    if user is not None:
        login(request, user)
        return JsonResponse({'success': True, 'message': 'Logged in successfully!', 'redirect_url': reverse('ticket_page'),})
    else:
        return JsonResponse({'success': False, 'message': 'Invalid phone number or password.'})


def logout_view(request):
    logout(request)
    return redirect('home_page')


@login_required
def profile_view(request):
    return render(request, "Main_Interface/profile.html", {
        'password_form': PasswordChangeForm(request.user),
    })


@login_required
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            form.save()
            login(request, request.user)
            return redirect('profile')
    else:
        form = PasswordChangeForm(request.user)

    return render(request, "Main_Interface/profile.html", {'password_form': form})

def get_coach_letter(index):
    if index < 26:
        return string.ascii_uppercase[index]
    else:
        first = (index // 26) - 1
        second = index % 26
        return string.ascii_uppercase[first] + string.ascii_uppercase[second]
    

SEATS_PER_COACH = 60  

@login_required
def ticket_page(request):
    if not request.user.is_authenticated:
        return redirect("login_view")

    schedules = TrainSchedule.objects.select_related("train").all()
    train_list = []

    for sched in schedules:
        total_seats = sched.train.total_seats
        coaches = {}
        seat_num = 1
        coach_index = 0

        while seat_num <= total_seats:
            coach_letter = get_coach_letter(coach_index)
            coach_seats = []
            for _ in range(SEATS_PER_COACH):
                if seat_num > total_seats:
                    break
                coach_seats.append(f"{coach_letter}{seat_num}")
                seat_num += 1
            coaches[coach_letter] = coach_seats
            coach_index += 1

        # Seats that are already booked for this schedule, so the page can render
        # them as unavailable immediately instead of waiting for a WebSocket event.
        booked_seats = set(
            TrainTicket.objects.filter(
                train_schedule=sched, status="booked"
            ).values_list("seat_number", flat=True)
        )

        train_list.append({
            "schedule_id": sched.id,
            "train_name": sched.train.train_name,
            "train_number": sched.train.train_number,
            "departure_time": sched.departure_time,
            "arrival_time": sched.arrival_time,
            "source_station": sched.source_station,
            "destination_station": sched.destination_station,
            "ticket_price": sched.ticket_price or 0,
            "seats": coaches,
            "booked_seats": booked_seats,
        })

    return render(request, "Main_Interface/ticket_page.html", {"train_list": train_list})


def train_schedule(request):
    return render(request, "Main_Interface/train_schedule.html", {
        'trains': TrainInformation.objects.all().order_by('train_number'),
        'schedules': TrainSchedule.objects.select_related('train').order_by('departure_time'),
    })


import redis
from django.conf import settings
from django.db import transaction
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


redis_client = redis.Redis.from_url(settings.REDIS_URL)
LOCK_TIMEOUT = 10  # seconds

@login_required
def book_seat(request, schedule_id):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Invalid method."}, status=405)

    seat_number = request.POST.get("seat_number")
    if not seat_number:
        return JsonResponse({"success": False, "message": "seat_number required."})

    lock_key = f"lock:schedule:{schedule_id}:seat:{seat_number}"
    lock = redis_client.lock(lock_key, timeout=LOCK_TIMEOUT)

    if not lock.acquire(blocking=True, blocking_timeout=3):
        return JsonResponse({"success": False, "message": "Seat is being booked by someone else. Try again."})

    try:
        schedule = TrainSchedule.objects.get(id=schedule_id)

        already_taken = TrainTicket.objects.filter(
            train_schedule=schedule, seat_number=seat_number, status="booked"
        ).exists()
        if already_taken:
            return JsonResponse({"success": False, "message": "Seat already booked."})

        with transaction.atomic():
            ticket = TrainTicket.objects.create(
                train_schedule=schedule,
                passenger_name=request.user.get_full_name() or request.user.username,
                passenger_phone_number=request.user.profile.phone_number,
                seat_number=seat_number,
                status="booked",
            )

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"schedule_{schedule_id}",
            {"type": "seat_update", "seat_number": seat_number, "status": "booked"},
        )

        return JsonResponse({
            "success": True,
            "message": "Seat booked successfully.",
            "confirmation_number": ticket.confirmation_number,
        })
    finally:
        lock.release()