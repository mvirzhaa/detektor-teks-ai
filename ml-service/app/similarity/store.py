"""Akses database untuk modul kemiripan."""
from __future__ import annotations

import hashlib
import re

from psycopg.types.json import Jsonb

from ..db import conn
from .config import MAX_DOC_CHARS
from .fingerprint import document_fingerprints, tokenize


def content_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text.casefold()).strip().encode("utf-8")).hexdigest()


def system_source_id(kind: str) -> int:
    with conn() as c:
        r = c.execute("SELECT id FROM sim_sources WHERE system AND kind = %s", (kind,)).fetchone()
    if not r:
        raise RuntimeError(f"Sumber sistem '{kind}' belum ada — jalankan migrasi (start web) dulu.")
    return int(r["id"])


def get_document(source_id: int, doc_key: str) -> dict | None:
    with conn() as c:
        return c.execute(
            "SELECT id, content_hash, etag, last_modified, fetched_at, url FROM sim_documents "
            "WHERE source_id = %s AND doc_key = %s", (source_id, doc_key)).fetchone()


def upsert_document(source_id: int, doc_key: str, text: str, *, url=None, title=None, authors=None,
                    published=None, etag=None, last_modified=None) -> tuple[int, str]:
    """Simpan/indeks dokumen. Return (id, status) dengan status: baru | diperbarui | tetap."""
    text = text[:MAX_DOC_CHARS]
    h = content_hash(text)
    n_words = len(tokenize(text)[0])
    with conn() as c:
        with c.transaction():
            old = c.execute("SELECT id, content_hash FROM sim_documents WHERE source_id = %s AND doc_key = %s "
                            "FOR UPDATE", (source_id, doc_key)).fetchone()
            if old and old["content_hash"] == h:
                c.execute("UPDATE sim_documents SET fetched_at = now(), etag = COALESCE(%s, etag), "
                          "last_modified = COALESCE(%s, last_modified), title = COALESCE(%s, title) WHERE id = %s",
                          (etag, last_modified, title, old["id"]))
                return int(old["id"]), "tetap"
            if old:
                doc_id = int(old["id"])
                c.execute("UPDATE sim_documents SET text=%s, content_hash=%s, word_count=%s, url=%s, title=%s, "
                          "authors=%s, published=%s, etag=%s, last_modified=%s, fetched_at=now() WHERE id=%s",
                          (text, h, n_words, url, title, authors, published, etag, last_modified, doc_id))
                c.execute("DELETE FROM sim_fingerprints WHERE doc_id = %s", (doc_id,))
                status = "diperbarui"
            else:
                doc_id = int(c.execute(
                    "INSERT INTO sim_documents (source_id, doc_key, url, title, authors, published, text, "
                    "content_hash, word_count, etag, last_modified) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "RETURNING id",
                    (source_id, doc_key, url, title, authors, published, text, h, n_words, etag, last_modified),
                ).fetchone()["id"])
                status = "baru"
            fps = document_fingerprints(text)
            with c.cursor().copy("COPY sim_fingerprints (hash, doc_id) FROM STDIN") as cp:
                for fp in fps:
                    cp.write_row((fp, doc_id))
    return doc_id, status


def touch_document(doc_id: int):
    with conn() as c:
        c.execute("UPDATE sim_documents SET fetched_at = now() WHERE id = %s", (doc_id,))


def delete_document(source_id: int, doc_key: str) -> bool:
    with conn() as c:
        r = c.execute("DELETE FROM sim_documents WHERE source_id = %s AND doc_key = %s RETURNING id",
                      (source_id, doc_key)).fetchone()
    return bool(r)


def candidates(fps: set[int], exclude_ids: list[int], limit: int) -> list[int]:
    if not fps:
        return []
    with conn() as c:
        rows = c.execute(
            "SELECT doc_id, count(DISTINCT hash) AS n FROM sim_fingerprints "
            "WHERE hash = ANY(%s) AND NOT (doc_id = ANY(%s)) GROUP BY doc_id ORDER BY n DESC LIMIT %s",
            (list(fps), exclude_ids or [0], limit)).fetchall()
    return [int(r["doc_id"]) for r in rows]


