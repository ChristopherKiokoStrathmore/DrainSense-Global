"""Dates in Africa/Nairobi (EAT) and the bilingual day/time words used in SMS copy."""
import datetime as dt
from zoneinfo import ZoneInfo

from django.utils import timezone

EAT = ZoneInfo("Africa/Nairobi")

_WEEKDAYS = {
    "sw": ["Jumatatu", "Jumanne", "Jumatano", "Alhamisi", "Ijumaa", "Jumamosi", "Jumapili"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}


def now_eat():
    return timezone.now().astimezone(EAT)


def today_eat():
    return now_eat().date()


def start_of_day_eat(day):
    return dt.datetime.combine(day, dt.time.min, tzinfo=EAT)


def iso_eat(value):
    """ISO-8601 with the +03:00 offset the contract uses, for values built outside serializers."""
    return value.astimezone(EAT).isoformat() if value else None


def weekday_name(day, lang):
    return _WEEKDAYS.get(lang, _WEEKDAYS["sw"])[day.weekday()]


def clock_text(moment, lang):
    """English: '8:00 AM'. Swahili time counts from 6 AM: 08:00 is 'saa 2 asubuhi'."""
    moment = moment.astimezone(EAT)
    if lang == "en":
        return moment.strftime("%I:%M %p").lstrip("0")
    hour = moment.hour
    sw_hour = (hour - 6) % 12 or 12
    if 6 <= hour < 12:
        period = "asubuhi"
    elif 12 <= hour < 16:
        period = "mchana"
    elif 16 <= hour < 19:
        period = "jioni"
    else:
        period = "usiku"
    minutes = f":{moment.minute:02d}" if moment.minute else ""
    return f"saa {sw_hour}{minutes} {period}"
