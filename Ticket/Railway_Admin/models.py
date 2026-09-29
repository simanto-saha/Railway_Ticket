from django.db import models
from django.contrib.auth.models import User
import random
import string


class AdminProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    designation = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=15, unique=True)
    one_time_password = models.BooleanField(default=False)
     

    def __str__(self):
        return self.full_name



class TrainInformation(models.Model):
    train_number = models.CharField(max_length=10, unique=True)
    train_name = models.CharField(max_length=100)
    total_seats = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.train_number} - {self.train_name}"



class TrainSchedule(models.Model):
    train = models.ForeignKey(TrainInformation, on_delete=models.CASCADE)
    departure_time = models.DateTimeField()
    arrival_time = models.DateTimeField()
    source_station = models.CharField(max_length=100)
    destination_station = models.CharField(max_length=100)
    ac_ticket_price = models.IntegerField(blank=True, null=True)
    singdha_ticket_price = models.IntegerField(blank=True, null=True)
    s_chair_ticket_price = models.IntegerField(blank=True, null=True)

    def __str__(self):
        return f"{self.train.train_name} - {self.source_station} to {self.destination_station}"


class TrainDriverInformation(models.Model):
    train = models.ForeignKey(TrainInformation, on_delete=models.CASCADE)
    driver_name = models.CharField(max_length=100)
    license_number = models.CharField(max_length=50, unique=True)
    contact_number = models.CharField(max_length=15)

    def __str__(self):
        return f"{self.driver_name} - {self.train.train_name}"


from django.conf import settings
from django.db.models import Q

def generate_confirmation_number():
    while True:
        code = ''.join(random.choices(string.digits, k=12))
        if not TrainTicket.objects.filter(confirmation_number=code).exists():
            return code

        
class TrainTicket(models.Model):

    StatusChoices = [
        ('pending', 'Pending'),
        ('booked', 'Booked'),
        ('cancelled', 'Cancelled'),
    ]
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tickets",
        null=True,   # purono ticket gulor jonno (niche dekhun)
        blank=True,
    )
    train_schedule = models.ForeignKey(TrainSchedule, on_delete=models.CASCADE)
    passenger_name = models.CharField(max_length=100)
    passenger_phone_number = models.CharField(max_length=15)
    seat_number = models.CharField(max_length=10)
    booking_time = models.DateTimeField(auto_now_add=True, db_index=True)
    status = models.CharField(max_length=10, choices=StatusChoices, default='booked')
    confirmation_number = models.CharField(max_length=20, db_index=True, blank=True, null=True)

    class Meta:
        constraints = [
            # DB level e double booking ekdom rokhbe
            models.UniqueConstraint(
                fields=["train_schedule", "seat_number"],
                condition=Q(status__in=("pending", "booked")),
                name="unique_booked_seat_per_schedule",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.status == 'booked' and not self.confirmation_number:
            self.confirmation_number = generate_confirmation_number()
        super().save(*args, **kwargs)



    def __str__(self):
        return f"Ticket for {self.passenger_name} on {self.train_schedule.train.train_name}"



class TicketPayment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tickets = models.ManyToManyField(TrainTicket, related_name="payments")
    payment_id = models.CharField(max_length=100, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, default="pending")
    payment_time = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"Payment {self.payment_id} - {self.amount} ({self.status})"
