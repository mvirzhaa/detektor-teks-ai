"""Konfigurasi modul Cek Kemiripan (semua dapat diatur lewat variabel lingkungan)."""
import os


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _bool(name, default=False):
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "ya", "on")


# --- Fingerprint ---
SHINGLE_K = _int("SIM_SHINGLE_K", 5)          # panjang shingle (kata)
WINNOW_W = _int("SIM_WINNOW_W", 4)            # jendela winnowing → salinan ≥ K+W-1 kata dijamin tertangkap
MIN_MATCH_WORDS = _int("SIM_MIN_MATCH_WORDS", 8)
MAX_CANDIDATES = _int("SIM_MAX_CANDIDATES", 80)

# --- Pencarian web ---
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "none").strip().lower()  # brave | serpapi | google_cse | searxng | none
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
GOOGLE_CSE_KEY = os.getenv("GOOGLE_CSE_KEY", "")
GOOGLE_CSE_CX = os.getenv("GOOGLE_CSE_CX", "")
SEARXNG_URL = os.getenv("SEARXNG_URL", "").rstrip("/")
SEARCH_LANG = os.getenv("SEARCH_LANG", "id")
SEARCH_MAX_QUERIES = _int("SEARCH_MAX_QUERIES", 10)   # kueri per dokumen (biaya API!)
SEARCH_RESULTS_PER_QUERY = _int("SEARCH_RESULTS_PER_QUERY", 5)
WEB_MAX_FETCH = _int("WEB_MAX_FETCH", 15)              # halaman hasil pencarian yang diunduh per dokumen
WEB_CACHE_DAYS = _int("WEB_CACHE_DAYS", 30)            # halaman web hasil pencarian disimpan sekian hari

# --- Crawler ---
USER_AGENT = os.getenv("CRAWL_USER_AGENT", "DetektorKemiripanBot/1.0 (+crawler repositori kampus)")
CRAWL_DELAY_SEC = _float("CRAWL_DELAY_SEC", 1.0)       # jeda minimum antarpermintaan ke host yang sama
FETCH_TIMEOUT_SEC = _float("FETCH_TIMEOUT_SEC", 25)
MAX_FETCH_MB = _int("MAX_FETCH_MB", 20)
MAX_DOC_CHARS = _int("MAX_DOC_CHARS", 400_000)
MIN_DOC_WORDS = _int("MIN_DOC_WORDS", 40)
CRAWL_MAX_DOCS = _int("CRAWL_MAX_DOCS", 500)           # batas dokumen per sumber per malam (default)
# Izinkan crawl ke IP privat/intranet (mis. repositori di jaringan kampus). Hasil pencarian web SELALU diblokir
# ke IP privat kecuali SIM_ALLOW_PRIVATE_FETCH=true (hanya untuk pengujian).
CRAWL_ALLOW_PRIVATE = _bool("CRAWL_ALLOW_PRIVATE", False)
ALLOW_PRIVATE_FETCH = _bool("SIM_ALLOW_PRIVATE_FETCH", False)

# --- Jadwal ---
CRAWL_TIME = os.getenv("CRAWL_TIME", "01:00")          # jam crawl harian (zona waktu di bawah)
CRAWL_TZ = os.getenv("CRAWL_TZ", "Asia/Jakarta")
CRAWL_WEEKLY_DAY = _int("CRAWL_WEEKLY_DAY", 6)         # 0=Senin … 6=Minggu, untuk sumber berjadwal mingguan
WORKER_POLL_SEC = _float("WORKER_POLL_SEC", 3)
