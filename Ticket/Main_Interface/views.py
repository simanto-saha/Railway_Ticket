import random
from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.hashers import make_password
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

from .models import Profile, VarificationCode


def home_page(request):
    return render(request, "Main_Interface/home_page.html")


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

    # User create করার আগে সব ডেটা session এ রাখা হলো
    request.session['pending_registration'] = {
        'full_name': full_name,
        'email': email,
        'phone_number': phone_number,
        'nid': nid,
        'password': make_password(password),  # hash করে রাখা হলো, plain text না
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

    # এখন verify হয়ে গেছে, তাই User + Profile তৈরি হবে
    user = User.objects.create(
        username=pending_data['email'],
        email=pending_data['email'],
        first_name=pending_data['full_name'],
        password=pending_data['password'],  # আগেই hash করা আছে
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
        return JsonResponse({'success': True, 'message': 'Logged in successfully!', 'redirect_url': '/'})
    else:
        return JsonResponse({'success': False, 'message': 'Invalid phone number or password.'})


def logout_view(request):
    logout(request)
    return JsonResponse({'success': True, 'message': 'Logged out successfully.', 'redirect_url': '/'})