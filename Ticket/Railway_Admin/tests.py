from datetime import date, datetime, time, timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from Main_Interface.models import Profile
from .models import AdminProfile, TrainInformation, TrainSchedule


class AdminPhoneNumberUniquenessTests(TestCase):
	def setUp(self):
		superuser = get_user_model().objects.create_superuser(
			username="phone-number-superuser",
			email="phone-number-superuser@example.com",
			password="test-password",
		)
		self.client.force_login(superuser)

	def test_admin_creation_rejects_passenger_phone_number(self):
		passenger = get_user_model().objects.create_user(
			username="passenger-with-phone",
			password="test-password",
		)
		Profile.objects.create(
			user=passenger,
			phone_number="01700000022",
			nid="1234567894",
		)

		response = self.client.post(
			reverse("admin_profile_create"),
			{
				"username": "new-admin",
				"full_name": "New Admin",
				"email": "new-admin@example.com",
				"designation": "Officer",
				"phone_number": "01700000022",
			},
		)

		self.assertFalse(response.json()["success"])
		self.assertIn("passenger account", response.json()["error"])
		self.assertFalse(AdminProfile.objects.filter(phone_number="01700000022").exists())

	@patch("Railway_Admin.views.send_mail")
	def test_admin_creation_does_not_require_username(self, send_mail):
		response = self.client.post(
			reverse("admin_profile_create"),
			{
				"full_name": "Created Admin",
				"email": "created-admin@example.com",
				"designation": "Officer",
				"phone_number": "01700000023",
			},
		)

		self.assertTrue(response.json()["success"])
		profile = AdminProfile.objects.get(phone_number="01700000023")
		self.assertTrue(profile.user.username.startswith("admin_"))
		send_mail.assert_called_once()
		details = self.client.get(
			reverse("admin_profile_get", args=[profile.pk]),
		).json()
		self.assertNotIn("username", details)
		temporary_password = send_mail.call_args.args[1].split(
			"One-time password: ",
			1,
		)[1].splitlines()[0]
		login_response = self.client.post(
			reverse("superuser_login"),
			{
				"phone_number": profile.phone_number,
				"password": temporary_password,
			},
			HTTP_X_REQUESTED_WITH="XMLHttpRequest",
		)
		self.assertTrue(login_response.json()["success"])
		self.assertEqual(
			login_response.json()["redirect_url"],
			reverse("admin_password_change"),
		)

	def test_admin_edit_does_not_require_username(self):
		user = get_user_model().objects.create_user(
			username="existing-admin",
			password="test-password",
		)
		profile = AdminProfile.objects.create(
			user=user,
			full_name="Existing Officer",
			email="existing-admin@example.com",
			designation="Officer",
			phone_number="01700000009",
		)
		original_username = profile.user.username

		response = self.client.post(
			reverse("admin_profile_edit", args=[profile.pk]),
			{
				"full_name": "Updated Officer",
				"email": profile.email,
				"designation": profile.designation,
				"phone_number": profile.phone_number,
			},
		)

		self.assertTrue(response.json()["success"])
		profile.refresh_from_db()
		self.assertEqual(profile.full_name, "Updated Officer")
		self.assertEqual(profile.user.username, original_username)

	def test_add_admin_form_has_no_username_field(self):
		response = self.client.get(reverse("superuser_dashboard"))

		self.assertContains(response, "Full name")
		self.assertContains(response, "Phone number")
		self.assertNotContains(response, "Username")


class DashboardLoginReturnTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(
			username="dashboard-officer",
			password="test-password",
		)
		AdminProfile.objects.create(
			user=self.user,
			full_name="Dashboard Officer",
			email="dashboard-officer@example.com",
			designation="Officer",
			phone_number="01700000009",
			one_time_password=True,
		)

	def test_admin_phone_login_goes_to_admin_dashboard(self):
		superuser_dashboard_url = reverse("superuser_dashboard")
		response = self.client.post(
			reverse("superuser_login"),
			{
				"phone_number": "01700000009",
				"password": "test-password",
				"next": superuser_dashboard_url,
			},
			HTTP_X_REQUESTED_WITH="XMLHttpRequest",
		)

		self.assertEqual(response.status_code, 200)
		admin_dashboard_url = reverse("admin_dashboard")
		self.assertEqual(response.json()["redirect_url"], admin_dashboard_url)
		self.assertRedirects(
			self.client.get(superuser_dashboard_url),
			admin_dashboard_url,
		)

		dashboard_response = self.client.get(admin_dashboard_url)
		self.assertEqual(dashboard_response.status_code, 200)
		self.assertContains(dashboard_response, "Railway Management Dashboard")

	def test_superuser_can_still_log_in_with_username(self):
		get_user_model().objects.create_superuser(
			username="root",
			email="root@example.com",
			password="test-password",
		)

		response = self.client.post(
			reverse("superuser_login"),
			{
				"phone_number": "root",
				"password": "test-password",
			},
			HTTP_X_REQUESTED_WITH="XMLHttpRequest",
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			response.json()["redirect_url"],
			reverse("superuser_dashboard"),
		)


