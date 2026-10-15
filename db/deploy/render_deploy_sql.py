#!/usr/bin/env python3
"""Render the SQLcl script for an NgaoDB deploy or dry run.

The script sets the APEX install context so application 100 is replaced in
workspace NGAO (parsing schema WKSP_NGAO). It does not call
generate_application_id or generate_offset: those allocate a second application
or shift component ids. The export already deletes and recreates app 100 when
the id and workspace stay the same (Oracle APEX 26.1, "Installing an Application
When Target Environment Mirrors the Source").
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_manifest import LintError, lint_manifest, repo_root


class RenderError(Exception):
    pass


IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
SERVICE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,254}$")
DIGITS = re.compile(r"^[1-9][0-9]{0,17}$")

COUNT_QUERIES = (
    ("wards", "SELECT COUNT(*) FROM wards"),
    ("cooperatives", "SELECT COUNT(*) FROM cooperatives"),
    ("v_ward_risk_latest", "SELECT COUNT(*) FROM v_ward_risk_latest"),
    ("v_alert_queue", "SELECT COUNT(*) FROM v_alert_queue"),
    ("v_garissa_replay", "SELECT COUNT(*) FROM v_garissa_replay"),
    ("v_rain_series", "SELECT COUNT(*) FROM v_rain_series"),
    ("climate_indices_monthly", "SELECT COUNT(*) FROM climate_indices_monthly"),
    ("climate_ward_rain_monthly", "SELECT COUNT(*) FROM climate_ward_rain_monthly"),
    ("seasonal_model_runs", "SELECT COUNT(*) FROM seasonal_model_runs"),
    ("v_climate_enso_latest", "SELECT COUNT(*) FROM v_climate_enso_latest"),
    ("v_seasonal_outlook_latest", "SELECT COUNT(*) FROM v_seasonal_outlook_latest"),
)


def fail(message: str) -> None:
    raise RenderError(message)


def env_stripped(name: str) -> str:
    return os.environ.get(name, "").strip()


def env_or(name: str, default: str) -> str:
    value = env_stripped(name)
    return value or default


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def require_ident(label: str, value: str) -> str:
    if not IDENT.fullmatch(value):
        fail(
            f"{label} must be an unquoted Oracle identifier (letters, digits, _, $, #). "
            f"Got a value that does not match. No connection to NgaoDB was attempted."
        )
    return value.upper()


def password_from_env() -> str:
    password = os.environ.get("NGAO_DB_PASSWORD", "")
    if password.endswith("\r\n"):
        password = password[:-2]
    elif password.endswith("\n"):
        password = password[:-1]
    if password.strip() == "":
        fail("NGAO_DB_PASSWORD is empty. No connection to NgaoDB was attempted.")
    if "\n" in password or "\r" in password:
        fail(
            "NGAO_DB_PASSWORD contains a line break. Store the password as a single-line secret. "
            "No connection to NgaoDB was attempted."
        )
    return password


def connect_statement(user: str, password: str, service: str) -> str:
    # Double quotes inside an Oracle quoted password are escaped by doubling them.
    quoted = password.replace('"', '""')
    return f'connect {user}/"{quoted}"@{service}'


def apex_settings() -> dict[str, str]:
    workspace = require_ident("NGAO_APEX_WORKSPACE", env_or("NGAO_APEX_WORKSPACE", "NGAO"))
    schema = require_ident("NGAO_APEX_SCHEMA", env_or("NGAO_APEX_SCHEMA", "WKSP_NGAO"))
    app_id = env_or("NGAO_APEX_APP_ID", "100")
    if not DIGITS.fullmatch(app_id):
        fail("NGAO_APEX_APP_ID must be a positive integer. No connection to NgaoDB was attempted.")
    alias = env_or("NGAO_APEX_ALIAS", "NGAO-MVUASMART")
    if not ALIAS.fullmatch(alias):
        fail(
            "NGAO_APEX_ALIAS must contain only letters, digits, underscores, and hyphens. "
            "No connection to NgaoDB was attempted."
        )
    workspace_id = env_stripped("NGAO_APEX_WORKSPACE_ID")
    if workspace_id and not DIGITS.fullmatch(workspace_id):
        fail(
            "NGAO_APEX_WORKSPACE_ID must be a positive integer when it is set. "
            "No connection to NgaoDB was attempted."
        )
    return {
        "workspace": workspace,
        "schema": schema,
        "app_id": app_id,
        "alias": alias,
        "workspace_id": workspace_id,
    }


def connection_settings(wallet: Path) -> dict[str, str]:
    user = require_ident("NGAO_DB_USER", env_stripped("NGAO_DB_USER"))
    service = env_stripped("NGAO_DB_SERVICE")
    if not SERVICE.fullmatch(service):
        fail(
            "NGAO_DB_SERVICE must be the TNS alias from the wallet tnsnames.ora "
            "(for example ngaodb_low), not a JDBC URL. "
            "No connection to NgaoDB was attempted."
        )
    wallet_path = wallet.resolve()
    if not wallet_path.is_file():
        fail(f"Wallet zip {wallet_path} does not exist. No connection to NgaoDB was attempted.")
    if any(char.isspace() for char in str(wallet_path)):
        fail("Wallet path contains whitespace. No connection to NgaoDB was attempted.")
    return {
        "user": user,
        "password": password_from_env(),
        "service": service,
        "wallet": str(wallet_path),
    }


def script_paths(root: Path) -> list[Path]:
    try:
        relative = lint_manifest(root, root / "db" / "deploy" / "manifest.txt")
    except LintError as exc:
        fail(str(exc))
    return [(root / rel).resolve() for rel in relative]


def apex_block(settings: dict[str, str]) -> str:
    lines = [
        "prompt ===== APEX install context (replace app, do not allocate a new id)",
        "begin",
        "  apex_application_install.clear_all;",
        f"  apex_application_install.set_workspace({sql_literal(settings['workspace'])});",
        f"  apex_application_install.set_application_id({settings['app_id']});",
        f"  apex_application_install.set_schema({sql_literal(settings['schema'])});",
        f"  apex_application_install.set_application_alias({sql_literal(settings['alias'])});",
    ]
    if settings["workspace_id"]:
        lines.append(
            f"  apex_application_install.set_workspace_id({settings['workspace_id']});"
        )
    lines.extend(
        [
            "end;",
            "/",
        ]
    )
    return "\n".join(lines)


def smoke_block(settings: dict[str, str]) -> str:
    reports = []
    for label, query in COUNT_QUERIES:
        reports.append(f"  report({sql_literal(label)}, {sql_literal(query)});")
    report_sql = "\n".join(reports)
    alias = settings["alias"].upper()
    return f"""prompt ===== post-deploy counts and application check
