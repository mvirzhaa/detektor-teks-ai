import json
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import DATABASE_URL


@contextmanager
def conn():
    c = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
    try:
        yield c
    finally:
        c.close()


def fetch_samples_by_ids(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    with conn() as c:
        return c.execute("SELECT id, text, label FROM samples WHERE id = ANY(%s) ORDER BY id", (ids,)).fetchall()


def fetch_replay(exclude_ids: list[int], per_class: int) -> list[dict]:
    rows: list[dict] = []
    with conn() as c:
        for label in (0, 1):
            rows += c.execute(
                "SELECT id, text, label FROM samples WHERE label = %s AND NOT (id = ANY(%s)) "
                "ORDER BY random() LIMIT %s",
                (label, exclude_ids or [0], per_class),
            ).fetchall()
    return rows


def fetch_all_samples() -> list[dict]:
    with conn() as c:
        return c.execute("SELECT id, text, label FROM samples ORDER BY id").fetchall()


def fetch_samples_after(max_id: int) -> list[int]:
    with conn() as c:
        return [r["id"] for r in c.execute("SELECT id FROM samples WHERE id > %s ORDER BY id", (max_id,)).fetchall()]


def mark_learned(ids: list[int]) -> None:
    if ids:
        with conn() as c:
            c.execute("UPDATE samples SET learned_at = now() WHERE id = ANY(%s)", (ids,))


def sample_counts() -> dict:
    with conn() as c:
        rows = c.execute("SELECT label, count(*) AS n FROM samples GROUP BY label").fetchall()
    d = {0: 0, 1: 0}
    for r in rows:
        d[int(r["label"])] = int(r["n"])
    return {"human": d[0], "ai": d[1]}


def create_version(kind: str, file_path: str, params: dict, metrics: dict | None,
                   n_human: int, n_ai: int, note: str | None = None) -> int:
    with conn() as c:
        r = c.execute(
            "INSERT INTO model_versions (kind, file_path, params, metrics, n_samples, n_human, n_ai, note) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (kind, file_path, Jsonb(params), Jsonb(metrics) if metrics is not None else None,
             n_human + n_ai, n_human, n_ai, note),
        ).fetchone()
        return int(r["id"])


def update_version(version_id: int, **fields) -> None:
    if not fields:
        return
    cols, vals = [], []
    for k, v in fields.items():
        cols.append(f"{k} = %s")
        vals.append(Jsonb(v) if isinstance(v, (dict, list)) else v)
    vals.append(version_id)
    with conn() as c:
        c.execute(f"UPDATE model_versions SET {', '.join(cols)}, updated_at = now() WHERE id = %s", vals)


def set_active(version_id: int) -> None:
    with conn() as c:
        with c.transaction():
            c.execute("UPDATE model_versions SET is_active = false WHERE is_active")
            c.execute("UPDATE model_versions SET is_active = true WHERE id = %s", (version_id,))


def get_active_version() -> dict | None:
    with conn() as c:
        return c.execute("SELECT * FROM model_versions WHERE is_active LIMIT 1").fetchone()


def get_version(version_id: int) -> dict | None:
    with conn() as c:
        return c.execute("SELECT * FROM model_versions WHERE id = %s", (version_id,)).fetchone()


def list_versions(limit: int = 50) -> list[dict]:
    with conn() as c:
        return c.execute(
            "SELECT id, kind, is_active, n_samples, n_human, n_ai, updates, params, metrics, note, "
            "created_at, updated_at FROM model_versions ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()


def insert_samples(rows: list[dict]) -> tuple[list[int], int]:
    """Dipakai CLI. rows: {text, label, source, filename, language, word_count, text_hash}"""
    ids, dup = [], 0
    with conn() as c:
        for r in rows:
            got = c.execute(
                "INSERT INTO samples (text, text_hash, label, source, filename, language, word_count) "
                "VALUES (%(text)s, %(text_hash)s, %(label)s, %(source)s, %(filename)s, %(language)s, %(word_count)s) "
                "ON CONFLICT (text_hash) DO NOTHING RETURNING id",
                r,
            ).fetchone()
            if got:
                ids.append(int(got["id"]))
            else:
                dup += 1
    return ids, dup


def to_json(obj) -> str:
    return json.dumps(obj, default=str)
