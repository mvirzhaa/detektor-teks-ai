"""
Crawler sumber data. Dijalankan oleh worker setiap malam (atau manual dari halaman Sumber Data).

Jenis sumber:
  oai_pmh   — repositori jurnal/skripsi: OJS, EPrints, DSpace (endpoint OAI-PMH). Panen bertahap
              memakai parameter `from`, teks lengkap diambil dari PDF lewat meta `citation_pdf_url`.
  sitemap   — situs web dengan sitemap.xml (berita, blog, portal). Hanya halaman baru/berubah (lastmod).
  web_crawl — telusuri tautan dari URL awal (domain yang sama), kedalaman terbatas.
  rss       — feed RSS/Atom; setiap item diunduh halamannya.
"""
from __future__ import annotations

import gzip
import logging
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from lxml import etree

from . import config as C
from . import store
from .fetch import FetchError, Fetcher, RobotsDisallowed, extract_links, extract_page
from .fingerprint import tokenize

log = logging.getLogger("sim.crawl")

XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, recover=True)
NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "dc": "http://purl.org/dc/elements/1.1/",
      "sm": "http://www.sitemaps.org/schemas/sitemap/0.9", "atom": "http://www.w3.org/2005/Atom"}
SKIP_EXT = re.compile(r"\.(jpe?g|png|gif|webp|svg|ico|css|js|zip|rar|7z|mp[34]|avi|mov|wav|exe|apk|dmg|iso|"
                      r"xlsx?|pptx?|docx?|csv|xml|json|woff2?|ttf)(\?|$)", re.I)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ctx:
    def __init__(self, run: dict, source: dict):
        self.run_id = run["id"]
        self.source = source
        self.cfg = source.get("config") or {}
        self.state = dict(source.get("state") or {})
        self.max_docs = int(self.cfg.get("max_docs") or C.CRAWL_MAX_DOCS)
        self.stats = {"docs_seen": 0, "docs_new": 0, "docs_updated": 0, "docs_skipped": 0, "docs_failed": 0}
        self.fetcher = Fetcher(allow_private=C.CRAWL_ALLOW_PRIVATE or C.ALLOW_PRIVATE_FETCH, respect_robots=True)
        self._last_flush = time.monotonic()
        self.include = re.compile(self.cfg["include"]) if self.cfg.get("include") else None
        self.exclude = re.compile(self.cfg["exclude"]) if self.cfg.get("exclude") else None

    def log(self, msg: str):
        log.info("[run %s] %s", self.run_id, msg)
        store.append_run_log(self.run_id, f"{datetime.now().strftime('%H:%M:%S')} {msg}")

    def bump(self, key: str):
        self.stats[key] += 1
        if time.monotonic() - self._last_flush > 5:
            self.flush()

    def flush(self):
        store.update_run(self.run_id, **self.stats)
        self._last_flush = time.monotonic()

    @property
    def done(self) -> bool:
        return (self.stats["docs_new"] + self.stats["docs_updated"]) >= self.max_docs

    def url_ok(self, url: str) -> bool:
        if SKIP_EXT.search(url):
            return False
        if self.include and not self.include.search(url):
            return False
        if self.exclude and self.exclude.search(url):
            return False
        return True

    def index(self, doc_key: str, text: str, **meta) -> str:
        n = len(tokenize(text)[0])
        if n < C.MIN_DOC_WORDS:
            self.bump("docs_skipped")
            return "pendek"
        _, status = store.upsert_document(self.source["id"], doc_key, text, **meta)
        self.bump({"baru": "docs_new", "diperbarui": "docs_updated"}.get(status, "docs_skipped"))
        return status

    def fetch_and_index(self, url: str, doc_key: str | None = None, conditional: bool = True, **meta) -> str:
        """Unduh satu URL (HTML/PDF), ambil teks, indeks. Mengikuti citation_pdf_url bila ada."""
        doc_key = doc_key or url
        old = store.get_document(self.source["id"], doc_key)
        try:
            page = self.fetcher.get(url, etag=old["etag"] if (old and conditional) else None,
                                    last_modified=old["last_modified"] if (old and conditional) else None)
            if page.status == 304:
                store.touch_document(old["id"])
                self.bump("docs_skipped")
                return "tetap"
            info = extract_page(page)
            text = info["text"]
            if info.get("pdf_url") and self.cfg.get("follow_pdf", True) and info["pdf_url"] != page.url:
                try:
                    pdf = extract_page(self.fetcher.get(info["pdf_url"]))
                    if len(pdf["text"]) > len(text):
                        text = pdf["text"]
                except FetchError as e:
                    self.log(f"PDF gagal {info['pdf_url']}: {e}")
            return self.index(doc_key, text, url=page.url, title=meta.get("title") or info["title"],
                              authors=meta.get("authors") or info["authors"],
                              published=meta.get("published") or info["published"],
                              etag=page.etag, last_modified=page.last_modified)
        except RobotsDisallowed:
            self.bump("docs_skipped")
            self.log(f"dilewati (robots.txt): {url}")
            return "robots"
        except FetchError as e:
            self.bump("docs_failed")
            if self.stats["docs_failed"] <= 30:
                self.log(f"gagal {url}: {e}")
            return "gagal"