def load_documents(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    with conn() as c:
        return c.execute(
            "SELECT d.id, d.url, d.title, d.authors, d.published, d.text, s.category, s.name AS source_name "
            "FROM sim_documents d JOIN sim_sources s ON s.id = d.source_id WHERE d.id = ANY(%s)", (ids,)).fetchall()


def fresh_web_doc(url: str, max_age_days: int) -> dict | None:
    with conn() as c:
        return c.execute(
            "SELECT d.id FROM sim_documents d JOIN sim_sources s ON s.id = d.source_id "
            "WHERE (d.url = %s OR d.doc_key = %s) AND (s.kind <> 'web_search' OR d.fetched_at > now() - make_interval(days => %s)) "
            "ORDER BY (s.kind = 'web_search') LIMIT 1", (url, url, max_age_days)).fetchone()


def purge_web_cache(days: int) -> int:
    with conn() as c:
        r = c.execute("DELETE FROM sim_documents d USING sim_sources s WHERE s.id = d.source_id AND "
                      "s.kind = 'web_search' AND d.fetched_at < now() - make_interval(days => %s)", (days,))
        return r.rowcount


# ---------- antrean ----------
def claim_check() -> dict | None:
    with conn() as c:
        return c.execute(
            "UPDATE sim_checks SET status='running', started_at=now(), stage='memulai', progress=1, error=NULL "
            "WHERE id = (SELECT id FROM sim_checks WHERE status='queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) "
            "RETURNING *").fetchone()


def update_check(check_id: int, **f):
    cols, vals = [], []
    for k, v in f.items():
        cols.append(f"{k} = %s")
        vals.append(Jsonb(v) if isinstance(v, (dict, list)) else v)
    vals.append(check_id)
    with conn() as c:
        c.execute(f"UPDATE sim_checks SET {', '.join(cols)} WHERE id = %s", vals)


def claim_crawl() -> dict | None:
    with conn() as c:
        return c.execute(
            "UPDATE sim_crawl_runs SET status='running', started_at=now() "
            "WHERE id = (SELECT id FROM sim_crawl_runs WHERE status='queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) "
            "RETURNING *").fetchone()


def get_source(source_id: int) -> dict | None:
    with conn() as c:
        return c.execute("SELECT * FROM sim_sources WHERE id = %s", (source_id,)).fetchone()


def update_run(run_id: int, **f):
    cols, vals = [], []
    for k, v in f.items():
        cols.append(f"{k} = %s")
        vals.append(v)
    vals.append(run_id)
    with conn() as c:
        c.execute(f"UPDATE sim_crawl_runs SET {', '.join(cols)} WHERE id = %s", vals)


def append_run_log(run_id: int, line: str):
    with conn() as c:
        c.execute("UPDATE sim_crawl_runs SET log = right(log || %s || E'\\n', 20000) WHERE id = %s", (line, run_id))


def finish_source(source_id: int, status: str, state: dict | None):
    with conn() as c:
        if state is None:
            c.execute("UPDATE sim_sources SET last_crawl_at=now(), last_status=%s, updated_at=now() WHERE id=%s",
                      (status, source_id))
        else:
            c.execute("UPDATE sim_sources SET last_crawl_at=now(), last_status=%s, state=%s, updated_at=now() "
                      "WHERE id=%s", (status, Jsonb(state), source_id))


def enqueue_scheduled(trigger: str, schedules: list[str]) -> int:
    with conn() as c:
        r = c.execute(
            "INSERT INTO sim_crawl_runs (source_id, trigger) SELECT s.id, %s FROM sim_sources s "
            "WHERE s.enabled AND NOT s.system AND s.schedule = ANY(%s) AND NOT EXISTS ("
            " SELECT 1 FROM sim_crawl_runs r WHERE r.source_id = s.id AND r.status IN ('queued','running'))",
            (trigger, schedules))
        return r.rowcount


def get_setting(key: str):
    with conn() as c:
        r = c.execute("SELECT value FROM sim_settings WHERE key = %s", (key,)).fetchone()
    return r["value"] if r else None


def set_setting(key: str, value):
    with conn() as c:
        c.execute("INSERT INTO sim_settings (key, value, updated_at) VALUES (%s, %s, now()) "
                  "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (key, Jsonb(value)))


def recover_stale():
    """Pekerjaan berstatus 'running' saat worker mulai = sisa worker sebelumnya yang mati."""
    with conn() as c:
        c.execute("UPDATE sim_checks SET status='queued', stage='diantrikan ulang' WHERE status='running'")
        c.execute("UPDATE sim_crawl_runs SET status='failed', error='worker berhenti saat crawl', finished_at=now() "
                  "WHERE status='running'")