whenever sqlerror exit sql.sqlcode
set define off
set serveroutput on size unlimited
declare
  n number;
  procedure report(p_label varchar2, p_sql varchar2) is
  begin
    execute immediate p_sql into n;
    dbms_output.put_line(p_label || '=' || n);
  end;
begin
{report_sql}
  begin
    execute immediate
      'select count(*) from apex_applications'
      || ' where application_id = :app_id and owner = :owner'
      || ' and workspace = :workspace and upper(alias) = :alias'
      into n
      using {settings['app_id']}, {sql_literal(settings['schema'])}, {sql_literal(settings['workspace'])}, {sql_literal(alias)};
  exception
    when others then
      if sqlcode = -904 then
        execute immediate
          'select count(*) from apex_applications'
          || ' where application_id = :app_id and application_owner = :owner'
          || ' and workspace = :workspace and upper(application_alias) = :alias'
          into n
          using {settings['app_id']}, {sql_literal(settings['schema'])}, {sql_literal(settings['workspace'])}, {sql_literal(alias)};
      else
        raise;
      end if;
  end;
  if n <> 1 then
    raise_application_error(-20002,
      'Expected exactly one APEX application {settings['app_id']} alias {alias}'
      || ' owned by {settings['schema']} in workspace {settings['workspace']}. Found ' || n);
  end if;
  dbms_output.put_line('apex_app_{settings['app_id']}=ok');
