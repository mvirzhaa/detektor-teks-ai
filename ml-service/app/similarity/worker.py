"""
Worker modul Cek Kemiripan — proses terpisah dari layanan ML:

  python -m app.similarity.worker

Menjalankan tiga utas:
  • antrean pemeriksaan  (sim_checks berstatus queued)
  • antrean crawl        (sim_crawl_runs berstatus queued)
  • penjadwal            (setiap hari pukul CRAWL_TIME: crawl semua sumber berjadwal harian,
                          mingguan pada CRAWL_WEEKLY_DAY, dan bersihkan cache web lama)
"""
from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config as C
from . import search, store
from .crawlers import run_crawl
from .engine import run_check

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sim.worker")
STOP = threading.Event()


def check_loop():
    while not STOP.is_set():
        try:
            job = store.claim_check()
        except Exception as e:  # noqa: BLE001
            log.warning("DB belum siap: %s", e)
            STOP.wait(5)
            continue
        if not job:
            STOP.wait(C.WORKER_POLL_SEC)
            continue
        log.info("pemeriksaan #%s mulai", job["id"])
        try:
            r = run_check(job)
            log.info("pemeriksaan #%s selesai: %.1f%%", job["id"], r["similarity"])
        except Exception as e:  # noqa: BLE001
            log.exception("pemeriksaan #%s gagal", job["id"])
            store.update_check(job["id"], status="failed", error=str(e)[:1000], stage="gagal",
                               finished_at=datetime.now(timezone.utc))


def crawl_loop():
    while not STOP.is_set():
        try:
            run = store.claim_crawl()
        except Exception as e:  # noqa: BLE001
            log.warning("DB belum siap: %s", e)
            STOP.wait(5)
            continue
        if not run:
            STOP.wait(C.WORKER_POLL_SEC * 2)
            continue
        run_crawl(run)


def scheduler_tick(now: datetime | None = None) -> dict | None:
    """Dipanggil berkala. Mengantrekan crawl harian sekali per hari setelah CRAWL_TIME."""
    tz = ZoneInfo(C.CRAWL_TZ)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    hh, mm = (int(x) for x in C.CRAWL_TIME.split(":"))
    today = now.date().isoformat()
    if (now.hour, now.minute) < (hh, mm) or store.get_setting("last_scheduled_crawl") == today:
        return None
    schedules = ["nightly"] + (["weekly"] if now.weekday() == C.CRAWL_WEEKLY_DAY else [])
    n = store.enqueue_scheduled("nightly", schedules)
    purged = store.purge_web_cache(C.WEB_CACHE_DAYS)
    store.set_setting("last_scheduled_crawl", today)
    log.info("jadwal %s: %s crawl diantrekan, %s halaman cache web lama dihapus", today, n, purged)
    return {"queued": n, "purged": purged}


def scheduler_loop():
    while not STOP.is_set():
        try:
            scheduler_tick()
            store.set_setting("worker", {
                "heartbeat": datetime.now(timezone.utc).isoformat(), "search_provider": search.provider_name(),
                "search_ready": search.is_configured(), "crawl_time": C.CRAWL_TIME, "crawl_tz": C.CRAWL_TZ,
                "weekly_day": C.CRAWL_WEEKLY_DAY, "web_cache_days": C.WEB_CACHE_DAYS,
                "max_queries": C.SEARCH_MAX_QUERIES, "max_fetch": C.WEB_MAX_FETCH})
        except Exception as e:  # noqa: BLE001
            log.warning("penjadwal: %s", e)
        STOP.wait(30)


def main():
    for _ in range(30):
        try:
            store.system_source_id("web_search")
            store.recover_stale()
            break
        except Exception as e:  # noqa: BLE001
            log.info("menunggu tabel modul kemiripan dibuat oleh web… (%s)", e)
            time.sleep(3)
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    threads = [threading.Thread(target=f, name=f.__name__, daemon=True) for f in (check_loop, crawl_loop, scheduler_loop)]
    for t in threads:
        t.start()
    log.info("worker kemiripan berjalan — pencarian web: %s (%s), crawl harian %s %s",
             search.provider_name(), "siap" if search.is_configured() else "belum dikonfigurasi", C.CRAWL_TIME, C.CRAWL_TZ)
    while not STOP.is_set():
        STOP.wait(1)
    log.info("worker berhenti")


if __name__ == "__main__":
    main()
