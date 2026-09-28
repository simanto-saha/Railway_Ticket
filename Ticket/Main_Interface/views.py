import random
import string
from django.shortcuts import redirect, render, get_object_or_404
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
import redis
from django.db import transaction
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from django.db import IntegrityError, transaction

from redis.exceptions import LockError


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
        .select_related("train_schedule")
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
    source = request.GET.get('source', '').strip()
    destination = request.GET.get('destination', '').strip()
    date_str = request.GET.get('date', '').strip()

    stations = sorted(set(
        TrainSchedule.objects.values_list('source_station', flat=True)
    ) | set(
        TrainSchedule.objects.values_list('destination_station', flat=True)
    ))

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

            if journey_date:
                schedules = TrainSchedule.objects.select_related("train").filter(
                    source_station__iexact=source,
                    destination_station__iexact=destination,
                    departure_time__date=journey_date,
                ).order_by('departure_time')
            else:
                search_error = 'Invalid date.'

    train_list = []
    for sched in schedules:
        total_seats = sched.train.total_seats

        booked_seats = set(
            TrainTicket.objects.filter(
                train_schedule=sched, status="booked"
            ).values_list("seat_number", flat=True)
        )

        classes = _build_classes(total_seats, sched, booked_seats)
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
        })

    return render(request, "Main_Interface/ticket_page.html", {
        "train_list": train_list,
        "stations": stations,
        "searched": searched,
        "search_error": search_error,
        "remaining_today": get_remaining_today(request.user),
    })


def train_schedule(request):
    return render(request, "Main_Interface/train_schedule.html", {
        'trains': TrainInformation.objects.all().order_by('train_number'),
        'schedules': TrainSchedule.objects.select_related('train').order_by('departure_time'),
    })




redis_client = redis.Redis.from_url(settings.REDIS_URL)
LOCK_TIMEOUT = 10  # seconds

MAX_TICKETS_PER_DAY = 4
RESET_HOUR = 8  # 8:00 AM


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

        booked_count = TrainTicket.objects.filter(
            user=request.user, status="booked",
            booking_time__gte=get_booking_window_start(),
        ).count()

        if booked_count + len(seats) > MAX_TICKETS_PER_DAY:
            left = max(0, MAX_TICKETS_PER_DAY - booked_count)
            return JsonResponse({
                "success": False,
                "message": f"Maximum Ticket limit is {MAX_TICKETS_PER_DAY}. You can book {left} more today.",
            }, status=403)

        taken = list(TrainTicket.objects.filter(
            train_schedule=schedule, seat_number__in=seats, status="booked"
        ).values_list("seat_number", flat=True))
        if taken:
            return JsonResponse({"success": False, "message": f"Seat already booked: {', '.join(taken)}"}, status=409)

        confirmation_number = generate_confirmation_number()  # sob seat-er jonno 1 ta
        try:
            with transaction.atomic():  # ekta fail hole kono seat-i book hobe na
                for s in seats:
                    TrainTicket.objects.create(
                        user=request.user,
                        train_schedule=schedule,
                        passenger_name=request.user.get_full_name() or request.user.username,
                        passenger_phone_number=request.user.profile.phone_number,
                        seat_number=s,
                        status="booked",
                        confirmation_number=confirmation_number,
                    )
        except IntegrityError:
            return JsonResponse({"success": False, "message": "Seat already booked."}, status=409)

        channel_layer = get_channel_layer()
        for s in seats:
            async_to_sync(channel_layer.group_send)(
                f"schedule_{schedule_id}",
                {"type": "seat_update", "seat_number": s, "status": "booked"},
            )

        return JsonResponse({
            "success": True,
            "message": "Seat booked successfully.",
            "confirmation_number": confirmation_number,
            "seats": seats,
            "remaining_today": MAX_TICKETS_PER_DAY - booked_count - len(seats),
        })
    finally:
        for lk in seat_locks:
            safe_release(lk)
        safe_release(user_lock)



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
            first = rows[0]
            classes = []
            total_fare = 0
            for r in rows:
                label, price = seat_info.get(r.seat_number, ("-", 0))
                if label not in classes:
                    classes.append(label)
                total_fare += price

            verify_base = request.build_absolute_uri(reverse("varification_ticket"))
            ticket = {
                "passenger_name": first.passenger_name,
                "phone_masked": _mask(first.passenger_phone_number),
                "issue_time": min(r.booking_time for r in rows),
                "seats": ", ".join(r.seat_number for r in rows),
                "seat_count": len(rows),
                "class_name": ", ".join(classes),
                "total_fare": total_fare,
                "verify_url": f"{verify_base}?confirmation_number={rows[0].confirmation_number}",
                
            }

    return render(request, "Main_Interface/sit_confarmation.html", {
        "schedule": schedule,
        "ticket": ticket,
        "nid_masked": _mask(getattr(request.user.profile, "nid", "")),
    })



def get_remaining_today(user):
    used = TrainTicket.objects.filter(
        user=user,
        status="booked",
        booking_time__gte=get_booking_window_start(),
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
