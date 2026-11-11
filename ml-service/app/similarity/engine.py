"""
Proses satu pemeriksaan kemiripan:

 1. Tokenisasi + pengecualian (kutipan, daftar pustaka) → hash shingle → fingerprint (winnowing)
 2. Kandidat dari indeks lokal: repositori jurnal, situs yang di-crawl, cache web, tugas mahasiswa
 3. Pencarian web: kalimat paling khas dicari sebagai frasa persis, halaman hasil diunduh & diindeks
 4. Penyelarasan kata per kata dengan setiap kandidat → laporan (indeks kemiripan + sumber utama)
"""
from __future__ import annotations

import bisect
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from ..features import FUNCTION_WORDS, sentence_spans
from . import config as C
from . import search, store
from .align import SourceHit, build_report, exclusion_mask, find_matches
from .fetch import FetchError, Fetcher, extract_page
from .fingerprint import shingle_hashes, tokenize, winnow

log = logging.getLogger("sim.engine")

SKIP_DOMAINS = ("youtube.com", "youtu.be", "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com")
DEFAULTS = {"web": True, "repository": True, "exclude_quotes": False, "exclude_bibliography": True,
            "min_words": C.MIN_MATCH_WORDS, "categories": ["internet", "publication", "submission"],
            "save_to_repository": False}


