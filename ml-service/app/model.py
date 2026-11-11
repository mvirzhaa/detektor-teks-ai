"""
Model pendeteksi teks AI vs manusia yang dapat terus belajar.

Representasi teks (semuanya "stateless" sehingga cocok untuk incremental learning):
  1. HashingVectorizer n-gram kata (1–2)          -> pilihan kata & frasa
  2. HashingVectorizer n-gram karakter (3–5)       -> morfologi, ejaan, gaya ketik
  3. 25 fitur stilometri (lihat features.py)       -> burstiness, keragaman, dsb.

Klasifier: SGDClassifier(loss="log_loss") = regresi logistik yang mendukung
partial_fit(), sehingga setiap sampel baru di Mode Belajar langsung dipelajari.
Latih ulang penuh (full retrain) memakai seluruh dataset, memilih alpha terbaik,
dan menghitung metrik pada data uji yang dipisahkan.
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from collections import Counter
from datetime import datetime, timezone

import joblib
import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.utils import murmurhash3_32

from . import db
from .config import AUTO_RETRAIN_EVERY, MIN_SAMPLES_PER_CLASS, MODEL_DIR, REPLAY_PER_CLASS
from .features import (
    FEATURE_KEYS, FEATURE_LABELS, chunk_text, feature_matrix, heuristic_probability,
    heuristic_terms, normalize_text, segment_spans, sentence_spans, stylometric_features, words,
)

log = logging.getLogger("model")

FEATURE_SCHEMA = "v1"
N_WORD = 2 ** 18
N_CHAR = 2 ** 18
W_WORD, W_CHAR, W_STYLO = 1.0, 1.0, 0.25
TOKEN_PATTERN = r"(?u)\b[^\W_]+\b"

WORD_VEC = HashingVectorizer(analyzer="word", ngram_range=(1, 2), n_features=N_WORD,
                             alternate_sign=False, norm="l2", token_pattern=TOKEN_PATTERN)
CHAR_VEC = HashingVectorizer(analyzer="char_wb", ngram_range=(3, 5), n_features=N_CHAR,
                             alternate_sign=False, norm="l2")
WORD_ANALYZER = WORD_VEC.build_analyzer()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _balanced_weights(labels: list[int], boost: list[float] | None = None) -> np.ndarray:
    c = Counter(labels)
    n = len(labels)
    w = np.array([n / (2.0 * c[y]) for y in labels], dtype=float)
    if boost is not None:
        w *= np.asarray(boost, dtype=float)
    return w


def expand(texts: list[str], labels: list[int], max_chunks: int = 6):
    """Augmentasi: dokumen panjang juga dipotong menjadi segmen berlabel sama,
    karena Mode Cek menilai teks per segmen."""
    out_t, out_y, is_chunk = [], [], []
    for t, y in zip(texts, labels):
        out_t.append(t); out_y.append(y); is_chunk.append(False)
        if len(words(t)) > 220:
            chunks = [c for c in chunk_text(t, 110) if len(words(c)) >= 40]
            if len(chunks) > max_chunks:
                chunks = random.Random(len(t)).sample(chunks, max_chunks)
            for ch in chunks:
                out_t.append(ch); out_y.append(y); is_chunk.append(True)
    return out_t, out_y, is_chunk


class DetectorModel:
    def __init__(self, alpha: float = 1e-4):
        self.alpha = alpha
        self.scaler = StandardScaler()
        self.clf = SGDClassifier(loss="log_loss", alpha=alpha, penalty="l2", random_state=42)
        self.seen = {0: 0, 1: 0}          # jumlah sampel (dokumen) yang sudah dipelajari
        self.prequential = {"n": 0, "correct": 0}
        self.updates = 0
        self.since_full = 0
        self.fitted = False
        self.schema = FEATURE_SCHEMA

    # ---------- representasi ----------
    def _stylo_scaled(self, stylo: np.ndarray) -> np.ndarray:
        return np.clip(self.scaler.transform(stylo), -5, 5)

    def matrix(self, texts: list[str], stylo: np.ndarray | None = None):
        if stylo is None:
            stylo = feature_matrix(texts)
        return sparse.hstack([
            WORD_VEC.transform(texts) * W_WORD,
            CHAR_VEC.transform(texts) * W_CHAR,
            sparse.csr_matrix(self._stylo_scaled(stylo) * W_STYLO),
        ]).tocsr()

    @property
    def ready(self) -> bool:
        return self.fitted and min(self.seen.values()) >= MIN_SAMPLES_PER_CLASS

    # ---------- belajar ----------
    def partial_learn(self, texts, labels, weights, epochs: int = 3, seed: int = 0):
        stylo = feature_matrix(texts)
        self.scaler.partial_fit(stylo)
        X = self.matrix(texts, stylo)
        y = np.asarray(labels)
        w = np.asarray(weights)
        rng = np.random.default_rng(seed)
        for _ in range(epochs):
            idx = rng.permutation(len(y))
            self.clf.partial_fit(X[idx], y[idx], classes=np.array([0, 1]), sample_weight=w[idx])
        self.fitted = True

    def fit_full(self, texts, labels, alpha: float):
        self.alpha = alpha
        exp_t, exp_y, _ = expand(texts, labels)
        stylo = feature_matrix(exp_t)
        self.scaler = StandardScaler().fit(stylo)
        X = self.matrix(exp_t, stylo)
        self.clf = SGDClassifier(loss="log_loss", alpha=alpha, penalty="l2", max_iter=60,
                                 tol=1e-4, random_state=42)
        self.clf.fit(X, np.asarray(exp_y), sample_weight=_balanced_weights(exp_y))
        self.fitted = True
        c = Counter(labels)
        self.seen = {0: c[0], 1: c[1]}
        self.prequential = {"n": 0, "correct": 0}
        self.updates = 0
        self.since_full = 0

    # ---------- prediksi ----------
    def proba(self, texts: list[str]) -> np.ndarray:
        return self.clf.predict_proba(self.matrix(texts))[:, 1]

    def explain(self, text: str, top: int = 6) -> dict:
        coef = self.clf.coef_[0]
        f = stylometric_features(text)
        raw = np.array([[f[k] for k in FEATURE_KEYS]])
        scaled = self._stylo_scaled(raw)[0] * W_STYLO
        stylo_coef = coef[N_WORD + N_CHAR:]
        contrib = stylo_coef * scaled
        indicators = [
            {"key": k, "label": FEATURE_LABELS[k], "value": round(f[k], 4),
             "contribution": round(float(c), 4), "toward": "ai" if c > 0 else "human"}
            for k, c in zip(FEATURE_KEYS, contrib) if abs(c) > 1e-4
        ]
        indicators.sort(key=lambda d: abs(d["contribution"]), reverse=True)

        # Frasa (n-gram kata) yang paling mendorong ke arah AI / manusia
        grams = Counter(WORD_ANALYZER(text))
        norm = np.sqrt(sum(v * v for v in grams.values())) or 1.0
        scored = []
        for g, cnt in grams.items():
            if len(g) < 3:
                continue
            idx = abs(murmurhash3_32(g, seed=0)) % N_WORD
            scored.append((g, float(coef[idx]) * cnt / norm * W_WORD))
        scored.sort(key=lambda x: x[1])
        to_ai = [{"phrase": g, "weight": round(s, 4)} for g, s in reversed(scored[-top:]) if s > 0]
        to_human = [{"phrase": g, "weight": round(s, 4)} for g, s in scored[:top] if s < 0]
        return {"indicators": indicators[:10], "phrases_ai": to_ai, "phrases_human": to_human}

    # ---------- simpan/muat ----------
    def save(self, path: str):
        tmp = path + ".tmp"
        joblib.dump(self, tmp, compress=3)
        os.replace(tmp, path)

    @staticmethod
    def load(path: str) -> "DetectorModel":
        m = joblib.load(path)
        if getattr(m, "schema", None) != FEATURE_SCHEMA:
            raise ValueError("Skema fitur model berbeda dengan versi aplikasi; latih ulang penuh.")
        return m


def verdict_for(p: float, segs: list[dict]) -> tuple[str, str]:
    if len(segs) >= 3:
        hi = sum(1 for s in segs if s["ai_probability"] >= 0.7)
        lo = sum(1 for s in segs if s["ai_probability"] <= 0.3)
        if hi and lo and min(hi, lo) / len(segs) >= 0.2:
            return "mixed", "Campuran: sebagian bagian terindikasi AI"
    if p >= 0.75:
        return "ai", "Kemungkinan besar buatan AI"
    if p >= 0.58:
        return "likely_ai", "Cenderung buatan AI"
    if p > 0.42:
        return "uncertain", "Tidak dapat dipastikan"
    if p > 0.25:
        return "likely_human", "Cenderung buatan manusia"
    return "human", "Kemungkinan besar buatan manusia"


class ModelManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.model: DetectorModel | None = None
        self.version_id: int | None = None
        self.training = {"running": False, "stage": None, "started_at": None,
                         "finished_at": None, "error": None, "reason": None, "last_version_id": None}

    # ---------- siklus hidup ----------
    def load_active(self):
        try:
            v = db.get_active_version()
        except Exception as e:  # noqa: BLE001  (tabel belum dibuat, DB belum siap)
            log.warning("Tidak dapat memuat model aktif: %s", e)
            return
        if not v:
            log.info("Belum ada model aktif — memakai mode heuristik.")
            return
        try:
            self.model = DetectorModel.load(v["file_path"])
            self.version_id = v["id"]
            log.info("Model v%s dimuat (%s)", v["id"], v["kind"])
        except Exception as e:  # noqa: BLE001
            log.error("Gagal memuat file model v%s: %s", v["id"], e)

    def _path(self, vid: int) -> str:
        return os.path.join(MODEL_DIR, f"model_v{vid}.joblib")

    def _new_version(self, model: DetectorModel, kind: str, metrics: dict | None, note: str | None) -> int:
        params = {"alpha": model.alpha, "schema": FEATURE_SCHEMA, "n_word": N_WORD, "n_char": N_CHAR,
                  "weights": [W_WORD, W_CHAR, W_STYLO]}
        vid = db.create_version(kind, "", params, metrics, model.seen[0], model.seen[1], note)
        path = self._path(vid)
        model.save(path)
        db.update_version(vid, file_path=path)
        return vid

    # ---------- status ----------
    def status(self) -> dict:
        m = self.model
        v = None
        if self.version_id:
            try:
                v = db.get_version(self.version_id)
            except Exception:  # noqa: BLE001
                v = None
        pre = m.prequential if m else {"n": 0, "correct": 0}
        try:
            counts = db.sample_counts()
        except Exception:  # noqa: BLE001
            counts = None
        return {
            "has_model": m is not None,
            "ready": bool(m and m.ready),
            "mode": "model" if (m and m.ready) else "heuristic",
            "version": None if not v else {
                "id": v["id"], "kind": v["kind"], "created_at": v["created_at"], "updated_at": v["updated_at"],
                "metrics": v["metrics"], "params": v["params"], "updates": m.updates if m else 0,
            },
            "seen": {"human": m.seen[0], "ai": m.seen[1]} if m else {"human": 0, "ai": 0},
            "prequential": {**pre, "accuracy": (pre["correct"] / pre["n"]) if pre["n"] else None},
            "since_full": m.since_full if m else 0,
            "auto_retrain_every": AUTO_RETRAIN_EVERY,
            "min_samples_per_class": MIN_SAMPLES_PER_CLASS,
            "dataset": counts,
            "training": dict(self.training),
        }

    # ---------- prediksi ----------
    def predict(self, text: str, with_segments: bool = True, with_explain: bool = True) -> dict:
        text = normalize_text(text)
        n_words = len(words(text))
        spans = segment_spans(text) if (with_segments and n_words >= 120) else []
        if len(spans) > 300:
            spans = spans[:300]
        seg_texts = [text[s:e] for s, e in spans]
        f = stylometric_features(text)

        with self.lock:
            m = self.model
            use_model = bool(m and m.ready)
            if use_model:
                probs = m.proba([text] + seg_texts)
                p_doc, seg_p = float(probs[0]), [float(x) for x in probs[1:]]
                expl = m.explain(text) if with_explain else None
                version_id = self.version_id
                seen = dict(m.seen)
            else:
                p_doc = heuristic_probability(f)
                seg_p = [heuristic_probability(stylometric_features(t)) for t in seg_texts]
                terms = heuristic_terms(f)
                expl = {
                    "indicators": sorted(
                        [{"key": k, "label": FEATURE_LABELS[k], "value": round(f[k], 4),
                          "contribution": round(v, 4), "toward": "ai" if v > 0 else "human"}
                         for k, v in terms.items() if abs(v) > 0.02],
                        key=lambda d: abs(d["contribution"]), reverse=True)[:10],
                    "phrases_ai": [], "phrases_human": [],
                }
                version_id = None
                seen = dict(m.seen) if m else {0: 0, 1: 0}

        segments = []
        for (s, e), p, t in zip(spans, seg_p, seg_texts):
            segments.append({"start": s, "end": e, "words": len(words(t)), "ai_probability": round(p, 4)})

        if segments:
            tot = sum(sg["words"] for sg in segments) or 1
            seg_mean = sum(sg["ai_probability"] * sg["words"] for sg in segments) / tot
            prob = 0.6 * p_doc + 0.4 * seg_mean
            ai_share = sum(sg["words"] for sg in segments if sg["ai_probability"] >= 0.5) / tot
        else:
            prob, ai_share = p_doc, (1.0 if p_doc >= 0.5 else 0.0)

        code, label = verdict_for(prob, segments)
        warnings = []
        if not use_model:
            warnings.append("Model belum cukup terlatih — skor memakai heuristik gaya bahasa. "
                            f"Tambahkan minimal {MIN_SAMPLES_PER_CLASS} contoh per kelas di Mode Belajar.")
        total_seen = seen[0] + seen[1]
        if use_model and total_seen < 200:
            warnings.append(f"Model baru belajar dari {total_seen} contoh; akurasi masih terbatas.")
        if n_words < 50:
            warnings.append("Teks sangat pendek (<50 kata); hasil kurang dapat diandalkan.")
        reliability = "rendah" if (not use_model or n_words < 50 or total_seen < 200) else (
            "sedang" if total_seen < 1000 or abs(prob - 0.5) < 0.2 else "tinggi")

        return {
            "ai_probability": round(prob, 4),
            "document_probability": round(p_doc, 4),
            "ai_share": round(ai_share, 4),
            "verdict": code,
            "verdict_label": label,
            "mode": "model" if use_model else "heuristic",
            "reliability": reliability,
            "warnings": warnings,
            "model_version_id": version_id,
            "stats": {"words": n_words, "chars": len(text), "segments": len(segments),
                      "sentences": len(sentence_spans(text))},
            "features": {k: round(v, 4) for k, v in f.items()},
            "segments": segments,
            "explanation": expl,
            "text": text,
        }

    # ---------- belajar incremental ----------
    def learn(self, sample_ids: list[int]) -> dict:
        new = db.fetch_samples_by_ids(sample_ids)
        if not new:
            return {"learned": 0, "results": []}
        replay = db.fetch_replay([r["id"] for r in new], REPLAY_PER_CLASS)
        results = []
        with self.lock:
            created = False
            if self.model is None:
                self.model = DetectorModel()
                created = True
            m = self.model
            # Evaluasi prequential: tebak dulu sebelum belajar (ukuran akurasi online)
            if m.ready:
                probs = m.proba([r["text"] for r in new])
                for r, p in zip(new, probs):
                    ok = int(p >= 0.5) == int(r["label"])
                    m.prequential["n"] += 1
                    m.prequential["correct"] += int(ok)
                    results.append({"id": r["id"], "predicted_ai_probability": round(float(p), 4),
                                    "label": int(r["label"]), "correct": ok})
            else:
                results = [{"id": r["id"], "predicted_ai_probability": None, "label": int(r["label"]),
                            "correct": None} for r in new]

            texts = [r["text"] for r in new] + [r["text"] for r in replay]
            labels = [int(r["label"]) for r in new] + [int(r["label"]) for r in replay]
            exp_t, exp_y, _ = expand(texts, labels, max_chunks=3)
            n_new_exp = len(expand([r["text"] for r in new], [int(r["label"]) for r in new], max_chunks=3)[0])
            boost = [2.0] * n_new_exp + [1.0] * (len(exp_y) - n_new_exp)
            weights = _balanced_weights(exp_y, boost) if len(set(exp_y)) > 1 else np.asarray(boost)
            m.partial_learn(exp_t, exp_y, weights, epochs=3, seed=m.updates)
            for r in new:
                m.seen[int(r["label"])] += 1
            m.updates += 1
            m.since_full += len(new)

            if created or self.version_id is None:
                self.version_id = self._new_version(m, "incremental", None, "Dibuat otomatis dari Mode Belajar")
                db.set_active(self.version_id)
            else:
                m.save(self._path(self.version_id))
                db.update_version(self.version_id, updates=m.updates, n_human=m.seen[0], n_ai=m.seen[1],
                                  n_samples=m.seen[0] + m.seen[1])
            since_full = m.since_full
        db.mark_learned([r["id"] for r in new])

        auto = False
        if AUTO_RETRAIN_EVERY and since_full >= AUTO_RETRAIN_EVERY:
            auto = self.start_full_train(reason=f"otomatis setelah {since_full} sampel baru")
        return {"learned": len(new), "results": results, "auto_retrain_started": auto, "status": self.status()}

    # ---------- latih ulang penuh ----------
    def start_full_train(self, reason: str = "manual") -> bool:
        with self.lock:
            if self.training["running"]:
                return False
            self.training.update(running=True, stage="menyiapkan data", started_at=_now(),
                                 finished_at=None, error=None, reason=reason)
        threading.Thread(target=self._full_train_job, daemon=True).start()
        return True

    def _stage(self, s: str):
        self.training["stage"] = s
        log.info("[latih penuh] %s", s)

    def _full_train_job(self):
        t0 = time.time()
        try:
            rows = db.fetch_all_samples()
            max_id = max((r["id"] for r in rows), default=0)
            texts = [r["text"] for r in rows]
            labels = [int(r["label"]) for r in rows]
            c = Counter(labels)
            if min(c[0], c[1]) < 2:
                raise ValueError("Butuh minimal 2 contoh untuk setiap kelas (manusia & AI).")

            metrics = None
            alphas = [1e-5, 3e-5, 1e-4, 3e-4, 1e-3]
            best_alpha = 1e-4
            if min(c[0], c[1]) >= 5:
                tr_t, te_t, tr_y, te_y = train_test_split(texts, labels, test_size=0.2, stratify=labels,
                                                          random_state=42)
                ctr = Counter(tr_y)
                if min(ctr.values()) >= 12:
                    self._stage("memilih parameter terbaik")
                    a_t, v_t, a_y, v_y = train_test_split(tr_t, tr_y, test_size=0.25, stratify=tr_y,
                                                          random_state=7)
                    scores = {}
                    for a in alphas:
                        mm = DetectorModel(a)
                        mm.fit_full(a_t, a_y, a)
                        scores[a] = f1_score(v_y, (mm.proba(v_t) >= 0.5).astype(int), zero_division=0)
                    best_alpha = max(scores, key=lambda a: (scores[a], -abs(np.log10(a) + 4)))
                self._stage("evaluasi pada data uji (20%)")
                mm = DetectorModel(best_alpha)
                mm.fit_full(tr_t, tr_y, best_alpha)
                p = mm.proba(te_t)
                pred = (p >= 0.5).astype(int)
                cm = confusion_matrix(te_y, pred, labels=[0, 1]).tolist()
                metrics = {
                    "accuracy": round(float(accuracy_score(te_y, pred)), 4),
                    "precision_ai": round(float(precision_score(te_y, pred, zero_division=0)), 4),
                    "recall_ai": round(float(recall_score(te_y, pred, zero_division=0)), 4),
                    "f1_ai": round(float(f1_score(te_y, pred, zero_division=0)), 4),
                    "roc_auc": round(float(roc_auc_score(te_y, p)), 4) if len(set(te_y)) > 1 else None,
                    "confusion_matrix": cm,  # [[TN, FP], [FN, TP]]; positif = AI
                    "n_train": len(tr_y), "n_test": len(te_y),
                }

            self._stage("melatih model final dengan seluruh data")
            final = DetectorModel(best_alpha)
            final.fit_full(texts, labels, best_alpha)
            if metrics is not None:
                metrics["alpha"] = best_alpha
                metrics["seconds"] = round(time.time() - t0, 1)
            vid = self._new_version(final, "full", metrics, f"Latih ulang penuh ({self.training['reason']})")
            with self.lock:
                db.set_active(vid)
                self.model, self.version_id = final, vid
            # Sampel yang masuk selama pelatihan berlangsung dipelajari secara incremental
            late = db.fetch_samples_after(max_id)
            if late:
                self._stage(f"mempelajari {len(late)} sampel yang masuk selama pelatihan")
                for i in range(0, len(late), 32):
                    self.learn(late[i:i + 32])
            db.mark_learned([r["id"] for r in rows])
            self.training["last_version_id"] = vid
            self._stage("selesai")
        except Exception as e:  # noqa: BLE001
            log.exception("Latih ulang gagal")
            self.training["error"] = str(e)
            self._stage("gagal")
        finally:
            self.training["running"] = False
            self.training["finished_at"] = _now()

    def activate(self, version_id: int) -> dict:
        v = db.get_version(version_id)
        if not v:
            raise KeyError("Versi tidak ditemukan")
        m = DetectorModel.load(v["file_path"])
        with self.lock:
            db.set_active(version_id)
            self.model, self.version_id = m, version_id
        return self.status()


manager = ModelManager()
