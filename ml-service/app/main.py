"""Layanan ML (FastAPI). Hanya diakses oleh server Node di jaringan internal."""
import logging

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import db
from .config import MAX_TEXT_CHARS, MAX_UPLOAD_MB
from .extract import ExtractError, extract
from .model import manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="Detektor Teks AI — ML Service", version="1.0.0")


@app.on_event("startup")
def _startup():
    manager.load_active()


class PredictIn(BaseModel):
    text: str = Field(..., min_length=1)
    segments: bool = True
    explain: bool = True


class LearnIn(BaseModel):
    sample_ids: list[int] = Field(..., min_length=1, max_length=500)


class TrainIn(BaseModel):
    reason: str = "manual"


@app.get("/health")
def health():
    return {"ok": True, "model_loaded": manager.model is not None}


@app.get("/model/status")
def model_status():
    return manager.status()


@app.post("/predict")
def predict(body: PredictIn):
    if len(body.text) > MAX_TEXT_CHARS:
        raise HTTPException(413, f"Teks terlalu panjang (maks {MAX_TEXT_CHARS:,} karakter).")
    return manager.predict(body.text, body.segments, body.explain)


@app.post("/learn")
def learn(body: LearnIn):
    return manager.learn(body.sample_ids)


@app.post("/train/full")
def train_full(body: TrainIn):
    started = manager.start_full_train(body.reason)
    return {"started": started, "training": manager.training}


@app.get("/model/versions")
def versions():
    return {"versions": db.list_versions(), "active_id": manager.version_id}


@app.post("/model/versions/{version_id}/activate")
def activate(version_id: int):
    try:
        return manager.activate(version_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Gagal mengaktifkan versi: {e}") from e


@app.post("/extract")
async def extract_file(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File terlalu besar (maks {MAX_UPLOAD_MB} MB).")
    try:
        out = extract(file.filename or "", data)
    except ExtractError as e:
        raise HTTPException(422, str(e)) from e
    return {"filename": file.filename, **out}


class SimIndexIn(BaseModel):
    text: str = Field(..., min_length=1)
    doc_key: str = Field(..., min_length=1, max_length=500)
    title: str | None = None
    authors: str | None = None
    published: str | None = None
    url: str | None = None


@app.post("/sim/index")
def sim_index(body: SimIndexIn):
    """Indeks dokumen unggahan manual ke repositori (modul Cek Kemiripan)."""
    from .similarity import store as sim_store
    from .similarity.fingerprint import tokenize

    words = len(tokenize(body.text)[0])
    if words < 40:
        raise HTTPException(422, f"Teks terlalu pendek untuk diindeks ({words} kata).")
    sid = sim_store.system_source_id("upload")
    doc_id, status = sim_store.upsert_document(sid, body.doc_key, body.text, url=body.url, title=body.title,
                                               authors=body.authors, published=body.published)
    return {"id": doc_id, "status": status, "words": words}


@app.post("/features")
def features(body: PredictIn):
    from .features import FEATURE_LABELS, stylometric_features
    f = stylometric_features(body.text)
    return {"features": [{"key": k, "label": FEATURE_LABELS[k], "value": v} for k, v in f.items()]}
