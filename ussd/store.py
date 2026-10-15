"""Data access for the USSD service.

Writes: USSD_SESSIONS only (one upsert per hop).
Reads:  SUBSCRIBERS.preferred_lang (to know whether a number is new and which
        language to use) and COOPERATIVES (the list farmers pick from at registration).
        These reads are read-only; SUBSCRIBERS is written by the backend via §5.

OracleStore is used when DB_DSN is set. MemoryStore is for local runs and
tests; it reads the same cooperative list from db/seed/cooperatives.csv.
"""

import csv
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

COOP_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "db", "seed", "cooperatives.csv")


def load_coops_csv(path: str = COOP_CSV) -> list[tuple[int, str, int | None]]:
    """(seed_id, short_name, None): local runs only; real ids and wards come from COOPERATIVES."""
    with open(path, newline="", encoding="utf-8") as f:
        return [(int(r["seed_id"]), r["short_name"], None) for r in csv.DictReader(f)]


@dataclass
class SessionMeta:
    """What we knew about the number when the session started."""
    start_lang: str | None  # None = new number, show the language prompt first
    created: float
    phone: str = ""  # a reused session id with another phone starts fresh
    cache: dict = field(default_factory=dict)  # mid-session backend reads, e.g. the drying offer


class SessionCache:
    """Per-session facts that USSD_SESSIONS has no column for.

    Kept in process memory, so run the service as a single worker process
    (threads are fine). Entries expire after the USSD session timeout.
    """

    def __init__(self, ttl_s: int = 600):
        self.ttl_s = ttl_s
        self._data: dict[str, SessionMeta] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> SessionMeta | None:
        with self._lock:
            self._expire()
            return self._data.get(session_id)

    def put(self, session_id: str, meta: SessionMeta) -> None:
        with self._lock:
            self._data[session_id] = meta

    def _expire(self) -> None:
        cutoff = time.time() - self.ttl_s
        for sid in [s for s, m in self._data.items() if m.created < cutoff]:
            del self._data[sid]


class MemoryStore:
    def __init__(self, coops: list[tuple[int, str, int | None]] | None = None):
        self._coops = coops if coops is not None else load_coops_csv()
        self._langs: dict[str, str] = {}
        self.sessions: dict[str, dict] = {}

    def preferred_lang(self, phone: str) -> str | None:
        return self._langs.get(phone)

    def note_language(self, phone: str, lang: str) -> None:
        # Mirrors what the backend stores, so local runs behave like production.
        self._langs[phone] = lang

    def last_text(self, session_id: str) -> str:
        return self.sessions.get(session_id, {}).get("last_text", "")

    def upsert_session(self, session_id: str, phone: str, text: str) -> None:
        self.sessions[session_id] = {"phone": phone, "last_text": text, "updated_at": time.time()}

    def cooperatives(self) -> list[tuple[int, str, int | None]]:
        """(coop_id, label, ward_id) sorted by label."""
        return sorted(self._coops, key=lambda c: c[1].lower())


class OracleStore:
    def __init__(self, pool):
        self.pool = pool
        self._coop_cache: tuple[float, list] | None = None

    @classmethod
    def from_env(cls) -> "OracleStore":
        import oracledb

        params = {
            "user": os.environ["DB_USER"],
            "password": os.environ["DB_PASSWORD"],
            "dsn": os.environ["DB_DSN"],
            "min": 1, "max": 4, "increment": 1,
        }
        wallet = os.getenv("DB_WALLET_DIR")
        if wallet:
            params.update(config_dir=wallet, wallet_location=wallet,
                          wallet_password=os.getenv("DB_WALLET_PASSWORD"))
        schema = os.getenv("DB_SCHEMA")
        if schema:
            # Log in as one user (e.g. ADMIN), work in the app schema (WKSP_NGAO), like the backend.
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]{0,127}", schema):
                raise ValueError("DB_SCHEMA must be a plain Oracle identifier")

            def use_schema(conn, requested_tag):
                with conn.cursor() as cur:
                    cur.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {schema}")

            params["session_callback"] = use_schema
        return cls(oracledb.create_pool(**params))

    def preferred_lang(self, phone: str) -> str | None:
        with self.pool.acquire() as conn, conn.cursor() as cur:
            cur.execute("SELECT preferred_lang FROM subscribers WHERE phone = :p", p=phone)
            row = cur.fetchone()
            return row[0].strip() if row else None

    def note_language(self, phone: str, lang: str) -> None:
        pass  # the backend wrote SUBSCRIBERS through POST /ussd/language

    def last_text(self, session_id: str) -> str:
        with self.pool.acquire() as conn, conn.cursor() as cur:
            cur.execute("SELECT last_text FROM ussd_sessions WHERE session_id = :s", s=session_id)
            row = cur.fetchone()
            return (row[0] or "") if row else ""

    def upsert_session(self, session_id: str, phone: str, text: str) -> None:
        with self.pool.acquire() as conn, conn.cursor() as cur:
            cur.execute(
                """
                MERGE INTO ussd_sessions t
                USING (SELECT :s AS session_id, :p AS phone, :t AS last_text FROM dual) src
                ON (t.session_id = src.session_id)
                WHEN MATCHED THEN UPDATE SET
                  t.last_text = src.last_text, t.updated_at = SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT (session_id, subscriber_id, phone, last_text, updated_at)
                  VALUES (src.session_id,
                          (SELECT subscriber_id FROM subscribers WHERE phone = src.phone),
                          src.phone, src.last_text, SYSTIMESTAMP)
                """,
                s=session_id, p=phone, t=text[:200],
            )
            conn.commit()

    def cooperatives(self) -> list[tuple[int, str, int | None]]:
        """Real societies only (is_demo = 'N'), as (coop_id, label, ward_id)."""
        # The list changes rarely; cache it for 10 minutes to keep hops fast.
        if self._coop_cache and time.time() - self._coop_cache[0] < 600:
            return self._coop_cache[1]
        with self.pool.acquire() as conn, conn.cursor() as cur:
            cur.execute("SELECT coop_id, NVL(short_name, coop_name), ward_id FROM cooperatives "
                        "WHERE NVL(is_demo, 'Y') = 'N' ORDER BY UPPER(NVL(short_name, coop_name))")
            rows = [(int(i), n, int(w) if w is not None else None) for i, n, w in cur]
        self._coop_cache = (time.time(), rows)
        return rows


def store_from_env():
    if os.getenv("DB_DSN"):
        return OracleStore.from_env()
    log.warning("DB_DSN not set: using MemoryStore (cooperatives from db/seed/cooperatives.csv)")
    return MemoryStore()