end;
/
"""


def render(mode: str, root: Path, wallet: Path) -> str:
    if mode not in {"apply", "dry-run"}:
        fail(f"Unknown mode {mode}")
    conn = connection_settings(wallet)
    settings = apex_settings()
    paths = script_paths(root)
    export = (root / "frontend" / "apex" / "f100.sql").resolve()
    if not export.is_file():
        fail(f"APEX export not found at {export}. No connection to NgaoDB was attempted.")

    lines = [
        "-- Generated by db/deploy/render_deploy_sql.py. Do not commit.",
        "set echo off",
        "set define off",
        "set verify off",
        "set feedback on",
        "set serveroutput on size unlimited",
        "whenever sqlerror exit sql.sqlcode",
        f"set cloudconfig {conn['wallet']}",
        connect_statement(conn["user"], conn["password"], conn["service"]),
        "prompt ===== connected",
        f"alter session set current_schema = {settings['schema']};",
        "declare",
        "  c varchar2(128);",
        "begin",
        "  c := sys_context('USERENV','CURRENT_SCHEMA');",
        f"  if c <> {sql_literal(settings['schema'])} then",
        "    raise_application_error(-20001, 'CURRENT_SCHEMA is ' || c || "
        f"', expected {settings['schema']}');",
        "  end if;",
        "end;",
        "/",
    ]

    if mode == "dry-run":
        lines.append("prompt ===== DRY RUN. The files below are not executed.")
        for path in paths:
            lines.append(f"prompt would run {path}")
        lines.append(f"prompt would import {export}")
        lines.append(
            "prompt install context workspace "
            f"{settings['workspace']} schema {settings['schema']} "
            f"application {settings['app_id']} alias {settings['alias']}"
        )
        lines.extend(
            [
                "select user as connected_user from dual;",
                "select sys_context('USERENV','CURRENT_SCHEMA') as current_schema from dual;",
                f"select count(*) as existing_app_{settings['app_id']} from apex_applications "
                f"where application_id = {settings['app_id']};",
                "prompt ===== DRY RUN complete. No manifest script ran and f100.sql was not imported.",
                "exit",
                "",
            ]
        )
        return "\n".join(lines)

    # Echo stays off. f100.sql is about 2 MB; echoing it would flood the Actions log.
    for path in paths:
        lines.append(f"prompt ===== {path.name}")
        lines.append(f"@{path}")
    lines.append(apex_block(settings))
    lines.append(f"prompt ===== importing {export}")
    lines.append(f"@{export}")
    lines.append(smoke_block(settings))
    lines.append("exit")
    lines.append("")
    return "\n".join(lines)


def write_session(path: Path, sql: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sql, encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def self_test() -> None:
    root = repo_root()
    with tempfile.TemporaryDirectory() as tmp:
        wallet = Path(tmp) / "wallet.zip"
        wallet.write_bytes(b"PK")
        out = Path(tmp) / "session.sql"
        env = {
            "NGAO_DB_USER": "WKSP_NGAO",
            "NGAO_DB_PASSWORD": 'p@ss/w"ord\n',
            "NGAO_DB_SERVICE": "ngaodb_low",
            "NGAO_WALLET_ZIP": str(wallet),
        }
        saved = {key: os.environ.get(key) for key in env}
        os.environ.update(env)
        for optional in (
            "NGAO_APEX_WORKSPACE",
            "NGAO_APEX_WORKSPACE_ID",
            "NGAO_APEX_SCHEMA",
            "NGAO_APEX_APP_ID",
            "NGAO_APEX_ALIAS",
        ):
            os.environ.pop(optional, None)
        try:
            sql = render("apply", root, wallet)
            write_session(out, sql)
            mode = stat.S_IMODE(out.stat().st_mode)
            if mode != 0o600:
                raise AssertionError(oct(mode))
            text = out.read_text(encoding="utf-8")
            for needle in (
                "set_workspace('NGAO')",
                "set_application_id(100)",
                "set_schema('WKSP_NGAO')",
                "set_application_alias('NGAO-MVUASMART')",
                "alter session set current_schema = WKSP_NGAO;",
                str((root / "db/deploy/000_live_objects.sql").resolve()),
                str((root / "db/deploy/010_climate_seasonal.sql").resolve()),
                str((root / "frontend/apex/f100.sql").resolve()),
                'connect WKSP_NGAO/"p@ss/w""ord"@ngaodb_low',
                "apex_app_100=ok",
            ):
                if needle not in text:
                    raise AssertionError(f"missing {needle}")
            for banned in ("generate_application_id", "generate_offset", "set_workspace_id"):
                if banned in text:
                    raise AssertionError(f"unexpected {banned}")
            if "set echo on" in text:
                raise AssertionError("echo would print the APEX export into the log")
            if "connect WKSP_NGAO/" not in text.split("prompt ===== connected", 1)[0]:
                raise AssertionError("connect statement missing before the connected prompt")

            os.environ["NGAO_APEX_WORKSPACE_ID"] = "9382588374983398"
            with_id = render("apply", root, wallet)
            if "set_workspace_id(9382588374983398)" not in with_id:
                raise AssertionError("workspace id was not rendered")

            dry = render("dry-run", root, wallet)
            if "@" + str((root / "frontend/apex/f100.sql").resolve()) in dry:
                raise AssertionError("dry-run imports f100.sql")
            for path in script_paths(root):
                if f"@{path}" in dry:
                    raise AssertionError(f"dry-run executes {path}")
            if "would import" not in dry or "DRY RUN complete" not in dry:
                raise AssertionError("dry-run prompt missing")

            os.environ["NGAO_APEX_SCHEMA"] = "WKSP_NGAO; DROP TABLE wards"
            try:
                render("apply", root, wallet)
            except RenderError as exc:
                if "NGAO_APEX_SCHEMA" not in str(exc):
                    raise AssertionError(exc) from exc
            else:
                raise AssertionError("bad schema should fail")
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            os.environ.pop("NGAO_APEX_SCHEMA", None)
            os.environ.pop("NGAO_APEX_WORKSPACE_ID", None)
    print("render_deploy_sql self-test ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the NgaoDB SQLcl session script")
    parser.add_argument("--mode", choices=("apply", "dry-run"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    if args.mode is None or args.out is None:
        print("::error::render_deploy_sql.py requires --mode and --out", file=sys.stderr)
        return 1
    wallet = Path(os.environ.get("NGAO_WALLET_ZIP", ""))
    try:
        sql = render(args.mode, repo_root(), wallet)
    except RenderError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    write_session(args.out, sql)
    print(f"SQLcl session script written ({args.mode}). It was not printed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