class ScheduleImportTests(TestCase):
	headers = [
		"Schedule ID",
		"Train Number",
		"Source Station",
		"Destination Station",
		"Departure Time",
		"Arrival Time",
		"AC Ticket Price",
		"Snigdha Ticket Price",
		"S. Chair Ticket Price",
		"Travel Date",
	]

	def setUp(self):
		user = get_user_model().objects.create_user(
			username="schedule-import-admin",
			password="test-password",
		)
		AdminProfile.objects.create(
			user=user,
			full_name="Schedule Import Admin",
			email="schedule-import-admin@example.com",
			designation="Officer",
			phone_number="01700000010",
			one_time_password=True,
		)
		self.client.force_login(user)
		self.train = TrainInformation.objects.create(
			train_number="T-301",
			train_name="Import Test Express",
			total_seats=40,
		)

	def _excel_upload(self, headers, rows):
		workbook = Workbook()
		worksheet = workbook.active
		worksheet.append(headers)
		for row in rows:
			worksheet.append(row)
		output = BytesIO()
		workbook.save(output)
		return SimpleUploadedFile(
			"schedules.xlsx",
			output.getvalue(),
			content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		)

	def _schedule_row(self, departure, arrival, travel_date, *prices):
		return [
			None,
			self.train.train_number,
			"Dhaka",
			"Chattogram",
			departure,
			arrival,
			*prices,
			travel_date,
		]

	def test_imports_3000_native_and_string_times_with_overnight_trips(self):
		rows = []
		for index in range(3000):
			travel_date = date(2026, 1, 1) + timedelta(days=index)
			if index == 0:
				departure, arrival = time(23, 30), time(1, 15)
				date_value = travel_date
			elif index == 1:
				departure, arrival = "08:00", "08:00"
				date_value = travel_date.strftime("%d/%m/%Y")
			elif index % 2:
				departure, arrival = "09:30", "11:00"
				date_value = travel_date.isoformat()
			else:
				departure, arrival = time(9, 30), time(11, 0)
				date_value = travel_date
			row = self._schedule_row(
				departure,
				arrival,
				date_value,
				1000,
				700.0,
				None,
			)
			row[0] = f"SC{index + 101:05d}"
			rows.append(row)

		response = self.client.post(
			reverse("schedule_import"),
			{"file": self._excel_upload(self.headers, rows)},
		)

		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response.json()["created"], 3000)
		self.assertEqual(TrainSchedule.objects.filter(train=self.train).count(), 3000)

		first, second = TrainSchedule.objects.filter(train=self.train).order_by("pk")[:2]
		self.assertEqual(first.arrival_time - first.departure_time, timedelta(hours=1, minutes=45))
		self.assertEqual(second.arrival_time - second.departure_time, timedelta(days=1))
		self.assertEqual(first.ac_ticket_price, 1000)
		self.assertEqual(first.singdha_ticket_price, 700)
		self.assertIsNone(first.s_chair_ticket_price)

	def test_invalid_row_prevents_partial_import(self):
		rows = [
			self._schedule_row(
				time(9, 0),
				time(10, 0),
				date(2026, 11, 18),
				1000,
				700,
				500,
			),
			self._schedule_row(
				"11:00",
				"12:00",
				"2026-11-18",
				1000.5,
				700,
				500,
			),
			self._schedule_row(
				"13:00",
				"14:00",
				"2026-11-18",
				1000,
				700,
				-1,
			),
		]

		response = self.client.post(
			reverse("schedule_import"),
			{"file": self._excel_upload(self.headers, rows)},
		)

		self.assertEqual(response.status_code, 400)
		self.assertEqual(TrainSchedule.objects.filter(train=self.train).count(), 0)

	def test_schedule_id_accepts_whitespace_blank_and_integer_like_text(self):
		departure_time = timezone.now() + timedelta(days=2)
		existing_schedule = TrainSchedule.objects.create(
			train=self.train,
			source_station="Old Source",
			destination_station="Old Destination",
			departure_time=departure_time,
			arrival_time=departure_time + timedelta(hours=2),
		)
		new_row = self._schedule_row(
			"09:00",
			"10:00",
			"2026-11-18",
			1000,
			700,
			500,
		)
		new_row[0] = "   "
		update_row = self._schedule_row(
			"11:00",
			"12:00",
			"2026-11-19",
			1200,
			800,
			600,
		)
		update_row[0] = f"{existing_schedule.pk}.0"

		response = self.client.post(
			reverse("schedule_import"),
			{"file": self._excel_upload(self.headers, [new_row, update_row])},
		)

		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response.json(), {"success": True, "created": 1, "updated": 1})
		self.assertEqual(TrainSchedule.objects.filter(train=self.train).count(), 2)

	def test_duplicate_external_schedule_ids_are_rejected(self):
		first_row = self._schedule_row(
			"09:00",
			"10:00",
			"2026-11-18",
			1000,
			700,
			500,
		)
		second_row = self._schedule_row(
			"11:00",
			"12:00",
			"2026-11-18",
			1000,
			700,
			500,
		)
		first_row[0] = second_row[0] = "SC00101"

		response = self.client.post(
			reverse("schedule_import"),
			{"file": self._excel_upload(self.headers, [first_row, second_row])},
		)

		self.assertEqual(response.status_code, 400)
		self.assertEqual(TrainSchedule.objects.filter(train=self.train).count(), 0)

	def test_import_accepts_exported_full_datetimes_without_travel_date(self):
		headers = self.headers[:-1]
		row = [
			None,
			self.train.train_number,
			"Dhaka",
			"Chattogram",
			datetime(2026, 11, 18, 9, 0),
			datetime(2026, 11, 18, 11, 0),
			1000,
			700,
			500,
		]

		response = self.client.post(
			reverse("schedule_import"),
			{"file": self._excel_upload(headers, [row])},
		)

		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response.json()["created"], 1)