def pick_queries(text: str, toks: list[str], spans, mask: list[bool], n_max: int, phrase_words: int = 10) -> list[str]:
    """Pilih kalimat paling khas, tersebar merata di seluruh dokumen, lalu ambil 10 kata sebagai frasa persis."""
    if not toks or n_max <= 0:
        return []
    starts = [s for s, _ in spans]
    cands = []
    for s, e in sentence_spans(text):
        a = bisect.bisect_left(starts, s)
        b = bisect.bisect_left(starts, e)
        n = b - a
        if n < phrase_words + 2 or any(mask[a:b]):
            continue
        words = toks[a:b]
        content = sum(1 for w in words if w not in FUNCTION_WORDS and len(w) > 3 and not w.isdigit()) / n
        if content < 0.45:
            continue
        mid = a + max(0, (n - phrase_words) // 2)
        phrase = text[spans[mid][0]:spans[mid + phrase_words - 1][1]]
        phrase = " ".join(phrase.split()).strip(" ,;:-–—()[]\"'“”")
        cands.append((a, content * min(n, 30), phrase))
    if not cands:
        return []
    bins: dict[int, tuple] = {}
    total = len(toks)
    for a, score, phrase in cands:
        k = min(n_max - 1, a * n_max // total)
        if k not in bins or score > bins[k][0]:
            bins[k] = (score, phrase)
    return list(dict.fromkeys(bins[k][1] for k in sorted(bins)))  # buang kueri kembar


class Progress:
    def __init__(self, check_id: int):
        self.id = check_id

    def __call__(self, pct: int, stage: str):
        store.update_check(self.id, progress=pct, stage=stage)


def _web_candidates(text, toks, spans, mask, prog: Progress, info: dict) -> list[int]:
    queries = pick_queries(text, toks, spans, mask, C.SEARCH_MAX_QUERIES)
    info["queries"] = queries
    scores: dict[str, int] = {}
    titles: dict[str, str] = {}
    for i, q in enumerate(queries):
        prog(15 + int(25 * i / max(1, len(queries))), f"mencari di web ({i + 1}/{len(queries)})")
        try:
            for r in search.search(f'"{q}"', C.SEARCH_RESULTS_PER_QUERY):
                url = (r.get("url") or "").split("#")[0]
                if url.startswith("http") and not any(d in url for d in SKIP_DOMAINS):
                    scores[url] = scores.get(url, 0) + 1
                    titles.setdefault(url, r.get("title") or "")
        except search.SearchError as e:
            info["errors"].append(str(e))
            if "401" in str(e) or "403" in str(e) or "429" in str(e):
                break  # kunci API salah / kuota habis — tidak perlu mencoba kueri lain
    ranked = sorted(scores, key=lambda u: -scores[u])
    info["urls_found"] = len(ranked)
    ids, to_fetch = [], []
    for url in ranked:
        cached = store.fresh_web_doc(url, C.WEB_CACHE_DAYS)
        if cached:
            ids.append(int(cached["id"]))
        elif len(to_fetch) < C.WEB_MAX_FETCH:
            to_fetch.append(url)
    info["cached"] = len(ids)
    if not to_fetch:
        return ids
    web_src = store.system_source_id("web_search")
    fetcher = Fetcher(allow_private=C.ALLOW_PRIVATE_FETCH, respect_robots=True)
    done = 0

    def grab(url):
        page = fetcher.get(url)
        x = extract_page(page)
        if x.get("pdf_url") and len(x["text"]) < 3000:
            try:
                pdf = extract_page(fetcher.get(x["pdf_url"]))
                if len(pdf["text"]) > len(x["text"]):
                    x["text"] = pdf["text"]
            except FetchError:
                pass
        if len(tokenize(x["text"])[0]) < C.MIN_DOC_WORDS:
            raise FetchError("teks terlalu sedikit")
        doc_id, _ = store.upsert_document(web_src, url, x["text"], url=page.url, title=x["title"] or titles.get(url),
                                          authors=x["authors"], published=x["published"])
        return doc_id

    try:
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(grab, u): u for u in to_fetch}
            for f in as_completed(futs):
                done += 1
                prog(40 + int(25 * done / len(to_fetch)), f"mengunduh halaman web ({done}/{len(to_fetch)})")
                try:
                    ids.append(f.result())
                    info["fetched"] += 1
                except Exception as e:  # noqa: BLE001
                    info["fetch_failed"] += 1
                    if len(info["errors"]) < 20:
                        info["errors"].append(f"{futs[f]}: {e}")
    finally:
        fetcher.close()
    return ids


def run_check(job: dict):
    prog = Progress(job["id"])
    opts = {**DEFAULTS, **(job.get("options") or {})}
    text = job["text"]
    min_words = max(4, int(opts["min_words"]))
    toks, spans = tokenize(text)
    mask, ranges = exclusion_mask(text, spans, bool(opts["exclude_quotes"]), bool(opts["exclude_bibliography"]))
    q_hashes = shingle_hashes(toks, excluded=mask)
    fps = winnow(toks, q_hashes)
    exclude_ids = [int(x) for x in (opts.get("exclude_doc_ids") or [])]
    if job.get("submission_doc_id"):
        exclude_ids.append(int(job["submission_doc_id"]))

    cand: list[int] = []
    if opts["repository"]:
        prog(8, "mencocokkan dengan repositori & indeks")
        cand += store.candidates(fps, exclude_ids, C.MAX_CANDIDATES)

    web = {"provider": search.provider_name(), "enabled": False, "queries": [], "urls_found": 0, "cached": 0,
           "fetched": 0, "fetch_failed": 0, "errors": []}
    prev = (job.get("result") or {}).get("web") or {}
    if opts["web"] and "internet" in opts["categories"]:
        if opts.get("reuse_web") and prev.get("doc_ids") is not None:
            web.update({k: prev.get(k) for k in ("queries", "urls_found") if k in prev})
            web["enabled"], web["reused"] = True, True
            web_ids = [i for i in prev["doc_ids"] if i not in exclude_ids]
        elif search.is_configured():
            web["enabled"] = True
            web_ids = _web_candidates(text, toks, spans, mask, prog, web)
        else:
            web_ids = []
            web["errors"].append("Pencarian web belum dikonfigurasi (SEARCH_PROVIDER & kunci API).")
        web["doc_ids"] = web_ids
        cand += web_ids
    cand = list(dict.fromkeys(i for i in cand if i not in exclude_ids))

    prog(70, f"menyelaraskan dengan {len(cand)} kandidat sumber")
    hits = []
    allowed = set(opts["categories"])
    for n, d in enumerate(store.load_documents(cand)):
        if d["category"] not in allowed:
            continue
        dt, dsp = tokenize(d["text"])
        runs = find_matches(q_hashes, shingle_hashes(dt), mask, min_words=min_words)
        if runs:
            hits.append(SourceHit(d, runs, d["text"], dsp))
        if n % 10 == 0:
            prog(70 + int(25 * n / max(1, len(cand))), "menyelaraskan teks")

    report = build_report(text, spans, hits, ranges, min_words)
    report["web"] = web
    report["options"] = opts
    report["candidates"] = len(cand)
    report["generated_at"] = datetime.now(timezone.utc).isoformat()

    sub_id = job.get("submission_doc_id")
    if opts.get("save_to_repository") and not sub_id:
        prog(97, "menyimpan ke repositori tugas")
        sub_id, _ = store.upsert_document(store.system_source_id("submission"), f"check:{job['id']}", text,
                                          title=job.get("title"), authors=job.get("author"))
    cat = report["by_category"]
    store.update_check(job["id"], status="done", progress=100, stage="selesai", result=report,
                       similarity=report["similarity"], internet=cat.get("internet", 0),
                       publication=cat.get("publication", 0), submission=cat.get("submission", 0),
                       submission_doc_id=sub_id, finished_at=datetime.now(timezone.utc))
    return report
