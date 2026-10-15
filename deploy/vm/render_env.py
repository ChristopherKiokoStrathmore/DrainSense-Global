#!/usr/bin/env python3
"""Turn GitHub Actions secrets (environment variables) into the VM's env files.

Writes, mode 0600, into --out:
  backend.env  Django api + scheduler (Compose env_file, format raw: KEY=VALUE, taken literally)
  ussd.env     USSD callback service
  .env         Compose variables: image names and the Caddy site address
and prints the public URLs (never a secret). Missing required secrets fail before anything is
written or any connection is made. See docs/DEPLOY_VM.md for every name.

  python3 deploy/vm/render_env.py --out DIR --backend-image IMG --ussd-image IMG
  python3 deploy/vm/render_env.py --self-test
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import stat
import sys
import tempfile
from pathlib import Path

DOC = "docs/DEPLOY_VM.md"

REQUIRED = [
    "NGAO_VM_HOST",            # public IP or DNS name of the VM
    "DJANGO_SECRET_KEY",
    "NGAO_API_TOKENS",         # comma-separated bearer tokens for APEX / operators
    "USSD_SERVICE_TOKEN",
    "WEBHOOK_SECRET",          # ?k=<secret> on the Africa's Talking and Wasiliana callback URLs
    "NGAO_DB_USER",
    "NGAO_DB_PASSWORD",
    "NGAO_DB_WALLET_PASSWORD",  # python-oracledb thin mode opens ewallet.pem with it
]
# One of these names the database: a TNS alias from the wallet, or a full connect descriptor.
DSN_NAMES = ("NGAO_DB_DSN", "NGAO_DB_SERVICE")

# Optional backend settings passed through unchanged when the secret (or variable) is set.
BACKEND_PASSTHROUGH = [
    "AT_USERNAME", "AT_API_KEY", "AT_SENDER_ID", "AT_SMS_URL",
    "WASILIANA_API_KEY", "WASILIANA_SENDER_ID",
    "SMS_DISPATCH", "SMS_SIMULATE_SYNTHETIC", "DEMO_PHONES",
    "ALERTS_REQUIRE_APPROVAL", "SEND_WELCOME_SMS", "SEND_REPORT_ACK",
    "REPLY_READER", "AI_CONFIDENCE_THRESHOLD", "OCI_AUTH", "OCI_REGION", "OCI_COMPARTMENT_ID",
    "OCI_GENAI_MODEL_ID", "OCI_GENAI_ENDPOINT",
    "SEASONAL_FEATURE_SOURCE", "CDSAPI_URL", "CDSAPI_KEY",
    "LOG_LEVEL",
]
USSD_PASSTHROUGH = [
    "USSD_PROVIDER", "WASILIANA_TEXT_MODE", "WASILIANA_FIELD_SESSION", "WASILIANA_FIELD_PHONE", "WASILIANA_FIELD_TEXT",
    "WASILIANA_FIELD_SERVICE_CODE", "BACKEND_TIMEOUT_S", "LOG_LEVEL",
]


class ConfigError(Exception):
    pass


def get(env, name, default=None):
    value = (env.get(name) or "").strip()
    return value or default


def site_address(env):
    """NGAO_DOMAIN, else <ip>.sslip.io for an IP host (a real name with a free certificate), else the host."""
    domain = get(env, "NGAO_DOMAIN")
    if domain:
        return domain
    host = get(env, "NGAO_VM_HOST")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if ip.version != 4:
        raise ConfigError("NGAO_VM_HOST is an IPv6 address; set NGAO_DOMAIN to a name that resolves to it")
    return f"{ip}.sslip.io"


def render(env, backend_image, ussd_image):
    missing = [name for name in REQUIRED if not get(env, name)]
    if not any(get(env, name) for name in DSN_NAMES):
        missing.append(" or ".join(DSN_NAMES))
    if missing:
        raise ConfigError(f"Missing GitHub Actions secrets: {', '.join(missing)}. See {DOC}.")

    site = site_address(env)
    host = site.split(":")[0]
    dsn = get(env, "NGAO_DB_DSN") or get(env, "NGAO_DB_SERVICE")
    schema = get(env, "NGAO_DB_SCHEMA", "WKSP_NGAO")
    database = {
        "DB_USER": get(env, "NGAO_DB_USER"),
        "DB_PASSWORD": get(env, "NGAO_DB_PASSWORD"),
        "DB_DSN": dsn,
        "DB_WALLET_DIR": "/wallet",
        "DB_WALLET_PASSWORD": get(env, "NGAO_DB_WALLET_PASSWORD"),
        "DB_SCHEMA": schema,
    }

    backend = {
        "DJANGO_DEBUG": "false",
        "DJANGO_SECRET_KEY": get(env, "DJANGO_SECRET_KEY"),
        # "api" is how the ussd container reaches the backend; 127.0.0.1 is the container health check.
        "DJANGO_ALLOWED_HOSTS": ",".join(dict.fromkeys([host, "api", "localhost", "127.0.0.1"])),
        "DJANGO_CSRF_TRUSTED_ORIGINS": f"https://{host}",
        "DB_ENGINE": "oracle",
        **database,
        "NGAO_API_TOKENS": get(env, "NGAO_API_TOKENS"),
        "USSD_SERVICE_TOKEN": get(env, "USSD_SERVICE_TOKEN"),
        "WEBHOOK_SECRET": get(env, "WEBHOOK_SECRET"),
        "SMS_BACKEND": get(env, "SMS_BACKEND") or ("africastalking" if get(env, "AT_API_KEY") else "console"),
    }
    backend.update({name: get(env, name) for name in BACKEND_PASSTHROUGH if get(env, name)})

    ussd = {
        "BACKEND_BASE_URL": "http://api:8000/ngao/v1",
        "USSD_SERVICE_TOKEN": get(env, "USSD_SERVICE_TOKEN"),
        "WEBHOOK_SECRET": get(env, "USSD_WEBHOOK_SECRET") or get(env, "WEBHOOK_SECRET"),
        "USSD_PUBLIC_URL": f"https://{host}/ussd",
        **database,
    }
    ussd.update({name: get(env, name) for name in USSD_PASSTHROUGH if get(env, name)})

    compose = {"NGAO_BACKEND_IMAGE": backend_image, "NGAO_USSD_IMAGE": ussd_image, "NGAO_SITE_ADDRESS": site}
    files = {"backend.env": backend, "ussd.env": ussd, ".env": compose}
    for name, values in files.items():
        for key, value in values.items():
            if value is None or "\n" in value or "\r" in value:
                raise ConfigError(f"{key} for {name} is empty or spans lines; secrets must be one line")
    return files, {"site": site, "base_url": f"https://{host}/ngao/v1", "ussd_url": f"https://{host}/ussd"}


def write(out, files):
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    for name, values in files.items():
        path = out / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(f"{key}={value}\n" for key, value in values.items())
        os.chmod(path, 0o600)


def self_test():
    base = {name: f"v-{name.lower()}" for name in REQUIRED}
    base.update(NGAO_VM_HOST="129.151.10.20", NGAO_DB_SERVICE="ngaodb_low", NGAO_DB_PASSWORD='p$ss"w\'rd')

    try:
        render({k: v for k, v in base.items() if k != "WEBHOOK_SECRET"}, "b", "u")
        raise AssertionError("missing WEBHOOK_SECRET was accepted")
    except ConfigError as exc:
        assert "WEBHOOK_SECRET" in str(exc) and DOC in str(exc), exc
    try:
        render({k: v for k, v in base.items() if k != "NGAO_DB_SERVICE"}, "b", "u")
        raise AssertionError("missing DSN was accepted")
    except ConfigError as exc:
        assert "NGAO_DB_DSN or NGAO_DB_SERVICE" in str(exc), exc

    files, urls = render(base, "ghcr.io/o/ngao-backend:abc", "ghcr.io/o/ngao-ussd:abc")
    assert urls["base_url"] == "https://129.151.10.20.sslip.io/ngao/v1", urls
    backend, ussd = files["backend.env"], files["ussd.env"]
    assert backend["DJANGO_ALLOWED_HOSTS"] == "129.151.10.20.sslip.io,api,localhost,127.0.0.1"
    assert backend["DB_PASSWORD"] == 'p$ss"w\'rd' and backend["DB_SCHEMA"] == "WKSP_NGAO"
    assert backend["SMS_BACKEND"] == "console" and "AT_API_KEY" not in backend
    assert ussd["BACKEND_BASE_URL"] == "http://api:8000/ngao/v1" and ussd["DB_DSN"] == "ngaodb_low"

    files, urls = render({**base, "NGAO_DOMAIN": "ngao.example.org", "AT_API_KEY": "k", "AT_USERNAME": "ngao",
                          "NGAO_DB_DSN": "(description=(address=(host=x)))"}, "b", "u")
    assert urls["site"] == "ngao.example.org"
    assert files["backend.env"]["SMS_BACKEND"] == "africastalking"
    assert files["backend.env"]["DB_DSN"] == "(description=(address=(host=x)))"

    try:
        render({**base, "DJANGO_SECRET_KEY": "a\nb"}, "b", "u")
        raise AssertionError("multi-line secret was accepted")
    except ConfigError:
        pass

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "bundle"
        write(out, render(base, "b", "u")[0])
        for name in ("backend.env", "ussd.env", ".env"):
            assert stat.S_IMODE((out / name).stat().st_mode) == 0o600, name
        assert "DB_PASSWORD=p$ss\"w'rd\n" in (out / "backend.env").read_text()
    print("render_env self-test ok")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--backend-image")
    parser.add_argument("--ussd-image")
    parser.add_argument("--github-output", type=Path, help="append site/base_url/ussd_url for later steps")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    if not (args.out and args.backend_image and args.ussd_image):
        parser.error("--out, --backend-image and --ussd-image are required")
    try:
        files, urls = render(os.environ, args.backend_image, args.ussd_image)
    except ConfigError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    write(args.out, files)
    for key, value in urls.items():
        print(f"{key}={value}")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.writelines(f"{key}={value}\n" for key, value in urls.items())
    return 0


if __name__ == "__main__":
    sys.exit(main())
