from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import AdminProfile


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

	def test_admin_login_returns_to_requested_dashboard_url(self):
		dashboard_url = reverse("superuser_dashboard")
		response = self.client.post(
			reverse("superuser_login"),
			{
				"username": "dashboard-officer",
				"password": "test-password",
				"next": dashboard_url,
			},
			HTTP_X_REQUESTED_WITH="XMLHttpRequest",
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["redirect_url"], dashboard_url)

		dashboard_response = self.client.get(dashboard_url)
		self.assertEqual(dashboard_response.status_code, 200)
		self.assertContains(dashboard_response, "Railway Management Dashboard")
