"""
Uji modul Cek Kemiripan (fingerprint, penyelarasan, indeks, penjadwal).

  DATABASE_URL=postgresql://.../detektor_test python -m pytest -q tests/test_similarity.py
"""
import os
from datetime import datetime, timezone

import pytest

from app import db
from app.similarity import store
from app.similarity.align import SourceHit, build_report, exclusion_mask, find_matches
from app.similarity.engine import pick_queries
from app.similarity.fingerprint import document_fingerprints, shingle_hashes, tokenize, winnow

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "web", "src", "db")
SRC = ("Pendidikan karakter merupakan usaha sadar dan terencana untuk menanamkan nilai-nilai kebaikan kepada "
       "peserta didik sehingga mereka mampu bersikap jujur, disiplin, dan bertanggung jawab dalam kehidupan "
       "sehari-hari. Hasil penelitian menunjukkan bahwa keteladanan guru menjadi faktor paling dominan dalam "
       "membentuk karakter siswa.")
OWN = ("Selama praktik mengajar saya mengamati bahwa anak-anak lebih bersemangat ketika guru membuka kelas "
       "dengan cerita pendek tentang pengalaman sehari-hari mereka di rumah dan di lingkungan sekitar sekolah.")


def _match(q, d, **kw):
    qt, qs = tokenize(q)
    dt, _ = tokenize(d)
    mask, _ = exclusion_mask(q, qs, kw.pop("quotes", False), kw.pop("biblio", True))
    return find_matches(shingle_hashes(qt, excluded=mask), shingle_hashes(dt), mask, **kw), qt


def test_exact_copy_found():
    runs, qt = _match(OWN + " " + SRC, SRC)
    assert len(runs) >= 1 and sum(r.qe - r.qs for r in runs) >= len(tokenize(SRC)[0]) - 2


def test_small_edit_merged():
    edited = SRC.replace("peserta didik", "siswa")
    runs, _ = _match(edited, SRC)
    assert len(runs) == 1  # satu kata diganti → tetap satu kecocokan utuh


def test_unrelated_text_no_match():
    runs, _ = _match(OWN, SRC)
    assert runs == []


def test_quotes_and_bibliography_excluded():
    q = OWN + f'\n\nMenurut ahli, "{SRC}"\n\n' + OWN + "\n\nDAFTAR PUSTAKA\n" + SRC
    runs, _ = _match(q, SRC, quotes=True, biblio=True)
    assert runs == []
    runs, _ = _match(q, SRC, quotes=False, biblio=True)
    assert len(runs) == 1


def test_winnowing_guarantee():
    # salinan sepanjang K+W-1 = 8 kata harus berbagi minimal satu fingerprint
    doc = OWN + " " + SRC
    piece = " ".join(tokenize(SRC)[0][10:18])
    assert document_fingerprints(piece) & document_fingerprints(doc)


def test_report_primary_source_attribution():
    q = OWN + " " + SRC
    qt, qs = tokenize(q)
    hits = []
    for i, d in enumerate([SRC, SRC[:120]]):  # sumber kedua tumpang tindih penuh dengan sumber pertama
        runs = find_matches(shingle_hashes(qt), shingle_hashes(tokenize(d)[0]), [False] * len(qt))
        hits.append(SourceHit({"id": i, "category": "publication"}, runs, d, tokenize(d)[1]))
    rep = build_report(q, qs, hits, [], 8)
    assert len(rep["sources"]) == 1  # kata yang sama tidak dihitung dua kali
    assert 40 < rep["similarity"] < 70


def test_pick_queries_spread():
    text = "\n\n".join([SRC, OWN] * 6)
    toks, spans = tokenize(text)
    qs = pick_queries(text, toks, spans, [False] * len(toks), 5)
    assert 1 <= len(qs) <= 5 and all(len(tokenize(q)[0]) == 10 for q in qs)
    assert len(qs) == len(set(qs))


@pytest.fixture(scope="module")
def fresh_db():
    with db.conn() as c:
        for f in ("schema.sql", "schema-similarity.sql"):
            c.execute(open(os.path.join(ROOT, f)).read())
        c.execute("TRUNCATE sim_crawl_runs, sim_checks, sim_fingerprints, sim_documents, sim_settings RESTART IDENTITY CASCADE")
        c.execute("DELETE FROM sim_sources WHERE NOT system")
        c.execute("INSERT INTO sim_sources (name, kind, category, url, schedule) VALUES "
                  "('A','oai_pmh','publication','http://a/oai','nightly'), ('B','rss','internet','http://b/feed','weekly'),"
                  "('C','sitemap','internet','http://c/','manual')")


def test_index_and_candidates(fresh_db):
    sid = store.system_source_id("upload")
    doc_id, st = store.upsert_document(sid, "k1", SRC * 2, title="Sumber")
    assert st == "baru"
    assert store.upsert_document(sid, "k1", SRC * 2)[1] == "tetap"
    assert store.upsert_document(sid, "k1", SRC * 3)[1] == "diperbarui"
    q = OWN + " " + SRC
    toks, _ = tokenize(q)
    assert doc_id in store.candidates(winnow(toks, shingle_hashes(toks)), [], 10)
    assert doc_id not in store.candidates(winnow(toks, shingle_hashes(toks)), [doc_id], 10)


def test_scheduler(fresh_db, monkeypatch):
    from app.similarity import worker
    monkeypatch.setattr(worker.C, "CRAWL_TIME", "01:00")
    monkeypatch.setattr(worker.C, "CRAWL_TZ", "Asia/Jakarta")
    monkeypatch.setattr(worker.C, "CRAWL_WEEKLY_DAY", 6)
    # Sabtu 14 Nov 2026 00:30 WIB → belum waktunya
    assert worker.scheduler_tick(datetime(2026, 11, 13, 17, 30, tzinfo=timezone.utc)) is None
    # 01:05 WIB → sumber "setiap malam" diantrekan (bukan mingguan, bukan manual)
    assert worker.scheduler_tick(datetime(2026, 11, 13, 18, 5, tzinfo=timezone.utc))["queued"] == 1
    # dipanggil lagi pada hari yang sama → tidak dobel
    assert worker.scheduler_tick(datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)) is None
    with db.conn() as c:
        c.execute("UPDATE sim_crawl_runs SET status='done'")
    # Minggu 15 Nov 01:05 WIB → harian + mingguan
    assert worker.scheduler_tick(datetime(2026, 11, 14, 18, 5, tzinfo=timezone.utc))["queued"] == 2
