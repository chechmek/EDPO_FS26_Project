from __future__ import annotations

import time
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.config import settings


def wait_for_db(retries: int = 30, delay_seconds: float = 2.0) -> None:
    for attempt in range(1, retries + 1):
        try:
            with psycopg.connect(settings.db_dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            return
        except Exception:
            if attempt == retries:
                raise
            time.sleep(delay_seconds)


@contextmanager
def get_connection():
    with psycopg.connect(settings.db_dsn, row_factory=dict_row) as conn:
        yield conn
