"""
Fingerprint dokumen untuk deteksi kemiripan (teknik yang sama dengan Turnitin/MOSS):

  teks → token (dengan posisi karakter) → shingle K kata → hash 64-bit → winnowing

Winnowing memilih hash minimum di setiap jendela W shingle, sehingga indeks jauh lebih
kecil tetapi setiap salinan sepanjang ≥ K+W-1 kata dijamin memiliki ≥1 fingerprint sama.
"""
from __future__ import annotations

import hashlib
import re
from collections import deque

from ..features import FUNCTION_WORDS
from .config import SHINGLE_K, WINNOW_W

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
SENTINEL = None  # shingle yang dikecualikan (kutipan / daftar pustaka)


def tokenize(text: str) -> tuple[list[str], list[tuple[int, int]]]:
    toks, spans = [], []
    for m in TOKEN_RE.finditer(text):
        toks.append(m.group(0).casefold())
        spans.append((m.start(), m.end()))
    return toks, spans


def _h(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big", signed=True)


def shingle_hashes(tokens: list[str], k: int = SHINGLE_K, excluded: list[bool] | None = None) -> list[int | None]:
    """Hash setiap shingle (posisi i = kata i..i+k-1). None bila menyentuh kata yang dikecualikan."""
    out: list[int | None] = []
    for i in range(len(tokens) - k + 1):
        if excluded is not None and any(excluded[i:i + k]):
            out.append(SENTINEL)
        else:
            out.append(_h(" ".join(tokens[i:i + k])))
    return out


def _informative(tokens: list[str], i: int, k: int) -> bool:
    # shingle yang hampir seluruhnya kata fungsi ("dan yang di dalam hal") tidak dipakai sebagai fingerprint
    return sum(1 for t in tokens[i:i + k] if t not in FUNCTION_WORDS) >= 2


def winnow(tokens: list[str], hashes: list[int | None], w: int = WINNOW_W, k: int = SHINGLE_K) -> set[int]:
    vals = [(h if (h is not None and _informative(tokens, i, k)) else None) for i, h in enumerate(hashes)]
    picked: set[int] = set()
    dq: deque[int] = deque()  # indeks dengan nilai menaik (untuk minimum jendela geser)
    last = -1
    for i, v in enumerate(vals):
        if v is not None:
            while dq and vals[dq[-1]] >= v:
                dq.pop()
            dq.append(i)
        while dq and dq[0] <= i - w:
            dq.popleft()
        if i >= w - 1 and dq and dq[0] != last:
            last = dq[0]
            picked.add(vals[last])
    if not picked:  # dokumen lebih pendek dari jendela
        picked = {v for v in vals if v is not None}
    return picked


def document_fingerprints(text: str) -> set[int]:
    toks, _ = tokenize(text)
    return winnow(toks, shingle_hashes(toks))
