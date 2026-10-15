"""SMS providers. The rest of the code only uses these three functions.

SMS_BACKEND selects who sends: console (log only) | africastalking (default for real sends) |
wasiliana. Callbacks are parsed in Africa's Talking format unless SMS_BACKEND=wasiliana.
"""
from django.conf import settings

from sms.providers import africastalking, wasiliana
from sms.providers.base import ConsoleBackend


def get_backend():
    if settings.NGAO_SMS_BACKEND == "africastalking":
        return africastalking.AfricasTalkingBackend()
    if settings.NGAO_SMS_BACKEND == "wasiliana":
        return wasiliana.WasilianaBackend()
    return ConsoleBackend()


def callback_module():
    return wasiliana if settings.NGAO_SMS_BACKEND == "wasiliana" else africastalking


def parse_delivery_report(data):
    return callback_module().parse_delivery_report(data)


def parse_inbound(data):
    return callback_module().parse_inbound(data)
