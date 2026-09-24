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

    def __str__(self):
        return f"{self.train.train_name} - {self.source_station} to {self.destination_station}"


class TrainDriverInformation(models.Model):
    train = models.ForeignKey(TrainInformation, on_delete=models.CASCADE)
    driver_name = models.CharField(max_length=100)
    license_number = models.CharField(max_length=50, unique=True)
    contact_number = models.CharField(max_length=15)

    def __str__(self):
        return f"{self.driver_name} - {self.train.train_name}"


class TrainTicket(models.Model):

    StatusChoices = [
        ('pending', 'Pending'),
        ('booked', 'Booked'),
        ('cancelled', 'Cancelled'),
    ]
    train_schedule = models.ForeignKey(TrainSchedule, on_delete=models.CASCADE)
    passenger_name = models.CharField(max_length=100)
    passenger_phone_number = models.CharField(max_length=15)
    seat_number = models.PositiveIntegerField()
    booking_time = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=StatusChoices, default='booked')
    confirmation_number = models.CharField(max_length=20, unique=True, blank=True, null=True)

    def save(self, *args, **kwargs):
        if self.status == 'booked' and not self.confirmation_number:
            self.confirmation_number = self._generate_confirmation_number()
        super().save(*args, **kwargs)

    def _generate_confirmation_number(self):
        while True:
            code = ''.join(random.choices(string.digits, k=12))
            if not TrainTicket.objects.filter(confirmation_number=code).exists():
                return code

    def __str__(self):
        return f"Ticket for {self.passenger_name} on {self.train_schedule.train.train_name}"

