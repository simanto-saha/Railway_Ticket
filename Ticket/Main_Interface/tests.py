from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core import mail
from django.http import HttpResponse
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from Main_Interface.models import Profile, VarificationCode
from Main_Interface import views
from Railway_Admin.models import AdminProfile, TrainInformation, TrainSchedule, TrainTicket, TicketPayment


class SharedPhoneNumberAccountTests(TestCase):
	def test_passenger_registration_rejects_admin_phone_number(self):
		admin_user = User.objects.create_user(
			username="admin-with-phone",
			password="test-password",
		)
		AdminProfile.objects.create(
			user=admin_user,
			full_name="Admin",
			email="admin-with-phone@example.com",
			designation="Officer",
			phone_number="01700000020",
		)

		response = self.client.post(
			reverse("register"),
			{
				"full_name": "New Passenger",
				"email": "new-passenger@example.com",
				"phone_number": "01700000020",
				"nid_number": "1234567892",
				"password": "test-password",
				"confirm_password": "test-password",
			},
		)

		self.assertFalse(response.json()["success"])
		self.assertIn("admin account", response.json()["message"])

	def test_verification_rejects_admin_phone_added_after_registration(self):
		registration_data = {
			"full_name": "New Passenger",
			"email": "pending-passenger@example.com",
			"phone_number": "01700000021",
			"nid_number": "1234567893",
			"password": "test-password",
			"confirm_password": "test-password",
		}
		with patch("Main_Interface.views.send_mail"):
			register_response = self.client.post(
				reverse("register"),
				registration_data,
			)

		self.assertTrue(register_response.json()["success"])
		admin_user = User.objects.create_user(
			username="admin-claimed-phone",
			password="test-password",
		)
		AdminProfile.objects.create(
			user=admin_user,
			full_name="Admin",
			email="admin-claimed-phone@example.com",
			designation="Officer",
			phone_number=registration_data["phone_number"],
		)
		code = VarificationCode.objects.get(email=registration_data["email"])

		response = self.client.post(
			reverse("varification_code"),
			{"code": code.code},
		)

		self.assertFalse(response.json()["success"])
		self.assertFalse(Profile.objects.filter(
			phone_number=registration_data["phone_number"],
		).exists())
		code.refresh_from_db()
		self.assertFalse(code.is_used)


class SeatReservationTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="rider", password="test-password")
		Profile.objects.create(user=self.user, phone_number="01700000001", nid="1234567890")
		train = TrainInformation.objects.create(
			train_number="T-101",
			train_name="Test Express",
			total_seats=10,
		)
		self.schedule = TrainSchedule.objects.create(
			train=train,
			departure_time=timezone.now() + timedelta(days=1),
			arrival_time=timezone.now() + timedelta(days=1, hours=2),
			source_station="Dhaka",
			destination_station="Chattogram",
			ac_ticket_price=1000,
			singdha_ticket_price=700,
			s_chair_ticket_price=500,
		)
		self.client.force_login(self.user)

	def test_home_page_excludes_departed_trains(self):
		now = timezone.now()
		past_schedule = TrainSchedule.objects.create(
			train=self.schedule.train,
			departure_time=now - timedelta(minutes=1),
			arrival_time=now + timedelta(hours=1),
			source_station="Dhaka",
			destination_station="Chattogram",
		)

		with patch("Main_Interface.views.timezone.now", return_value=now):
			response = self.client.get(reverse("home_page"))

		featured_ids = [schedule.id for schedule in response.context["featured_schedules"]]
		self.assertIn(self.schedule.id, featured_ids)
		self.assertNotIn(past_schedule.id, featured_ids)

	def test_home_page_remains_available_when_redis_is_unavailable(self):
		connection_error = views.redis.exceptions.ConnectionError("Redis unavailable")
		with patch.object(views.cache, "add", side_effect=connection_error), patch.object(
			views.cache, "get", side_effect=connection_error
		), patch.object(views.cache, "set", side_effect=connection_error), patch(
			"Main_Interface.views.render", return_value=HttpResponse(status=200)
		) as render:
			response = self.client.get(reverse("home_page"))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			[schedule.id for schedule in render.call_args.args[2]["featured_schedules"]],
			[self.schedule.id],
		)

	def test_train_schedule_loads_stations_when_redis_is_unavailable(self):
		connection_error = views.redis.exceptions.ConnectionError("Redis unavailable")
		with patch.object(views.cache, "get", side_effect=connection_error), patch.object(
			views.cache, "set", side_effect=connection_error
		), patch(
			"Main_Interface.views.render", return_value=HttpResponse(status=200)
		) as render:
			response = self.client.get(reverse("train_schedule"))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(render.call_args.args[2]["stations"], ["Chattogram", "Dhaka"])

	def test_register_saves_session_when_redis_is_unavailable(self):
		with patch.object(
			views.cache,
			"has_key",
			side_effect=views.redis.exceptions.ConnectionError("Redis unavailable"),
		), patch("Main_Interface.views.send_mail"):
			response = self.client.post(
				reverse("register"),
				{
					"full_name": "New Rider",
					"email": "new-rider@example.com",
					"phone_number": "01700000002",
					"nid_number": "1234567891",
					"password": "test-password",
					"confirm_password": "test-password",
				},
			)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(self.client.session["pending_email"], "new-rider@example.com")

	def test_seat_hold_deadline_is_five_minutes_from_server_timestamp(self):
		with patch.object(views.redis_client, "lock") as lock_factory, patch(
			"Main_Interface.views.async_to_sync", side_effect=lambda function: lambda *args, **kwargs: None
		):
			lock_factory.return_value.acquire.return_value = True
			response = self.client.post(
				reverse("hold_seat", args=[self.schedule.id]),
				{"seat_number": "A1", "action": "hold"},
			)
			second_response = self.client.post(
				reverse("hold_seat", args=[self.schedule.id]),
				{
					"seat_number": "C1",
					"confirmation_number": response.json()["confirmation_number"],
					"action": "hold",
				},
			)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(second_response.status_code, 200)
		held_ticket = TrainTicket.objects.get(seat_number="A1", train_schedule=self.schedule)
		second_ticket = TrainTicket.objects.get(seat_number="C1", train_schedule=self.schedule)
		self.assertEqual(held_ticket.status, "pending")
		self.assertEqual(
			second_response.json()["confirmation_number"],
			response.json()["confirmation_number"],
		)
		expires_at = timezone.datetime.fromisoformat(response.json()["expires_at"])
		second_expires_at = timezone.datetime.fromisoformat(second_response.json()["expires_at"])
		self.assertEqual(expires_at, second_expires_at)
		self.assertAlmostEqual(
			(expires_at - held_ticket.booking_time).total_seconds(),
			views.RESERVATION_DURATION.total_seconds(),
			delta=1,
		)
		self.assertLess(second_ticket.booking_time + views.RESERVATION_DURATION, expires_at)

	def test_payment_failure_keeps_unexpired_seats_for_retry(self):
		ticket = TrainTicket.objects.create(
			user=self.user,
			train_schedule=self.schedule,
			passenger_name="Rider",
			passenger_phone_number="01700000001",
			seat_number="A1",
			status="pending",
			confirmation_number="123456789012",
		)
		payment = TicketPayment.objects.create(
			user=self.user,
			payment_id=uuid4().hex,
			amount="1000.00",
			status="pending",
		)
		payment.tickets.add(ticket)

		response = self.client.post(
			reverse("payment_failure"),
			{"tran_id": payment.payment_id},
		)

		ticket.refresh_from_db()
		payment.refresh_from_db()
		self.assertEqual(ticket.status, "pending")
		self.assertEqual(payment.status, "failed")
		self.assertIn("source=Dhaka", response.url)
		self.assertIn("destination=Chattogram", response.url)

	def test_successful_payment_emails_a4_ticket_once(self):
		self.user.email = "rider@example.com"
		self.user.save(update_fields=["email"])
		ticket = TrainTicket.objects.create(
			user=self.user,
			train_schedule=self.schedule,
			passenger_name="Rider",
			passenger_phone_number="01700000001",
			seat_number="A1",
			status="pending",
			confirmation_number="123456789012",
		)
		payment = TicketPayment.objects.create(
			user=self.user,
			payment_id=uuid4().hex,
			amount="1000.00",
			status="pending",
		)
		payment.tickets.add(ticket)
		validation = type("ValidationResponse", (), {
			"raise_for_status": lambda self: None,
			"json": lambda self: {
				"status": "VALID",
				"tran_id": payment.payment_id,
				"currency": "BDT",
				"amount": "1000.00",
			},
		})()

		with patch("Main_Interface.views.requests.get", return_value=validation), patch(
			"Main_Interface.views.async_to_sync", side_effect=lambda function: lambda *args, **kwargs: None
		):
			response = self.client.get(
				reverse("payment_success"),
				{"tran_id": payment.payment_id, "val_id": "validation-id"},
			)
			self.assertEqual(response.status_code, 302)
			self.assertEqual(len(mail.outbox), 1)
			attachment_name, attachment_data, content_type = mail.outbox[0].attachments[0]
			self.assertEqual(attachment_name, "RailwaySheba-Ticket-123456789012.pdf")
			self.assertEqual(content_type, "application/pdf")
			self.assertTrue(attachment_data.startswith(b"%PDF-"))

			self.client.get(
				reverse("payment_success"),
				{"tran_id": payment.payment_id, "val_id": "validation-id"},
			)

		self.assertEqual(len(mail.outbox), 1)

	def test_abandoning_gateway_attempt_keeps_seat_hold_pending(self):
		ticket = TrainTicket.objects.create(
			user=self.user,
			train_schedule=self.schedule,
			passenger_name="Rider",
			passenger_phone_number="01700000001",
			seat_number="A1",
			status="pending",
			confirmation_number="123456789012",
		)
		payment = TicketPayment.objects.create(
			user=self.user,
			payment_id=uuid4().hex,
			amount="1000.00",
			status="pending",
		)
		payment.tickets.add(ticket)

		response = self.client.post(
			reverse("abandon_ticket_payment"),
			{"payment_id": payment.payment_id},
		)

		ticket.refresh_from_db()
		payment.refresh_from_db()
		self.assertEqual(response.status_code, 200)
		self.assertEqual(payment.status, "cancelled")
		self.assertEqual(ticket.status, "pending")

	def test_expired_pending_seat_is_cancelled_and_broadcast_released(self):
		ticket = TrainTicket.objects.create(
			user=self.user,
			train_schedule=self.schedule,
			passenger_name="Rider",
			passenger_phone_number="01700000001",
			seat_number="A1",
			status="pending",
			confirmation_number="123456789012",
		)
		TrainTicket.objects.filter(pk=ticket.pk).update(
			booking_time=timezone.now() - views.RESERVATION_DURATION - timedelta(seconds=1)
		)

		with patch(
			"Main_Interface.views.async_to_sync", side_effect=lambda function: lambda *args, **kwargs: None
		) as send_event:
			views.expire_pending_reservations()

		ticket.refresh_from_db()
		self.assertEqual(ticket.status, "cancelled")
		send_event.assert_called_once()

	def test_expiry_releases_all_seats_from_staggered_reservation(self):
		now = timezone.now()
		first_ticket = TrainTicket.objects.create(
			user=self.user,
			train_schedule=self.schedule,
			passenger_name="Rider",
			passenger_phone_number="01700000001",
			seat_number="A1",
			status="pending",
			confirmation_number="123456789012",
		)
		second_ticket = TrainTicket.objects.create(
			user=self.user,
			train_schedule=self.schedule,
			passenger_name="Rider",
			passenger_phone_number="01700000001",
			seat_number="A2",
			status="pending",
			confirmation_number="123456789012",
		)
		TrainTicket.objects.filter(pk=first_ticket.pk).update(
			booking_time=now - views.RESERVATION_DURATION - timedelta(seconds=1)
		)
		TrainTicket.objects.filter(pk=second_ticket.pk).update(
			booking_time=now - views.RESERVATION_DURATION + timedelta(minutes=1)
		)

		with patch("Main_Interface.views.timezone.now", return_value=now), patch(
			"Main_Interface.views.async_to_sync", side_effect=lambda function: lambda *args, **kwargs: None
		) as send_event:
			views.expire_pending_reservations()

		first_ticket.refresh_from_db()
		second_ticket.refresh_from_db()
		self.assertEqual(first_ticket.status, "cancelled")
		self.assertEqual(second_ticket.status, "cancelled")
		self.assertEqual(send_event.call_count, 2)