# ======================= OAI-PMH =======================
def _dc(md, tag: str) -> list[str]:
    return [(e.text or "").strip() for e in md.iterfind(f".//dc:{tag}", NS) if (e.text or "").strip()]


def crawl_oai(ctx: Ctx):
    base = ctx.source["url"]
    started = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()  # tumpang tindih 1 hari
    params = {"verb": "ListRecords", "metadataPrefix": ctx.cfg.get("metadata_prefix", "oai_dc")}
    if ctx.cfg.get("set"):
        params["set"] = ctx.cfg["set"]
    if ctx.state.get("from") and not ctx.cfg.get("full_harvest"):
        params["from"] = ctx.state["from"]
        ctx.log(f"panen bertahap sejak {params['from']}")
    else:
        ctx.log("panen penuh")
    fulltext = ctx.cfg.get("fulltext", True)
    truncated, pages = False, 0
    while True:
        page = ctx.fetcher.get(base, params=params, check_robots=False)  # endpoint OAI memang untuk dipanen
        pages += 1
        root = etree.fromstring(page.data, XML_PARSER)
        if root is None:
            raise FetchError("Respons OAI bukan XML yang valid")
        err = root.find("oai:error", NS)
        if err is not None:
            if err.get("code") == "noRecordsMatch":
                ctx.log("tidak ada rekaman baru")
                break
            raise FetchError(f"OAI error {err.get('code')}: {err.text}")
        for rec in root.iterfind(".//oai:record", NS):
            header = rec.find("oai:header", NS)
            ident = header.findtext("oai:identifier", namespaces=NS)
            stamp = header.findtext("oai:datestamp", namespaces=NS)
            ctx.bump("docs_seen")
            if header.get("status") == "deleted":
                if store.delete_document(ctx.source["id"], ident):
                    ctx.log(f"dihapus dari indeks: {ident}")
                continue
            old = store.get_document(ctx.source["id"], ident)
            if old and old["last_modified"] == stamp:
                ctx.bump("docs_skipped")
                continue
            md = rec.find("oai:metadata", NS)
            if md is None:
                continue
            title = (_dc(md, "title") or [None])[0]
            authors = "; ".join(_dc(md, "creator")[:10]) or None
            date = (_dc(md, "date") or [None])[0]
            abstract = "\n\n".join(_dc(md, "description"))
            links = [x for x in _dc(md, "identifier") + _dc(md, "relation") if x.startswith("http")]
            landing = next((x for x in links if not x.lower().endswith(".pdf")), links[0] if links else None)
            pdfs = [x for x in links if x.lower().endswith(".pdf") or "/download/" in x or "/bitstream/" in x]
            pdfs += [x.replace("/article/view/", "/article/download/") for x in links
                     if re.search(r"/article/view/\d+/\d+", x)]
            text = ""
            if fulltext:
                for cand in ([landing] if landing else []) + pdfs:
                    try:
                        info = extract_page(ctx.fetcher.get(cand))
                        t = info["text"]
                        if info.get("pdf_url"):
                            try:
                                t2 = extract_page(ctx.fetcher.get(info["pdf_url"]))["text"]
                                t = t2 if len(t2) > len(t) else t
                            except FetchError as e:
                                ctx.log(f"PDF gagal ({ident}): {e}")
                        if len(t) > len(text):
                            text = t
                        if len(tokenize(text)[0]) > 800:  # sudah teks lengkap
                            break
                    except FetchError as e:
                        if ctx.stats["docs_failed"] < 30:
                            ctx.log(f"unduh gagal {cand}: {e}")
            if len(text) < len(abstract) + 200:
                text = "\n\n".join(x for x in [title, abstract, text] if x)
            ctx.index(ident, text, url=landing, title=title, authors=authors, published=date, last_modified=stamp)
            if ctx.done:
                truncated = True
                break
        token = root.find(".//oai:resumptionToken", NS)
        if truncated or token is None or not (token.text or "").strip():
            break
        params = {"verb": "ListRecords", "resumptionToken": token.text.strip()}
    if truncated:
        ctx.log(f"batas {ctx.max_docs} dokumen per malam tercapai — dilanjutkan malam berikutnya")
    else:
        ctx.state["from"] = started
    ctx.state["last_pages"] = pages


