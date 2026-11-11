"""
Perintah baris untuk dataset besar.

  python -m app.cli import data.csv [--text-col text] [--label-col label] [--no-train]
  python -m app.cli import data.jsonl
  python -m app.cli retrain
  python -m app.cli evaluate uji.csv        # uji model aktif pada dataset terpisah
  python -m app.cli status
"""
import argparse
import csv
import hashlib
import json
import re
import sys
import time

from . import db
from .features import detect_language, normalize_text, words
from .model import manager

csv.field_size_limit(sys.maxsize)

AI_LABELS = {"1", "ai", "a.i.", "generated", "machine", "gpt", "llm", "bot", "true", "ya", "buatan ai", "mesin"}
HUMAN_LABELS = {"0", "human", "manusia", "human-written", "orang", "false", "tidak", "asli", "buatan manusia"}
TEXT_COLS = ["text", "teks", "content", "isi", "essay", "body", "document"]
LABEL_COLS = ["label", "kelas", "generated", "is_ai", "class", "target", "source_label"]


def parse_label(v) -> int | None:
    s = str(v).strip().lower()
    if s in AI_LABELS:
        return 1
    if s in HUMAN_LABELS:
        return 0
    try:
        f = float(s)
        return 1 if f >= 0.5 else 0
    except ValueError:
        return None


def text_hash(text: str) -> str:
    key = re.sub(r"\s+", " ", text.lower()).strip()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def read_rows(path: str, text_col: str | None, label_col: str | None):
    if path.endswith((".jsonl", ".ndjson")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)
        return
    with open(path, encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample else csv.excel
        yield from csv.DictReader(fh, dialect=dialect)


def pick(row: dict, preferred: str | None, candidates: list[str]):
    keys = {k.lower().strip(): k for k in row.keys() if k}
    for c in ([preferred.lower()] if preferred else []) + candidates:
        if c in keys:
            return row[keys[c]]
    return None


def cmd_import(a):
    batch, total, skipped, inserted, dups = [], 0, 0, 0, 0

    def flush():
        nonlocal batch, inserted, dups
        ids, d = db.insert_samples(batch)
        inserted += len(ids)
        dups += d
        batch = []

    for row in read_rows(a.path, a.text_col, a.label_col):
        total += 1
        text = normalize_text(str(pick(row, a.text_col, TEXT_COLS) or ""))
        label = parse_label(pick(row, a.label_col, LABEL_COLS))
        n = len(words(text))
        if label is None or n < a.min_words:
            skipped += 1
            continue
        batch.append({"text": text, "text_hash": text_hash(text), "label": label, "source": "cli",
                      "filename": a.path.split("/")[-1], "language": detect_language(text), "word_count": n})
        if len(batch) >= 500:
            flush()
            print(f"  … {inserted} masuk", flush=True)
    if batch:
        flush()
    print(f"Baris dibaca: {total} | masuk: {inserted} | duplikat: {dups} | dilewati: {skipped}")
    if inserted and not a.no_train:
        cmd_retrain(a)


def _server_retrain(url: str) -> bool:
    """Minta server ML yang sedang berjalan melatih ulang, agar model di memorinya ikut terbarui."""
    import urllib.request

    def req(path, data=None):
        r = urllib.request.Request(url + path, data=data, headers={"content-type": "application/json"})
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.loads(resp.read())

    try:
        out = req("/train/full", json.dumps({"reason": "CLI"}).encode())
    except Exception:  # noqa: BLE001
        return False
    if not out.get("started"):
        print("Server sedang melatih model lain; coba lagi nanti.")
        return True
    print(f"Latih ulang dijalankan oleh server ML ({url}).")
    while True:
        time.sleep(3)
        st = req("/model/status")
        tr = st["training"]
        if not tr["running"]:
            if tr["error"]:
                print("GAGAL:", tr["error"])
                sys.exit(1)
            print(json.dumps(st["version"], indent=2, default=str))
            return True
        print(f"  tahap: {tr['stage']}", flush=True)


def cmd_retrain(_a):
    import os

    if _server_retrain(os.getenv("ML_SELF_URL", "http://localhost:8000")):
        return
    print("Server ML tidak berjalan — melatih secara lokal. Restart server ML setelahnya agar model baru dimuat.")
    manager.load_active()
    manager.start_full_train("CLI")
    while manager.training["running"]:
        print(f"  tahap: {manager.training['stage']}", flush=True)
        time.sleep(3)
    if manager.training["error"]:
        print("GAGAL:", manager.training["error"])
        sys.exit(1)
    print(json.dumps(manager.status()["version"], indent=2, default=str))


def cmd_evaluate(a):
    from sklearn.metrics import classification_report

    manager.load_active()
    if not (manager.model and manager.model.ready):
        sys.exit("Belum ada model yang siap.")
    texts, ys = [], []
    for row in read_rows(a.path, a.text_col, a.label_col):
        t = normalize_text(str(pick(row, a.text_col, TEXT_COLS) or ""))
        y = parse_label(pick(row, a.label_col, LABEL_COLS))
        if y is not None and len(words(t)) >= a.min_words:
            texts.append(t); ys.append(y)
    p = manager.model.proba(texts)
    print(classification_report(ys, (p >= 0.5).astype(int), target_names=["manusia", "AI"], digits=3))


def cmd_status(_a):
    manager.load_active()
    print(json.dumps(manager.status(), indent=2, default=str))


def main():
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("import", "evaluate"):
        p = sub.add_parser(name)
        p.add_argument("path")
        p.add_argument("--text-col")
        p.add_argument("--label-col")
        p.add_argument("--min-words", type=int, default=20)
        p.add_argument("--no-train", action="store_true")
    sub.add_parser("retrain")
    sub.add_parser("status")
    a = ap.parse_args()
    {"import": cmd_import, "retrain": cmd_retrain, "evaluate": cmd_evaluate, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
