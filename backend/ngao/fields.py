"""Column types that match how db/schema.sql stores values in Oracle."""
import json

from django.db import models


class YesNoField(models.CharField):
    """CHAR(1) 'Y'/'N' in the database, bool in Python (is_replay, is_demo, is_placeholder ...)."""

    def __init__(self, *args, **kwargs):
        kwargs["max_length"] = 1
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs.pop("max_length", None)
        return name, path, args, kwargs

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        return str(value).strip().upper() == "Y"

    def to_python(self, value):
        if value is None or isinstance(value, bool):
            return value
        return str(value).strip().upper() in ("Y", "TRUE", "1")

    def get_prep_value(self, value):
        value = self.to_python(value)
        if value is None:
            return None
        return "Y" if value else "N"


class JSONColumn(models.JSONField):
    """Oracle 21c+ native JSON columns come back from python-oracledb as dict/list, not text."""

    def from_db_value(self, value, expression, connection):
        if isinstance(value, (dict, list)):
            return value
        if hasattr(value, "read"):  # LOB
            value = value.read()
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if value is None:
            return None
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return value