# ======================= SITEMAP =======================
def _sitemap_entries(ctx: Ctx, url: str, depth: int = 0, out=None, limit: int = 50000):
    out = [] if out is None else out
    if depth > 3 or len(out) >= limit:
        return out
    try:
        page = ctx.fetcher.get(url)
    except FetchError as e:
        ctx.log(f"sitemap gagal {url}: {e}")
        return out
    data = page.data
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    root = etree.fromstring(data, XML_PARSER)
    if root is None:
        return out
    for sm in root.iterfind("sm:sitemap", NS):
        loc = sm.findtext("sm:loc", namespaces=NS)
        if loc:
            _sitemap_entries(ctx, loc.strip(), depth + 1, out, limit)
    for u in root.iterfind("sm:url", NS):
        loc = (u.findtext("sm:loc", namespaces=NS) or "").strip()
        if loc:
            out.append((loc, (u.findtext("sm:lastmod", namespaces=NS) or "").strip()[:10] or None))
    return out


def crawl_sitemap(ctx: Ctx):
    url = ctx.source["url"]
    maps = ctx.cfg.get("sitemaps") or []
    if not maps:
        if re.search(r"\.xml(\.gz)?$|sitemap", url, re.I):
            maps = [url]
        else:
            p = urlparse(url)
            maps = ctx.fetcher.sitemaps_from_robots(url) or [f"{p.scheme}://{p.netloc}/sitemap.xml"]
    entries = []
    for m in maps:
        _sitemap_entries(ctx, m, out=entries)
    ctx.log(f"{len(entries)} URL di sitemap")
    since = (ctx.state.get("last_run") or "")[:10]
    recrawl = int(ctx.cfg.get("recrawl_days", 30))
    entries.sort(key=lambda e: e[1] or "", reverse=True)  # terbaru dulu
    for loc, lastmod in entries:
        if ctx.done:
            ctx.log(f"batas {ctx.max_docs} dokumen tercapai")
            break
        if not ctx.url_ok(loc):
            continue
        ctx.bump("docs_seen")
        old = store.get_document(ctx.source["id"], loc)
        if old:
            if lastmod and since and lastmod < since:
                ctx.bump("docs_skipped")
                continue
            if not lastmod and old["fetched_at"] > datetime.now(timezone.utc) - timedelta(days=recrawl):
                ctx.bump("docs_skipped")
                continue
        ctx.fetch_and_index(loc)
    ctx.state["last_run"] = _now_iso()


