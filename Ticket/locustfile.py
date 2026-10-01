from locust import HttpUser, task, between


class WebsiteUser(HttpUser):
    host = "http://127.0.0.1:8000"
    wait_time = between(1, 3)

    @task(5)
    def home_page(self):
        self.client.get("/", name="Home")

    @task(3)
    def train_schedule_page(self):
        self.client.get("/train-schedule/", name="Train Schedule")

    @task(3)
    def ticket_page(self):
        self.client.get("/ticket/", name="Ticket Page", allow_redirects=False)

    @task(2)
    def login_page(self):
        self.client.get("/super-admin-login/", name="Login Page")