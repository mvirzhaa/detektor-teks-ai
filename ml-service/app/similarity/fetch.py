"""
Pengunduh halaman yang sopan dan aman:
- mematuhi robots.txt (RFC 9309) dan jeda per host,
- menolak alamat IP privat/loopback (cegah SSRF) kecuali diizinkan,
- membatasi ukuran unduhan, lalu mengekstrak teks dari HTML/PDF/teks.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from ..extract import ExtractError, _decode, _pdf
from ..features import normalize_text
from . import config as C

log = logging.getLogger("sim.fetch")

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None


class FetchError(Exception):
    pass


class RobotsDisallowed(FetchError):
    pass


@dataclass
class Page:
    url: str
    status: int
    content_type: str
    data: bytes
    etag: str | None = None
    last_modified: str | None = None

    @property
    def is_pdf(self) -> bool:
        return "pdf" in self.content_type or self.data[:5] == b"%PDF-" or self.url.lower().endswith(".pdf")

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type or b"<html" in self.data[:4000].lower()

    def text_html(self) -> str:
        return _decode(self.data)


def _check_host(url: str, allow_private: bool):
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise FetchError(f"Skema URL tidak diizinkan: {p.scheme}")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as e:
        raise FetchError(f"Host tidak ditemukan: {p.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise FetchError(f"Alamat {p.hostname} ({ip}) adalah jaringan privat — diblokir.")


class Fetcher:
    def __init__(self, allow_private: bool = False, respect_robots: bool = True):
        self.allow_private = allow_private
        self.respect_robots = respect_robots
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.client = httpx.Client(
            headers={"User-Agent": C.USER_AGENT, "Accept-Language": "id,en;q=0.8"},
            follow_redirects=True, timeout=C.FETCH_TIMEOUT_SEC,
            event_hooks={"request": [lambda req: _check_host(str(req.url), self.allow_private)]},
        )

    def close(self):
        self.client.close()

    # ---------- robots & jeda ----------
    def _robot(self, url: str) -> RobotFileParser | None:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        with self._lock:
            if base in self._robots:
                return self._robots[base]
        rp = RobotFileParser()
        try:
            r = self.client.get(base + "/robots.txt", timeout=10)
            if r.status_code >= 500:
                rp.disallow_all = True            # RFC 9309: server error → anggap dilarang
            elif r.status_code >= 400:
                rp = None                         # tidak ada robots.txt → semua boleh
            else:
                rp.parse(r.text.splitlines())
        except Exception:  # noqa: BLE001
            rp = None
        with self._lock:
            self._robots[base] = rp
        return rp

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        rp = self._robot(url)
        return True if rp is None else rp.can_fetch(C.USER_AGENT, url)

    def sitemaps_from_robots(self, url: str) -> list[str]:
        rp = self._robot(url)
        return list(rp.site_maps() or []) if rp else []

    def _wait(self, url: str):
        host = urlparse(url).netloc
        delay = C.CRAWL_DELAY_SEC
        rp = self._robots.get(f"{urlparse(url).scheme}://{host}")
        if rp is not None:
            try:
                cd = rp.crawl_delay(C.USER_AGENT)
                if cd:
                    delay = max(delay, min(float(cd), 30.0))
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            wait = self._last.get(host, 0) + delay - time.monotonic()
            self._last[host] = time.monotonic() + max(0, wait)
        if wait > 0:
            time.sleep(wait)

    # ---------- unduh ----------
    def get(self, url: str, etag: str | None = None, last_modified: str | None = None,
            check_robots: bool = True, params: dict | None = None) -> Page:
        if check_robots and not self.allowed(url):
            raise RobotsDisallowed("Dilarang oleh robots.txt")
        self._wait(url)
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        limit = C.MAX_FETCH_MB * 1024 * 1024
        try:
            with self.client.stream("GET", url, headers=headers, params=params) as r:
                if r.status_code == 304:
                    return Page(str(r.url), 304, "", b"", etag, last_modified)
                if r.status_code >= 400:
                    raise FetchError(f"HTTP {r.status_code}")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > limit:
                        raise FetchError(f"Melebihi {C.MAX_FETCH_MB} MB")
                return Page(str(r.url), r.status_code, r.headers.get("content-type", "").lower(), bytes(buf),
                            r.headers.get("etag"), r.headers.get("last-modified"))
        except FetchError:
            raise
        except httpx.HTTPError as e:
            raise FetchError(f"{type(e).__name__}: {e}") from e


def extract_page(page: Page) -> dict:
    """Ambil teks utama + metadata dari halaman. Return {text, title, authors, published, pdf_url, links}."""
    if page.is_pdf:
        try:
            text, _ = _pdf(page.data)
        except ExtractError as e:
            raise FetchError(str(e)) from e
        return {"text": normalize_text(text), "title": None, "authors": None, "published": None, "pdf_url": None}
    if "html" not in page.content_type and b"<html" not in page.data[:4000].lower():
        return {"text": normalize_text(_decode(page.data)), "title": None, "authors": None, "published": None,
                "pdf_url": None}
    html = page.text_html()
    meta = _citation_meta(html, page.url)
    text, title, author, date = "", None, None, None
    if trafilatura is not None:
        text = trafilatura.extract(html, url=page.url, include_comments=False, include_tables=True,
                                   favor_precision=True) or ""
        try:
            md = trafilatura.extract_metadata(html, default_url=page.url)
            if md:
                title, author, date = md.title, md.author, md.date
        except Exception:  # noqa: BLE001
            pass
    if not text:
        from ..extract import _HTMLText
        p = _HTMLText()
        p.feed(html)
        text = "".join(p.parts)
    return {
        "text": normalize_text(text),
        "title": meta.get("citation_title") or title,
        "authors": meta.get("citation_author") or author,
        "published": meta.get("citation_publication_date") or meta.get("citation_date") or date,
        "pdf_url": meta.get("citation_pdf_url"),
    }


def _citation_meta(html: str, base: str) -> dict:
    """Meta tag Google Scholar (dipakai OJS, EPrints, DSpace): citation_pdf_url, citation_title, dst."""
    import re
    out: dict[str, str] = {}
    authors = []
    for m in re.finditer(r"<meta\s+[^>]*>", html[:200_000], re.IGNORECASE):
        tag = m.group(0)
        n = re.search(r'name\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        c = re.search(r'content\s*=\s*["\']([^"\']*)["\']', tag, re.I)
        if not n or not c:
            continue
        name, content = n.group(1).lower(), c.group(1).strip()
        if name == "citation_author":
            authors.append(content)
        elif name.startswith("citation_") and name not in out:
            out[name] = content
    if authors:
        out["citation_author"] = "; ".join(authors[:10])
    if "citation_pdf_url" in out:
        out["citation_pdf_url"] = urljoin(base, out["citation_pdf_url"])
    return out


def extract_links(html: str, base: str) -> list[str]:
    try:
        import lxml.html
        doc = lxml.html.fromstring(html)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for a in doc.iter("a"):
        href = a.get("href")
        if href and not href.startswith(("#", "mailto:", "javascript:", "tel:")):
            out.append(urljoin(base, href).split("#")[0])
    return out
