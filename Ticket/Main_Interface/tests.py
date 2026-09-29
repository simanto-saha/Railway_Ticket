from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from Main_Interface.models import Profile
from Main_Interface import views
from Railway_Admin.models import TrainInformation, TrainSchedule, TrainTicket, TicketPayment


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