# ======================= WEB CRAWL =======================
def crawl_web(ctx: Ctx):
    seeds = ctx.cfg.get("seeds") or [ctx.source["url"]]
    hosts = {urlparse(s).netloc for s in seeds} | set(ctx.cfg.get("hosts") or [])
    max_depth = int(ctx.cfg.get("depth", 2))
    max_pages = int(ctx.cfg.get("max_pages", ctx.max_docs * 3))
    recrawl = int(ctx.cfg.get("recrawl_days", 30))
    q = deque((s, 0) for s in seeds)
    seen, fetched = set(), 0
    while q and fetched < max_pages and not ctx.done:
        url, d = q.popleft()
        if url in seen or urlparse(url).netloc not in hosts:
            continue
        seen.add(url)
        is_seed = d == 0
        if not is_seed and not ctx.url_ok(url):
            continue
        old = store.get_document(ctx.source["id"], url)
        fresh = old and old["fetched_at"] > datetime.now(timezone.utc) - timedelta(days=recrawl)
        if fresh and d >= max_depth:
            ctx.bump("docs_skipped")
            continue
        try:
            page = ctx.fetcher.get(url)
        except FetchError as e:
            ctx.bump("docs_failed")
            if ctx.stats["docs_failed"] <= 30:
                ctx.log(f"gagal {url}: {e}")
            continue
        fetched += 1
        ctx.bump("docs_seen")
        if "html" in page.content_type and d < max_depth:
            for link in extract_links(page.text_html(), page.url):
                if link not in seen and urlparse(link).netloc in hosts:
                    q.append((link, d + 1))
        if fresh:
            ctx.bump("docs_skipped")
            continue
        try:
            info = extract_page(page)
        except FetchError:
            ctx.bump("docs_failed")
            continue
        ctx.index(url, info["text"], url=page.url, title=info["title"], authors=info["authors"],
                  published=info["published"], etag=page.etag, last_modified=page.last_modified)
    ctx.log(f"{fetched} halaman ditelusuri")
    ctx.state["last_run"] = _now_iso()


# ======================= RSS / ATOM =======================
def crawl_rss(ctx: Ctx):
    page = ctx.fetcher.get(ctx.source["url"])
    root = etree.fromstring(page.data, XML_PARSER)
    if root is None:
        raise FetchError("Feed bukan XML yang valid")
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        if link:
            items.append((link, it.findtext("title"), it.findtext("pubDate")))
    for e in root.iterfind(".//atom:entry", NS):
        ln = e.find("atom:link[@rel='alternate']", NS)
        if ln is None:
            ln = e.find("atom:link", NS)
        if ln is not None and ln.get("href"):
            items.append((ln.get("href"), e.findtext("atom:title", namespaces=NS), e.findtext("atom:updated", namespaces=NS)))
    ctx.log(f"{len(items)} item di feed")
    for link, title, date in items:
        if ctx.done:
            break
        if not ctx.url_ok(link):
            continue
        ctx.bump("docs_seen")
        if store.get_document(ctx.source["id"], link):
            ctx.bump("docs_skipped")
            continue
        ctx.fetch_and_index(link, title=title, published=date)
    ctx.state["last_run"] = _now_iso()


CRAWLERS = {"oai_pmh": crawl_oai, "sitemap": crawl_sitemap, "web_crawl": crawl_web, "rss": crawl_rss}


def run_crawl(run: dict):
    source = store.get_source(run["source_id"])
    if not source:
        store.update_run(run["id"], status="failed", error="Sumber tidak ditemukan", finished_at=datetime.now(timezone.utc))
        return
    fn = CRAWLERS.get(source["kind"])
    ctx = Ctx(run, source)
    ctx.log(f"mulai crawl '{source['name']}' ({source['kind']}) — pemicu: {run['trigger']}")
    status, error = "done", None
    try:
        if fn is None:
            raise ValueError(f"Jenis sumber '{source['kind']}' tidak di-crawl")
        fn(ctx)
    except Exception as e:  # noqa: BLE001
        log.exception("crawl gagal")
        status, error = "failed", str(e)[:1000]
        ctx.log(f"GAGAL: {e}")
    finally:
        ctx.fetcher.close()
    ctx.log(f"selesai: {ctx.stats['docs_new']} baru, {ctx.stats['docs_updated']} diperbarui, "
            f"{ctx.stats['docs_skipped']} dilewati, {ctx.stats['docs_failed']} gagal")
    store.update_run(run["id"], status=status, error=error, finished_at=datetime.now(timezone.utc), **ctx.stats)
    store.finish_source(source["id"], status, ctx.state if status == "done" else None)
