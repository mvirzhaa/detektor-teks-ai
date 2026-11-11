"""
Penyelarasan teks dan penyusunan laporan ala Turnitin.

- find_matches(): mencari bagian yang sama antara dokumen yang diperiksa dan satu sumber,
  menoleransi perubahan kecil (1–3 kata disisipkan/diganti).
- build_report(): menghitung Indeks Kemiripan, persentase per kategori (Internet, Publikasi,
  Tugas Mahasiswa) dan per sumber. Setiap kata hanya "dimiliki" satu sumber (sumber terbesar
  lebih dulu), persis cara Turnitin menyusun daftar "Primary Sources".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .config import SHINGLE_K

QUOTE_RE = re.compile(r"[\"“”«»]([^\"“”«»]{20,800})[\"“”«»]")
BIBLIO_RE = re.compile(
    r"^\s*(?:bab\s+[ivx\d]+\s*)?(daftar\s+pustaka|daftar\s+referensi|referensi|rujukan|kepustakaan|"
    r"references|bibliography|works\s+cited|literature\s+cited)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def exclusion_mask(text: str, spans: list[tuple[int, int]], exclude_quotes: bool, exclude_biblio: bool):
    """Kembalikan (mask per kata, daftar rentang karakter yang dikecualikan)."""
    ranges: list[tuple[int, int, str]] = []
    if exclude_biblio:
        for m in BIBLIO_RE.finditer(text):
            if m.start() >= len(text) * 0.4:  # judul daftar pustaka biasanya di bagian akhir
                ranges.append((m.start(), len(text), "biblio"))
                break
    if exclude_quotes:
        ranges += [(m.start(), m.end(), "quote") for m in QUOTE_RE.finditer(text)]
    mask = [False] * len(spans)
    if ranges:
        for i, (s, e) in enumerate(spans):
            for rs, re_, _ in ranges:
                if s >= rs and e <= re_:
                    mask[i] = True
                    break
    return mask, ranges


@dataclass
class Run:
    qs: int  # kata awal (inklusif) di dokumen yang diperiksa
    qe: int  # kata akhir (eksklusif)
    ds: int  # kata awal di sumber
    de: int


def find_matches(q_hashes, d_hashes, q_excluded: list[bool], k: int = SHINGLE_K,
                 min_words: int = 8, max_gap: int = 3) -> list[Run]:
    d_index: dict[int, list[int]] = {}
    for j, h in enumerate(d_hashes):
        if h is not None:
            lst = d_index.setdefault(h, [])
            if len(lst) < 64:
                lst.append(j)

    # 1) rangkaian shingle yang berurutan di kedua dokumen
    runs: list[list[int]] = []  # [q_first_shingle, q_last_shingle, d_first, d_last]
    cur = None
    for i, h in enumerate(q_hashes):
        if h is None:
            continue
        js = d_index.get(h)
        if not js:
            continue
        if cur is not None and i - cur[1] <= 1:
            want = cur[3] + (i - cur[1])
            if want in js:
                cur[1], cur[3] = i, want
                continue
        # mulai rangkaian baru; pilih posisi sumber yang paling dekat dengan rangkaian sebelumnya
        j = js[0]
        if cur is not None:
            j = min(js, key=lambda x: abs(x - (cur[3] + i - cur[1])))
        cur = [i, i, j, j]
        runs.append(cur)

    spans = [Run(r[0], r[1] + k, r[2], r[3] + k) for r in runs]
    spans.sort(key=lambda r: (r.qs, r.ds))

    # 2) gabungkan rangkaian yang terpisah sedikit kata (kata diganti/disisipkan)
    merged: list[Run] = []
    for r in spans:
        if merged:
            p = merged[-1]
            q_gap, d_gap = r.qs - p.qe, r.ds - p.de
            if -k <= q_gap <= max_gap and -k <= d_gap <= max_gap + 2 and not any(q_excluded[p.qe:max(p.qe, r.qs)]):
                p.qe, p.de = max(p.qe, r.qe), max(p.de, r.de)
                continue
        merged.append(Run(r.qs, r.qe, r.ds, r.de))
    return [r for r in merged if r.qe - r.qs >= min_words]


@dataclass
class SourceHit:
    doc: dict
    runs: list[Run]
    d_text: str = ""
    d_spans: list = field(default_factory=list)

    def words(self) -> set[int]:
        s: set[int] = set()
        for r in self.runs:
            s.update(range(r.qs, r.qe))
        return s


def _ranges(idxs: set[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for i in sorted(idxs):
        if out and out[-1][1] == i:
            out[-1][1] = i + 1
        else:
            out.append([i, i + 1])
    return out


def _domain(url: str | None) -> str:
    if not url:
        return ""
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def build_report(text: str, q_spans, hits: list[SourceHit], excluded_ranges, min_words: int, max_sources: int = 60):
    total = len(q_spans) or 1
    hits = [h for h in hits if h.runs]
    hits.sort(key=lambda h: len(h.words()), reverse=True)

    owner = [-1] * len(q_spans)
    sources = []
    for h in hits:
        w = h.words()
        owned = [i for i in sorted(w) if owner[i] == -1]
        if len(owned) < min(min_words, 4):
            continue
        idx = len(sources)
        for i in owned:
            owner[i] = idx
        doc = h.doc
        matches = []
        for r in h.runs[:40]:
            src = ""
            if h.d_spans:
                a = h.d_spans[r.ds][0]
                b = h.d_spans[min(r.de, len(h.d_spans)) - 1][1]
                src = h.d_text[a:b]
                if len(src) > 900:
                    src = src[:900] + "…"
            matches.append({
                "qs": r.qs, "qe": r.qe,
                "q_start": q_spans[r.qs][0], "q_end": q_spans[r.qe - 1][1],
                "words": r.qe - r.qs, "source_text": src,
            })
        sources.append({
            "doc_id": doc["id"],
            "title": doc.get("title") or _domain(doc.get("url")) or "Tanpa judul",
            "url": doc.get("url"),
            "domain": _domain(doc.get("url")),
            "authors": doc.get("authors"),
            "published": doc.get("published"),
            "category": doc["category"],
            "source_name": doc.get("source_name"),
            "matched_words": len(w),
            "owned_words": len(owned),
            "percent": round(100 * len(owned) / total, 2),
            "ranges": _ranges(w),
            "matches": matches,
        })
        if len(sources) >= max_sources:
            break

    covered = sum(1 for o in owner if o != -1)
    cats = {"internet": set(), "publication": set(), "submission": set()}
    for s in sources:
        for a, b in s["ranges"]:
            cats.setdefault(s["category"], set()).update(range(a, b))
    return {
        "similarity": round(100 * covered / total, 2),
        "by_category": {c: round(100 * len(v) / total, 2) for c, v in cats.items()},
        "total_words": len(q_spans),
        "matched_words": covered,
        "sources": sources,
        "word_offsets": [[s, e] for s, e in q_spans],
        "excluded": [{"start": a, "end": b, "kind": k} for a, b, k in excluded_ranges],
    }
