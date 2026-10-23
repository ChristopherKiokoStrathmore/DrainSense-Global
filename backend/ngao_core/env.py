"""Small typed readers for environment variables (values come from .env or the host)."""
import json
import os


def env_str(name, default=None):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def env_bool(name, default=False):
    value = env_str(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on", "y")


def env_int(name, default):
    value = env_str(name)
    return default if value is None else int(value)


def env_float(name, default):
    value = env_str(name)
    return default if value is None else float(value)


def env_list(name, default=None):
    value = env_str(name)
    if value is None:
        return list(default or [])
    return [item.strip() for item in value.split(",") if item.strip()]


def env_json(name, default=None):
    value = env_str(name)
    if value is None:
        return default
    return json.loads(value)
