"""
Uji mekanis pipeline (bukan uji akurasi!). Data sintetis di sini hanya untuk
memastikan alur belajar → prediksi → latih ulang berjalan benar.

  DATABASE_URL=postgresql://.../detektor_test python -m pytest -q tests
"""
import os
import random
import time

import numpy as np
import pytest

os.environ.setdefault("MODEL_DIR", "/tmp/detektor-test-models")
os.environ["AUTO_RETRAIN_EVERY"] = "0"

from app import db  # noqa: E402
from app.cli import parse_label, text_hash  # noqa: E402
from app.features import segment_spans, stylometric_features, words  # noqa: E402
from app.model import N_WORD, WORD_VEC, DetectorModel, ModelManager  # noqa: E402
from sklearn.utils import murmurhash3_32  # noqa: E402

SCHEMA = os.path.join(os.path.dirname(__file__), "..", "..", "web", "src", "db", "schema.sql")

FORMAL = [
    "Selain itu, {t} memainkan peran penting dalam meningkatkan kualitas {o} secara berkelanjutan.",
    "Oleh karena itu, penting untuk memahami bahwa {t} memberikan dampak yang signifikan terhadap {o}.",
    "Secara keseluruhan, {t} merupakan salah satu faktor utama dalam pengembangan {o} yang optimal.",
    "Dengan demikian, pendekatan yang komprehensif terhadap {t} dapat mendukung {o} di era digital.",
    "Lebih lanjut, {t} juga berkontribusi dalam menciptakan {o} yang inovatif dan berkelanjutan.",
]
CASUAL = [
    "jujur aja aku gak nyangka {t} bisa ngaruh ke {o} wkwk",
    "Kemarin pas ngobrolin {t} sama temen, ujung-ujungnya malah bahas {o}.",
    "duh, {t} ini bikin pusing banget sih!",
    "Gue sih ngerasa {t} tuh biasa aja. Tapi {o}? Nah itu beda cerita.",
    "Pernah nggak kalian mikir kenapa {t} selalu dikaitkan sama {o}? aku sering.",
    "Ok.",
]
TOPICS = ["teknologi", "pendidikan", "ekonomi desa", "kesehatan mental", "transportasi umum", "pertanian"]
OBJS = ["masyarakat", "kampus", "anak muda", "pemerintah daerah", "lingkungan", "dunia kerja"]


def synth(label: int, rng: random.Random, n_sent: int) -> str:
    pool = FORMAL if label == 1 else CASUAL
    return " ".join(rng.choice(pool).format(t=rng.choice(TOPICS), o=rng.choice(OBJS)) for _ in range(n_sent))


@pytest.fixture(scope="module")
def seeded():
    with db.conn() as c:
        c.execute(open(SCHEMA).read())
        c.execute("TRUNCATE checks, samples, model_versions RESTART IDENTITY CASCADE")
    rng = random.Random(1)
    rows = []
    for i in range(120):
        y = i % 2
        t = synth(y, rng, rng.randint(4, 30))
        rows.append({"text": t, "text_hash": text_hash(t + str(i)), "label": y, "source": "test",
                     "filename": None, "language": "id", "word_count": len(words(t))})
    ids, _ = db.insert_samples(rows)
    return ids


def test_hash_index_matches_vectorizer():
    for tok in ["teknologi", "masyarakat", "wkwk", "selain"]:
        X = WORD_VEC.transform([tok])
        assert X.indices[0] == abs(murmurhash3_32(tok, seed=0)) % N_WORD


def test_labels():
    assert parse_label("AI") == 1 and parse_label("manusia") == 0 and parse_label("0.9") == 1
    assert parse_label("??") is None


def test_segments_cover_text():
    t = synth(1, random.Random(3), 40)
    spans = segment_spans(t)
    assert len(spans) >= 2
    assert all(s < e for s, e in spans)
    assert all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))


def test_features_finite():
    f = stylometric_features(synth(0, random.Random(2), 10))
    assert all(np.isfinite(v) for v in f.values())


def test_incremental_then_full(seeded):
    mgr = ModelManager()
    # Sebelum belajar: heuristik
    r = mgr.predict(synth(1, random.Random(9), 20))
    assert r["mode"] == "heuristic"

    for i in range(0, 60, 4):
        out = mgr.learn(seeded[i:i + 4])
        assert out["learned"] == 4
    st = mgr.status()
    assert st["ready"] and st["version"]["kind"] == "incremental"
    assert st["prequential"]["n"] > 0

    ai = mgr.predict(synth(1, random.Random(10), 25))
    hu = mgr.predict(synth(0, random.Random(11), 25))
    assert ai["mode"] == "model"
    assert ai["ai_probability"] > hu["ai_probability"]
    assert ai["segments"] and ai["explanation"]["indicators"]

    assert mgr.start_full_train("test")
    while mgr.training["running"]:
        time.sleep(0.5)
    assert mgr.training["error"] is None, mgr.training["error"]
    st = mgr.status()
    assert st["version"]["kind"] == "full"
    assert st["version"]["metrics"]["accuracy"] >= 0.9

    # model tersimpan dan dapat dimuat ulang
    m2 = DetectorModel.load(db.get_active_version()["file_path"])
    assert m2.ready

    # rollback ke versi incremental
    first = db.list_versions()[-1]["id"]
    mgr.activate(first)
    assert mgr.status()["version"]["id"] == first
