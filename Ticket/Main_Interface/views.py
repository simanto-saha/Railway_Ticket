import random
import string
import uuid
import logging
from decimal import Decimal, InvalidOperation
from django.shortcuts import redirect, render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.hashers import make_password
from django.views.decorators.csrf import csrf_exempt
from django.core.mail import EmailMessage, send_mail
from django.core.paginator import Paginator
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from django.urls import reverse
from urllib.parse import urlencode
from .models import Profile, VarificationCode
from Railway_Admin.models import TrainInformation, TrainSchedule, TrainTicket, TicketPayment, generate_confirmation_number
import requests
import redis
from django.db import transaction
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from django.db import IntegrityError, transaction
from django.db.models import Q

from redis.exceptions import LockError
from .ticket_pdf import create_ticket_pdf

logger = logging.getLogger(__name__)


def home_page(request):
    expire_pending_reservations()
    schedules = TrainSchedule.objects.select_related('train').filter(
        departure_time__gte=timezone.now()
    ).order_by('departure_time')
    return render(request, "Main_Interface/home_page.html", {
        'featured_schedules': schedules[:3],
        'has_more_schedules': schedules.count() > 3,
    })


def _varification_code():
    return random.randint(100000, 999999)


def send_verification_code(email):
    code = _varification_code()
    created_at = timezone.now()

    entry, _ = VarificationCode.objects.update_or_create(
        email=email,
        defaults={'code': code, 'is_used': False, 'created_at': created_at}
    )

    send_mail(
        'Your Verification Code',
        f'Your verification code is: {code}',
        settings.DEFAULT_FROM_EMAIL,
        [email],
        fail_silently=False,
    )
    return entry.created_at + timedelta(minutes=2)


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

    expires_at = send_verification_code(email)

    return JsonResponse({
        'success': True,
        'message': 'A verification code has been sent to your email.',
        'next_modal': 'verifyModal',
        'expires_at': expires_at.isoformat(),
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

    if entry.created_at < timezone.now() - timedelta(minutes=2):
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

    expires_at = send_verification_code(pending_email)
    return JsonResponse({
        'success': True,
        'message': 'A new code has been sent.',
        'expires_at': expires_at.isoformat(),
    })


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

from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from .forms import ProfileImageForm


@require_POST
@login_required
def update_profile_image(request):
    profile = request.user.profile
    old = profile.profile_image.name if profile.profile_image else None

    if request.POST.get('remove'):
        if old:
            profile.profile_image.delete(save=True)
        messages.success(request, "Profile photo removed.")
        return redirect('profile')

    form = ProfileImageForm(request.POST, request.FILES, instance=profile)
    if form.is_valid() and request.FILES.get('profile_image'):
        if old:
            profile.profile_image.storage.delete(old)  # purano image delete
        form.save()
        messages.success(request, "Profile photo updated.")
    else:
        messages.error(request, "; ".join(form.errors.get('profile_image', ["Please choose an image."])))
    return redirect('profile')


def _profile_context(user, password_form=None):
    rows = (
        TrainTicket.objects
        .filter(user=user, status="booked")
        .select_related("train_schedule__train")
        .order_by("-booking_time", "seat_number")
    )
    groups = {}
    for t in rows:
        g = groups.setdefault(t.confirmation_number, {"ticket": t, "seats": []})
        g["seats"].append(t.seat_number)

    return {
        'password_form': password_form or PasswordChangeForm(user),
        'all_tickets': list(groups.values()),
    }


@login_required
def profile_view(request):
    return render(request, "Main_Interface/profile.html", _profile_context(request.user))


@login_required
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            form.save()
            login(request, request.user)
            messages.success(request, "Password updated.")
            return redirect('profile')
        return render(request, "Main_Interface/profile.html", _profile_context(request.user, form))
    return redirect('profile')


def get_coach_letter(index):
    if index < 26:
        return string.ascii_uppercase[index]
    else:
        first = (index // 26) - 1
        second = index % 26
        return string.ascii_uppercase[first] + string.ascii_uppercase[second]


# Bangladesh Railway style ticket classes. A schedule's total seats are split
# across these three classes: AC_S 10%, SNIGDHA 20%, S_CHAIR 70%.
# Each class's price comes straight from the schedule's own price field.
CLASS_CONFIG = [
    {"code": "AC_S", "label": "AC Seat", "price_field": "ac_ticket_price", "share": 0.10, "seats_per_coach": 44},
    {"code": "SNIGDHA", "label": "Snigdha (AC Chair)", "price_field": "singdha_ticket_price", "share": 0.20, "seats_per_coach": 50},
    {"code": "S_CHAIR", "label": "Shovon Chair", "price_field": "s_chair_ticket_price", "share": 0.70, "seats_per_coach": 60},
]


def _split_seats_by_class(total_seats):
    """Return {class_code: seat_count} where the shares sum back to total_seats exactly."""
    counts = {}
    remaining = total_seats
    for i, cfg in enumerate(CLASS_CONFIG):
        if i == len(CLASS_CONFIG) - 1:
            count = remaining  # last class absorbs any rounding remainder
        else:
            count = round(total_seats * cfg["share"])
            remaining -= count
        counts[cfg["code"]] = max(count, 0)
    return counts


def _build_classes(total_seats, schedule, booked_seats):
    """Build the AC_S / SNIGDHA / S_CHAIR breakdown (coaches + seats) for one schedule."""
    class_seat_counts = _split_seats_by_class(total_seats)
    coach_index = 0  # shared across classes so coach letters never repeat on a schedule
    classes_data = []

    for cfg in CLASS_CONFIG:
        class_total = class_seat_counts[cfg["code"]]
        price = getattr(schedule, cfg["price_field"]) or 0
        coaches = []
        seat_num = 1
        class_available = 0

        while seat_num <= class_total:
            coach_letter = get_coach_letter(coach_index)
            coach_seats = []
            for _ in range(cfg["seats_per_coach"]):
                if seat_num > class_total:
                    break
                coach_seats.append(f"{coach_letter}{seat_num}")
                seat_num += 1

            coach_available = sum(1 for s in coach_seats if s not in booked_seats)
            class_available += coach_available

            coaches.append({
                "letter": coach_letter,
                "seats": coach_seats,
                "total": len(coach_seats),
                "available": coach_available,
            })
            coach_index += 1

        classes_data.append({
            "code": cfg["code"],
            "label": cfg["label"],
            "price": price,
            "total_seats": class_total,
            "available_seats": class_available,
            "coaches": coaches,
        })

    return classes_data

from datetime import datetime


@login_required
def ticket_page(request):
    expire_pending_reservations()
    normalize_pending_reservations(request.user)
    today = timezone.localdate()
    source = request.GET.get('source', '').strip()
    destination = request.GET.get('destination', '').strip()
    date_str = request.GET.get('date', '').strip()

    stations = sorted(
        {
            station.strip()
            for station in (
                set(TrainSchedule.objects.values_list('source_station', flat=True))
                | set(TrainSchedule.objects.values_list('destination_station', flat=True))
            )
            if station and station.strip()
        },
        key=str.casefold,
    )

    search_error = None
    searched = bool(source or destination or date_str)

    # Nothing is searched yet -> stays empty, no trains are queried or shown.
    schedules = TrainSchedule.objects.none()

    if searched:
        if not (source and destination and date_str):
            search_error = 'Source, destination and date are required.'
        else:
            try:
                journey_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                journey_date = None

            if journey_date and journey_date < today:
                search_error = 'Journey date cannot be in the past.'
            elif journey_date:
                schedules = TrainSchedule.objects.select_related("train").filter(
                    source_station__iexact=source,
                    destination_station__iexact=destination,
                    departure_time__date=journey_date,
                ).order_by('departure_time')
            else:
                search_error = 'Invalid date.'

    pending_schedule_ids = TrainTicket.objects.filter(
        user=request.user,
        status="pending",
        booking_time__gte=timezone.now() - RESERVATION_DURATION,
    ).values_list("train_schedule_id", flat=True).distinct()
    schedules_by_id = {schedule.id: schedule for schedule in schedules}
    for pending_schedule in TrainSchedule.objects.filter(
        id__in=pending_schedule_ids
    ).select_related("train"):
        schedules_by_id.setdefault(pending_schedule.id, pending_schedule)
    schedules = sorted(schedules_by_id.values(), key=lambda schedule: schedule.departure_time)

    train_list = []
    for sched in schedules:
        total_seats = sched.train.total_seats

        booked_seats = set(
            TrainTicket.objects.filter(
                train_schedule=sched, status="booked"
            ).values_list("seat_number", flat=True)
        )
        pending_seats = set(
            TrainTicket.objects.filter(
                train_schedule=sched,
                status="pending",
                booking_time__gte=timezone.now() - RESERVATION_DURATION,
            ).values_list("seat_number", flat=True)
        )

        classes = _build_classes(total_seats, sched, booked_seats | pending_seats)
        starting_price = min((c["price"] for c in classes), default=0)

        train_list.append({
            "schedule_id": sched.id,
            "train_name": sched.train.train_name,
            "train_number": sched.train.train_number,
            "departure_time": sched.departure_time,
            "arrival_time": sched.arrival_time,
            "source_station": sched.source_station,
            "destination_station": sched.destination_station,
            "starting_price": starting_price,
            "classes": classes,
            "booked_seats": booked_seats,
            "pending_seats": pending_seats,
        })

    pending_groups = {}
    pending_tickets = TrainTicket.objects.filter(
        user=request.user,
        status="pending",
        booking_time__gte=timezone.now() - RESERVATION_DURATION,
    ).select_related("train_schedule__train").order_by("booking_time")
    for pending_ticket in pending_tickets:
        group = pending_groups.setdefault(pending_ticket.confirmation_number, {
            "confirmation_number": pending_ticket.confirmation_number,
            "schedule": pending_ticket.train_schedule,
            "schedule_id": pending_ticket.train_schedule_id,
            "tickets": [],
            "seat_data": [],
            "amount": Decimal("0.00"),
            "expires_at": reservation_expiry([pending_ticket]),
        })
        group["tickets"].append(pending_ticket)
        group["expires_at"] = min(
            group["expires_at"], pending_ticket.booking_time + RESERVATION_DURATION
        )
        classes_for_schedule = _build_classes(
            pending_ticket.train_schedule.train.total_seats,
            pending_ticket.train_schedule,
            set(),
        )
        seat_class = next(
            (
                klass
                for klass in classes_for_schedule
                if any(pending_ticket.seat_number in coach["seats"] for coach in klass["coaches"])
            ),
            {"code": "", "price": 0},
        )
        seat_price = seat_class["price"]
        group["seat_data"].append({
            "seat_number": pending_ticket.seat_number,
            "class_code": seat_class["code"],
            "price": seat_price,
        })
        group["amount"] += Decimal(str(seat_price))

    return render(request, "Main_Interface/ticket_page.html", {
        "train_list": train_list,
        "stations": stations,
        "searched": searched,
        "search_error": search_error,
        "remaining_today": get_remaining_today(request.user),
        "today": today,
        "pending_reservations": list(pending_groups.values()),
        "pending_reservation_data": [
            {
                "schedule_id": group["schedule_id"],
                "confirmation_number": group["confirmation_number"],
                "expires_at": group["expires_at"].isoformat(),
                "seats": group["seat_data"],
            }
            for group in pending_groups.values()
        ],
    })


def train_schedule(request):
    now = timezone.now()
    today = timezone.localdate()
    source = request.GET.get("source", "").strip()
    destination = request.GET.get("destination", "").strip()
    date_str = request.GET.get("date", "").strip()
    stations = sorted(
        {
            station.strip()
            for station in (
                set(TrainSchedule.objects.values_list("source_station", flat=True))
                | set(TrainSchedule.objects.values_list("destination_station", flat=True))
            )
            if station and station.strip()
        },
        key=str.casefold,
    )

    searched = bool(source or destination or date_str)
    search_error = None
    schedules = TrainSchedule.objects.none()
    if searched:
        if not (source and destination and date_str):
            search_error = "Source, destination and date are required."
        else:
            try:
                journey_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                journey_date = None

            if journey_date and journey_date < today:
                search_error = "Journey date cannot be in the past."
            elif journey_date:
                schedule_filters = {
                    "source_station__iexact": source,
                    "destination_station__iexact": destination,
                    "departure_time__date": journey_date,
                }
                schedules = TrainSchedule.objects.select_related("train").filter(
                    **schedule_filters
                )
                if journey_date == today:
                    schedules = schedules.filter(departure_time__gte=now)
                schedules = schedules.order_by("departure_time")
            else:
                search_error = "Invalid date."
    else:
        schedules = TrainSchedule.objects.select_related("train").filter(
            departure_time__date=today,
            departure_time__gte=now,
        ).order_by("departure_time")

    schedule_page = Paginator(schedules, 10).get_page(request.GET.get("page"))

    def page_url(page_number):
        query = request.GET.copy()
        query["page"] = page_number
        return f"?{query.urlencode()}#upcoming-journeys"

    return render(request, "Main_Interface/train_schedule.html", {
        "schedules": schedule_page.object_list,
        "schedule_page": schedule_page,
        "previous_page_url": page_url(schedule_page.previous_page_number()) if schedule_page.has_previous() else None,
        "next_page_url": page_url(schedule_page.next_page_number()) if schedule_page.has_next() else None,
        "stations": stations,
        "searched": searched,
        "search_error": search_error,
        "today": today,
    })




redis_client = redis.Redis.from_url(settings.REDIS_URL)
LOCK_TIMEOUT = 10  # seconds
RESERVATION_DURATION = timedelta(minutes=5)

MAX_TICKETS_PER_DAY = 4
RESET_HOUR = 8  # 8:00 AM


def expire_pending_reservations():
    expired_rows = list(TrainTicket.objects.filter(
        status="pending",
        booking_time__lt=timezone.now() - RESERVATION_DURATION,
    ).values("id", "train_schedule_id", "user_id", "confirmation_number"))
    if not expired_rows:
        return

    expired_groups = set()
    expired_ticket_ids = []
    for row in expired_rows:
        confirmation_number = row["confirmation_number"]
        if confirmation_number:
            expired_groups.add((
                row["train_schedule_id"], row["user_id"], confirmation_number
            ))
        else:
            expired_ticket_ids.append(row["id"])

    expired_filter = Q(pk__in=expired_ticket_ids)
    for schedule_id, user_id, confirmation_number in expired_groups:
        expired_filter |= Q(
            train_schedule_id=schedule_id,
            user_id=user_id,
            confirmation_number=confirmation_number,
        )

    expired_tickets = TrainTicket.objects.filter(
        status="pending"
    ).filter(expired_filter)
    expired = list(expired_tickets.values_list("train_schedule_id", "seat_number"))
    expired_tickets.update(status="cancelled")
    channel_layer = get_channel_layer()
    for schedule_id, seat_number in expired:
        async_to_sync(channel_layer.group_send)(
            f"schedule_{schedule_id}",
            {"type": "seat_update", "seat_number": seat_number, "status": "released"},
        )


def normalize_pending_reservations(user, schedule_id=None):
    tickets = TrainTicket.objects.filter(
        user=user,
        status="pending",
        booking_time__gte=timezone.now() - RESERVATION_DURATION,
    ).order_by("train_schedule_id", "booking_time", "id")
    if schedule_id is not None:
        tickets = tickets.filter(train_schedule_id=schedule_id)

    grouped_tickets = {}
    for ticket in tickets:
        grouped_tickets.setdefault(ticket.train_schedule_id, []).append(ticket)

    for schedule_tickets in grouped_tickets.values():
        canonical_number = schedule_tickets[0].confirmation_number
        duplicate_ids = [
            ticket.id
            for ticket in schedule_tickets
            if ticket.confirmation_number != canonical_number
        ]
        if duplicate_ids:
            TrainTicket.objects.filter(id__in=duplicate_ids).update(
                confirmation_number=canonical_number
            )


def reservation_expiry(tickets):
    created_at = min(ticket.booking_time for ticket in tickets)
    return created_at + RESERVATION_DURATION


@login_required
def hold_seat(request, schedule_id):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Invalid method."}, status=405)

    expire_pending_reservations()
    normalize_pending_reservations(request.user, schedule_id)
    seat_number = request.POST.get("seat_number", "").strip()
    confirmation_number = request.POST.get("confirmation_number", "").strip()
    action = request.POST.get("action", "hold")
    if not seat_number or action not in {"hold", "release"}:
        return JsonResponse({"success": False, "message": "Invalid seat hold request."}, status=400)

    user_lock = redis_client.lock(
        f"lock:user:{request.user.id}:booking", timeout=LOCK_TIMEOUT
    )
    if not user_lock.acquire(blocking=True, blocking_timeout=3):
        return JsonResponse({"success": False, "message": "Another booking is in progress. Try again."}, status=429)

    seat_lock = redis_client.lock(
        f"lock:schedule:{schedule_id}:seat:{seat_number}", timeout=LOCK_TIMEOUT
    )
    if not seat_lock.acquire(blocking=True, blocking_timeout=3):
        safe_release(user_lock)
        return JsonResponse({"success": False, "message": "Seat is being selected by someone else."}, status=409)

    try:
        schedule = get_object_or_404(TrainSchedule.objects.select_related("train"), id=schedule_id)
        current_reservation = TrainTicket.objects.filter(
            user=request.user,
            train_schedule=schedule,
            status="pending",
            booking_time__gte=timezone.now() - RESERVATION_DURATION,
        ).order_by("booking_time", "id").first()
        if current_reservation:
            confirmation_number = current_reservation.confirmation_number

        active_tickets = TrainTicket.objects.filter(
            user=request.user,
            train_schedule=schedule,
            confirmation_number=confirmation_number,
            status="pending",
            booking_time__gte=timezone.now() - RESERVATION_DURATION,
        ) if confirmation_number else TrainTicket.objects.none()

        if action == "release":
            held_ticket = active_tickets.filter(seat_number=seat_number).first()
            if held_ticket:
                held_ticket.status = "cancelled"
                held_ticket.save(update_fields=["status"])
                async_to_sync(get_channel_layer().group_send)(
                    f"schedule_{schedule_id}",
                    {"type": "seat_update", "seat_number": seat_number, "status": "released"},
                )
            remaining_tickets = list(active_tickets.exclude(seat_number=seat_number))
            if not remaining_tickets:
                confirmation_number = ""
            return JsonResponse({
                "success": True,
                "confirmation_number": confirmation_number,
                "has_reservation": bool(remaining_tickets),
            })

        existing_ticket = active_tickets.filter(seat_number=seat_number).first()
        if existing_ticket:
            current_tickets = list(active_tickets)
            return JsonResponse({
                "success": True,
                "confirmation_number": confirmation_number,
                "expires_at": reservation_expiry(current_tickets).isoformat(),
            })

        valid_seats = {
            seat
            for klass in _build_classes(schedule.train.total_seats, schedule, set())
            for coach in klass["coaches"]
            for seat in coach["seats"]
        }
        if seat_number not in valid_seats:
            return JsonResponse({"success": False, "message": "Invalid seat."}, status=400)

        if TrainTicket.objects.filter(
            train_schedule=schedule,
            seat_number=seat_number,
            status__in=("pending", "booked"),
        ).exists():
            return JsonResponse({"success": False, "message": "Seat is no longer available."}, status=409)

        booking_start = get_booking_window_start()
        booked_today = TrainTicket.objects.filter(
            user=request.user, status="booked", booking_time__gte=booking_start
        ).count()
        pending_today = TrainTicket.objects.filter(
            user=request.user,
            status="pending",
            booking_time__gte=max(booking_start, timezone.now() - RESERVATION_DURATION),
        ).count()
        if booked_today + pending_today >= MAX_TICKETS_PER_DAY:
            return JsonResponse({"success": False, "message": "You have reached today's ticket limit."}, status=403)

        if not confirmation_number or not active_tickets.exists():
            confirmation_number = generate_confirmation_number()
        try:
            held_ticket = TrainTicket.objects.create(
                user=request.user,
                train_schedule=schedule,
                passenger_name=request.user.get_full_name() or request.user.username,
                passenger_phone_number=request.user.profile.phone_number,
                seat_number=seat_number,
                status="pending",
                confirmation_number=confirmation_number,
            )
        except IntegrityError:
            return JsonResponse({"success": False, "message": "Seat is no longer available."}, status=409)

        async_to_sync(get_channel_layer().group_send)(
            f"schedule_{schedule_id}",
            {"type": "seat_update", "seat_number": seat_number, "status": "pending"},
        )
        group_tickets = list(TrainTicket.objects.filter(
            user=request.user, confirmation_number=confirmation_number, status="pending"
        ))
        return JsonResponse({
            "success": True,
            "confirmation_number": confirmation_number,
            "expires_at": reservation_expiry(group_tickets).isoformat(),
        })
    finally:
        safe_release(seat_lock)
        safe_release(user_lock)


def get_booking_window_start():
    """Current booking day er shuru (8:00 AM) return kore."""
    now = timezone.localtime()
    start = now.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    return start


def safe_release(lock):
    """Lock timeout e expire hoye gele release() LockError dey, seta ignore kori."""
    try:
        lock.release()
    except LockError:
        pass


@login_required
def book_seat(request, schedule_id):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Invalid method."}, status=405)

    expire_pending_reservations()
    normalize_pending_reservations(request.user, schedule_id)

    # seat_number (single/repeated) ba seat_numbers ("A1,A2,A3") dutoi cholbe
    raw = request.POST.getlist("seat_number") + request.POST.get("seat_numbers", "").split(",")
    seats = list(dict.fromkeys(s.strip() for s in raw if s.strip()))

    if not seats:
        return JsonResponse({"success": False, "message": "seat_number required."}, status=400)
    if len(seats) > MAX_TICKETS_PER_DAY:
        return JsonResponse({"success": False, "message": f"Maximum {MAX_TICKETS_PER_DAY} seats per booking."}, status=400)

    user_lock = redis_client.lock(f"lock:user:{request.user.id}:booking", timeout=LOCK_TIMEOUT)
    if not user_lock.acquire(blocking=True, blocking_timeout=3):
        return JsonResponse({"success": False, "message": "Another booking in progress. Try again."}, status=429)

    seat_locks = []
    try:
        # sorted order-e lock nai, jate deadlock na hoy
        for s in sorted(seats):
            lk = redis_client.lock(f"lock:schedule:{schedule_id}:seat:{s}", timeout=LOCK_TIMEOUT)
            if not lk.acquire(blocking=True, blocking_timeout=3):
                return JsonResponse({"success": False, "message": f"Seat {s} is being booked by someone else. Try again."}, status=409)
            seat_locks.append(lk)

        try:
            schedule = TrainSchedule.objects.get(id=schedule_id)
        except TrainSchedule.DoesNotExist:
            return JsonResponse({"success": False, "message": "Schedule not found."}, status=404)

        reservation_start = timezone.now() - RESERVATION_DURATION
        TrainTicket.objects.filter(
            train_schedule=schedule,
            status="pending",
            booking_time__lt=reservation_start,
        ).update(status="cancelled")

        booked_count = TrainTicket.objects.filter(
            user=request.user,
            status="booked",
            booking_time__gte=get_booking_window_start(),
        ).count()
        booked_count += TrainTicket.objects.filter(
            user=request.user,
            status="pending",
            booking_time__gte=reservation_start,
        ).count()

        if booked_count > MAX_TICKETS_PER_DAY:
            left = max(0, MAX_TICKETS_PER_DAY - booked_count)
            return JsonResponse({
                "success": False,
                "message": f"Maximum Ticket limit is {MAX_TICKETS_PER_DAY}. You can book {left} more today.",
            }, status=403)

        taken = list(TrainTicket.objects.filter(
            train_schedule=schedule,
            seat_number__in=seats,
            status="booked",
        ).values_list("seat_number", flat=True))
        taken += list(TrainTicket.objects.filter(
            train_schedule=schedule,
            seat_number__in=seats,
            status="pending",
        ).exclude(user=request.user).values_list("seat_number", flat=True))
        if taken:
            return JsonResponse({"success": False, "message": f"Seat already booked: {', '.join(taken)}"}, status=409)

        seat_prices = {
            seat: klass["price"]
            for klass in _build_classes(schedule.train.total_seats, schedule, set())
            for coach in klass["coaches"]
            for seat in coach["seats"]
        }
        invalid_seats = [seat for seat in seats if seat not in seat_prices]
        if invalid_seats:
            return JsonResponse({"success": False, "message": f"Invalid seat: {', '.join(invalid_seats)}"}, status=400)

        held_tickets = list(TrainTicket.objects.filter(
            user=request.user,
            train_schedule=schedule,
            seat_number__in=seats,
            status="pending",
            booking_time__gte=reservation_start,
        ).order_by("booking_time"))
        if len(held_tickets) != len(seats):
            return JsonResponse({"success": False, "message": "Please select the seats again to start their five-minute reservation."}, status=409)

        confirmation_numbers = {ticket.confirmation_number for ticket in held_tickets}
        if len(confirmation_numbers) != 1:
            return JsonResponse({"success": False, "message": "Selected seats must belong to the same active reservation."}, status=409)
        confirmation_number = confirmation_numbers.pop()

        return JsonResponse({
            "success": True,
            "message": "Seats reserved. Continue to payment.",
            "confirmation_number": confirmation_number,
            "seats": seats,
            "total_amount": str(sum(seat_prices[seat] for seat in seats)),
            "expires_at": reservation_expiry(held_tickets).isoformat(),
            "remaining_today": max(0, MAX_TICKETS_PER_DAY - booked_count),
        })
    finally:
        for lk in seat_locks:
            safe_release(lk)
        safe_release(user_lock)


@login_required
def start_ticket_payment(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Invalid method."}, status=405)

    expire_pending_reservations()
    normalize_pending_reservations(request.user)
    confirmation_number = request.POST.get("confirmation_number", "").strip()
    tickets = list(TrainTicket.objects.filter(
        user=request.user,
        confirmation_number=confirmation_number,
        status="pending",
        booking_time__gte=timezone.now() - RESERVATION_DURATION,
    ).select_related("train_schedule__train"))
    if not tickets:
        return JsonResponse({"success": False, "expired": True, "message": "Reservation expired. Please select seats again."}, status=400)
    if not settings.SSL_COMMERZ_STORE_ID or not settings.SSL_COMMERZ_STORE_PASSWORD:
        return JsonResponse({"success": False, "message": "SSLCommerz credentials are not configured."}, status=503)

    schedule = tickets[0].train_schedule
    seat_prices = {
        seat: klass["price"]
        for klass in _build_classes(schedule.train.total_seats, schedule, set())
        for coach in klass["coaches"]
        for seat in coach["seats"]
    }
    amount = sum((Decimal(str(seat_prices[ticket.seat_number])) for ticket in tickets), Decimal("0.00"))
    if amount <= 0:
        return JsonResponse({"success": False, "message": "Unable to determine ticket fare."}, status=400)

    payment = TicketPayment.objects.create(
        user=request.user,
        payment_id=uuid.uuid4().hex,
        amount=amount,
        status="pending",
    )
    payment.tickets.set(tickets)
    expires_at = reservation_expiry(tickets)
    callback_url = request.build_absolute_uri
    payload = {
        "store_id": settings.SSL_COMMERZ_STORE_ID,
        "store_passwd": settings.SSL_COMMERZ_STORE_PASSWORD,
        "total_amount": f"{amount:.2f}",
        "currency": "BDT",
        "tran_id": payment.payment_id,
        "success_url": callback_url(reverse("payment_success")),
        "fail_url": callback_url(reverse("payment_failure")),
        "cancel_url": callback_url(reverse("payment_failure")),
        "ipn_url": callback_url(reverse("payment_success")),
        "cus_name": request.user.get_full_name() or request.user.username,
        "cus_email": request.user.email or "customer@example.com",
        "cus_phone": request.user.profile.phone_number,
        "cus_add1": "Bangladesh",
        "cus_city": "Dhaka",
        "cus_country": "Bangladesh",
        "shipping_method": "NO",
        "product_name": f"Train ticket {confirmation_number}",
        "product_category": "Ticket",
        "product_profile": "non-physical-goods",
    }
    try:
        response = requests.post(
            settings.SSL_COMMERZ_GATEWAY_URL,
            data=payload,
            timeout=15,
        )
        response.raise_for_status()
        gateway_response = response.json()
    except (requests.RequestException, ValueError):
        payment.status = "failed"
        payment.save(update_fields=["status"])
        return JsonResponse({"success": False, "message": "Could not connect to SSLCommerz. Please try again.", "expires_at": expires_at.isoformat()}, status=502)

    gateway_url = gateway_response.get("GatewayPageURL")
    if gateway_response.get("status") != "SUCCESS" or not gateway_url:
        payment.status = "failed"
        payment.save(update_fields=["status"])
        return JsonResponse({"success": False, "message": "SSLCommerz could not start the payment.", "expires_at": expires_at.isoformat()}, status=502)

    return JsonResponse({
        "success": True,
        "payment_url": gateway_url,
        "payment_id": payment.payment_id,
        "expires_at": expires_at.isoformat(),
    })


@login_required
def abandon_ticket_payment(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Invalid method."}, status=405)

    payment_id = request.POST.get("payment_id", "").strip()
    payment = TicketPayment.objects.filter(
        user=request.user,
        payment_id=payment_id,
        status="pending",
    ).first()
    if payment is None:
        return JsonResponse({"success": True, "abandoned": False})

    tickets = list(payment.tickets.filter(status="pending"))
    if tickets and reservation_expiry(tickets) <= timezone.now():
        expire_pending_reservations()
        payment.status = "cancelled"
        payment.save(update_fields=["status"])
        return JsonResponse({"success": True, "abandoned": True, "expired": True})

    payment.status = "cancelled"
    payment.save(update_fields=["status"])
    return JsonResponse({"success": True, "abandoned": True, "expired": False})


@csrf_exempt
def payment_success(request):
    callback_data = request.POST if request.method == "POST" else request.GET
    transaction_id = callback_data.get("tran_id", "").strip()
    validation_id = callback_data.get("val_id", "").strip()
    payment = get_object_or_404(TicketPayment, payment_id=transaction_id)

    if payment.status == "paid":
        ticket = payment.tickets.first()
        return _payment_confirmation_url(payment, ticket)
    if payment.status != "pending":
        return redirect("ticket_page")
    if not validation_id:
        return redirect("ticket_page")

    try:
        validation_response = requests.get(
            settings.SSL_COMMERZ_VALIDATION_URL,
            params={
                "val_id": validation_id,
                "store_id": settings.SSL_COMMERZ_STORE_ID,
                "store_passwd": settings.SSL_COMMERZ_STORE_PASSWORD,
                "format": "json",
            },
            timeout=15,
        )
        validation_response.raise_for_status()
        validation = validation_response.json()
        validated_amount = Decimal(str(validation.get("amount", "0")))
    except (requests.RequestException, ValueError, InvalidOperation):
        return redirect("ticket_page")

    if (
        validation.get("status") not in {"VALID", "VALIDATED"}
        or validation.get("tran_id") != payment.payment_id
        or validation.get("currency") != "BDT"
        or validated_amount != payment.amount
    ):
        return redirect("ticket_page")

    expire_pending_reservations()
    newly_booked_tickets = None
    with transaction.atomic():
        payment = TicketPayment.objects.select_for_update().get(pk=payment.pk)
        if payment.status != "paid":
            tickets = list(payment.tickets.select_for_update().filter(status="pending"))
            if not tickets:
                payment.status = "failed"
                payment.save(update_fields=["status"])
                return redirect("home_page")
            confirmation_number = tickets[0].confirmation_number or generate_confirmation_number()
            for ticket in tickets:
                ticket.status = "booked"
                ticket.confirmation_number = confirmation_number
                ticket.save(update_fields=["status", "confirmation_number"])
            payment.status = "paid"
            payment.payment_time = timezone.now()
            payment.save(update_fields=["status", "payment_time"])
            newly_booked_tickets = tickets

    if newly_booked_tickets and payment.user.email:
        try:
            schedule = newly_booked_tickets[0].train_schedule
            ticket_data = _build_ticket_details(schedule, newly_booked_tickets, request)
            profile = getattr(payment.user, "profile", None)
            pdf_data = create_ticket_pdf(
                ticket_data,
                schedule,
                _mask(profile.nid if profile else ""),
            )
            email = EmailMessage(
                subject=f"Your RailwaySheba e-ticket - {ticket_data['confirmation_numbers'][0]}",
                body="Your train ticket is confirmed. Your A4 e-ticket PDF is attached.",
                to=[payment.user.email],
            )
            email.attach(
                f"RailwaySheba-Ticket-{ticket_data['confirmation_numbers'][0]}.pdf",
                pdf_data,
                "application/pdf",
            )
            email.send()
        except Exception:
            logger.exception("Could not email ticket for payment %s", payment.payment_id)

    channel_layer = get_channel_layer()
    for ticket in payment.tickets.all():
        async_to_sync(channel_layer.group_send)(
            f"schedule_{ticket.train_schedule_id}",
            {"type": "seat_update", "seat_number": ticket.seat_number, "status": "booked"},
        )
    return _payment_confirmation_url(payment, payment.tickets.first())


def _payment_confirmation_url(payment, ticket):
    if ticket is None:
        return redirect("ticket_page")
    return redirect(
        f"{reverse('seat_confarmation', args=[ticket.train_schedule_id])}"
        f"?c={ticket.confirmation_number or ''}"
    )


@csrf_exempt
def payment_failure(request):
    callback_data = request.POST if request.method == "POST" else request.GET
    transaction_id = callback_data.get("tran_id", "").strip()
    if transaction_id:
        payment = TicketPayment.objects.filter(payment_id=transaction_id, status="pending").first()
        if payment:
            payment.status = "failed"
            payment.save(update_fields=["status"])
            tickets = list(payment.tickets.filter(status="pending"))
            if tickets and reservation_expiry(tickets) <= timezone.now():
                expire_pending_reservations()
                return redirect("home_page")
            if tickets:
                schedule = tickets[0].train_schedule
                query = urlencode({
                    "source": schedule.source_station,
                    "destination": schedule.destination_station,
                    "date": timezone.localtime(schedule.departure_time).date().isoformat(),
                })
                return redirect(
                    f"{reverse('ticket_page')}?{query}"
                )
    return redirect("ticket_page")



from Railway_Admin.models import TrainInformation, TrainSchedule, TrainTicket, generate_confirmation_number
from django.urls import reverse

def _mask(value, head=3, tail=3):
    value = str(value or "")
    if len(value) <= head + tail:
        return value
    return value[:head] + "*" * (len(value) - head - tail) + value[-tail:]


@login_required
def sit_confarmation_page(request, schedule_id):
    schedule = get_object_or_404(
        TrainSchedule.objects.select_related("train"), id=schedule_id
    )
    codes = [c.strip() for c in request.GET.get("c", "").split(",") if c.strip()]

    ticket = None
    if codes:
        seat_info = {}
        for c in _build_classes(schedule.train.total_seats, schedule, set()):
            for coach in c["coaches"]:
                for s in coach["seats"]:
                    seat_info[s] = (c["label"], c["price"])

        rows = list(
            TrainTicket.objects.filter(
                user=request.user,
                train_schedule=schedule,
                status="booked",
                confirmation_number__in=codes,
            ).order_by("seat_number")
        )

        if rows:
            ticket = _build_ticket_details(schedule, rows, request, seat_info)

    return render(request, "Main_Interface/sit_confarmation.html", {
        "schedule": schedule,
        "ticket": ticket,
        "nid_masked": _mask(getattr(request.user.profile, "nid", "")),
    })


def _build_ticket_details(schedule, rows, request, seat_info=None):
    if seat_info is None:
        seat_info = {}
        for ticket_class in _build_classes(schedule.train.total_seats, schedule, set()):
            for coach in ticket_class["coaches"]:
                for seat in coach["seats"]:
                    seat_info[seat] = (ticket_class["label"], ticket_class["price"])

    classes = []
    total_fare = 0
    for row in rows:
        label, price = seat_info.get(row.seat_number, ("-", 0))
        if label not in classes:
            classes.append(label)
        total_fare += price

    confirmation_numbers = list(dict.fromkeys(
        row.confirmation_number for row in rows if row.confirmation_number
    ))
    verify_base = request.build_absolute_uri(reverse("varification_ticket"))
    first = rows[0]
    return {
        "passenger_name": first.passenger_name,
        "phone_masked": _mask(first.passenger_phone_number),
        "issue_time": min(row.booking_time for row in rows),
        "seats": ", ".join(row.seat_number for row in rows),
        "seat_count": len(rows),
        "class_name": ", ".join(classes),
        "total_fare": total_fare,
        "confirmation_numbers": confirmation_numbers,
        "verify_url": f"{verify_base}?confirmation_number={confirmation_numbers[0]}",
    }



def get_remaining_today(user):
    booking_start = get_booking_window_start()
    used = TrainTicket.objects.filter(
        user=user,
        status="booked",
        booking_time__gte=booking_start,
    ).count()
    used += TrainTicket.objects.filter(
        user=user,
        status="pending",
        booking_time__gte=max(booking_start, timezone.now() - RESERVATION_DURATION),
    ).count()
    return max(0, MAX_TICKETS_PER_DAY - used)



def varification_ticket(request):
    code = request.GET.get("confirmation_number", "").strip()
    ticket = None
    checked = bool(code)

    if code:
        ticket = (
            TrainTicket.objects
            .select_related("train_schedule__train")
            .filter(confirmation_number=code, status="booked")
            .first()
        )

    return render(request, "Main_Interface/varifaction_ticket.html", {
        "code": code,
        "checked": checked,
        "ticket": ticket,
    })
