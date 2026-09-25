from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r"ws/schedule/(?P<schedule_id>\d+)/$", consumers.SeatConsumer.as_asgi()),
]