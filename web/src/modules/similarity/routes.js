// MODUL CEK KEMIRIPAN (ala Turnitin): pemeriksaan, laporan, sumber data & crawling
const crypto = require('crypto');
const express = require('express');
const multer = require('multer');
const config = require('../../config');
const db = require('../../db');
const ml = require('../../ml');
const { normalizeText, countWords, HttpError, requireLearnToken, wrap } = require('../../util');

const router = express.Router();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: config.maxUploadMb * 1024 * 1024, files: 30 } });
const fixName = (n) => Buffer.from(n, 'latin1').toString('utf8');

const KINDS = ['oai_pmh', 'sitemap', 'web_crawl', 'rss'];
const CATEGORIES = ['internet', 'publication', 'submission'];
const SCHEDULES = ['nightly', 'weekly', 'manual'];
const MIN_WORDS = 30;

const bool = (v, d) => (v === undefined || v === '' ? d : ['1', 'true', 'on', 'ya', true].includes(v));
const int = (v, d, lo, hi) => {
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : d;
};

function parseOptions(src = {}) {
  let cats = src.categories;
  if (typeof cats === 'string') cats = cats.split(',').map((s) => s.trim()).filter(Boolean);
  if (!Array.isArray(cats) || !cats.length) cats = CATEGORIES;
  cats = cats.filter((c) => CATEGORIES.includes(c));
  return {
    web: bool(src.web, true),
    repository: bool(src.repository, true),
    exclude_quotes: bool(src.exclude_quotes, false),
    exclude_bibliography: bool(src.exclude_bibliography, true),
    min_words: int(src.min_words, 8, 4, 50),
    categories: cats.length ? cats : CATEGORIES,
    save_to_repository: bool(src.save_to_repository, false),
  };
}

// Simpan ke repositori tugas = mengubah data → butuh token bila diatur
function tokenIfSaving(req, res, next) {
  if (bool(req.body?.save_to_repository, false)) return requireLearnToken(req, res, next);
  return next();
}

// ---------------- Ringkasan ----------------
router.get('/overview', wrap(async (_req, res) => {
  const [docs, checks, worker, runs, fp] = await Promise.all([
    db.query(`SELECT s.category, count(d.id)::int AS n, coalesce(sum(d.word_count),0)::bigint AS words
              FROM sim_sources s LEFT JOIN sim_documents d ON d.source_id = s.id GROUP BY s.category`),
    db.query(`SELECT count(*)::int AS total, count(*) FILTER (WHERE status IN ('queued','running'))::int AS antre,
                     round(avg(similarity) FILTER (WHERE status='done')::numeric, 1) AS rata
              FROM sim_checks`),
    db.query(`SELECT value, updated_at FROM sim_settings WHERE key = 'worker'`),
    db.query(`SELECT r.id, r.status, r.trigger, r.docs_new, r.docs_updated, r.docs_failed, r.created_at, r.finished_at,
                     s.name AS source_name FROM sim_crawl_runs r JOIN sim_sources s ON s.id = r.source_id
              ORDER BY r.id DESC LIMIT 8`),
    db.query(`SELECT reltuples::bigint AS n FROM pg_class WHERE relname = 'sim_fingerprints'`),
  ]);
  const w = worker.rows[0];
  const alive = w && Date.now() - new Date(w.value.heartbeat).getTime() < 120000;
  const cat = Object.fromEntries(CATEGORIES.map((c) => [c, { docs: 0, words: 0 }]));
  docs.rows.forEach((r) => { cat[r.category] = { docs: r.n, words: Number(r.words) }; });
  const last = await db.query(`SELECT value FROM sim_settings WHERE key = 'last_scheduled_crawl'`);
  res.json({
    documents: cat,
    fingerprints: Math.max(0, Number(fp.rows[0]?.n || 0)),
    checks: checks.rows[0],
    worker: w ? { ...w.value, alive } : { alive: false },
    last_scheduled_crawl: last.rows[0]?.value || null,
    recent_runs: runs.rows,
    token_required: Boolean(config.learnToken),
  });
}));

// ---------------- Pemeriksaan ----------------
router.post('/checks', upload.single('file'), tokenIfSaving, wrap(async (req, res) => {
  let text = req.body.text || '';
  let filename = null;
  if (req.file) {
    filename = fixName(req.file.originalname);
    text = (await ml.extract(req.file.buffer, filename, req.file.mimetype)).text;
  }
  text = normalizeText(text);
  const words = countWords(text);
  if (!text) throw new HttpError(400, 'Tempel teks atau unggah dokumen terlebih dahulu.');
  if (words < MIN_WORDS) throw new HttpError(400, `Teks terlalu pendek (${words} kata). Minimal ${MIN_WORDS} kata.`);
  const firstLine = text.split('\n')[0];
  const autoTitle = firstLine.length <= 90 ? firstLine : `${firstLine.slice(0, 90).replace(/\s+\S*$/, '')}…`;
  const title = (req.body.title || '').trim() || (filename ? filename.replace(/\.[^.]+$/, '') : autoTitle);
  const r = await db.query(
    `INSERT INTO sim_checks (title, author, filename, text, word_count, options, stage)
     VALUES ($1,$2,$3,$4,$5,$6,'menunggu antrean') RETURNING id, created_at`,
    [title.slice(0, 300), (req.body.author || '').trim().slice(0, 200) || null, filename, text, words, parseOptions(req.body)]
  );
  res.status(201).json({ id: Number(r.rows[0].id), title, words, status: 'queued' });
}));

router.get('/checks', wrap(async (req, res) => {
  const limit = int(req.query.limit, 20, 1, 100);
  const page = int(req.query.page, 1, 1, 100000);
  const params = [];
  let where = '';
  if (req.query.q) { params.push(`%${req.query.q}%`); where = `WHERE title ILIKE $1 OR author ILIKE $1 OR filename ILIKE $1`; }
  const [rows, total] = await Promise.all([
    db.query(`SELECT id, title, author, filename, word_count, status, stage, progress, similarity, internet, publication,
                     submission, error, (submission_doc_id IS NOT NULL) AS in_repository, created_at, finished_at
              FROM sim_checks ${where} ORDER BY id DESC LIMIT ${limit} OFFSET ${(page - 1) * limit}`, params),
    db.query(`SELECT count(*)::int AS n FROM sim_checks ${where}`, params),
  ]);
  res.json({ items: rows.rows, total: total.rows[0].n, page, limit });
}));

router.get('/checks/:id/status', wrap(async (req, res) => {
  const r = await db.query('SELECT id, status, stage, progress, similarity, error FROM sim_checks WHERE id = $1', [req.params.id]);
  if (!r.rows.length) throw new HttpError(404, 'Pemeriksaan tidak ditemukan.');
  res.json(r.rows[0]);
}));

router.get('/checks/:id', wrap(async (req, res) => {
  const r = await db.query('SELECT * FROM sim_checks WHERE id = $1', [req.params.id]);
  if (!r.rows.length) throw new HttpError(404, 'Pemeriksaan tidak ditemukan.');
  const c = r.rows[0];
  res.json({ ...c, id: Number(c.id), in_repository: Boolean(c.submission_doc_id) });
}));

router.post('/checks/:id/rerun', express.json(), wrap(async (req, res) => {
  const cur = await db.query('SELECT status, options FROM sim_checks WHERE id = $1', [req.params.id]);
  if (!cur.rows.length) throw new HttpError(404, 'Pemeriksaan tidak ditemukan.');
  if (['queued', 'running'].includes(cur.rows[0].status)) throw new HttpError(409, 'Pemeriksaan masih berjalan.');
  const opts = { ...cur.rows[0].options, ...parseOptions({ ...cur.rows[0].options, ...req.body }) };
  opts.save_to_repository = false;
  opts.reuse_web = req.body.search_again ? false : true; // pakai ulang hasil pencarian web (hemat kuota API)
  await db.query(`UPDATE sim_checks SET status='queued', stage='menunggu antrean', progress=0, error=NULL, options=$1
                  WHERE id = $2`, [opts, req.params.id]);
  res.json({ ok: true });
}));

router.delete('/checks/:id', requireLearnToken, wrap(async (req, res) => {
  const r = await db.query('DELETE FROM sim_checks WHERE id = $1 RETURNING submission_doc_id', [req.params.id]);
  if (r.rows[0]?.submission_doc_id) await db.query('DELETE FROM sim_documents WHERE id = $1', [r.rows[0].submission_doc_id]);
  res.json({ ok: true });
}));

// ---------------- Sumber data ----------------
function validateSource(b, partial = false) {
  const out = {};
  if (!partial || b.name !== undefined) {
    if (!String(b.name || '').trim()) throw new HttpError(400, 'Nama sumber wajib diisi.');
    out.name = String(b.name).trim().slice(0, 200);
  }
  if (!partial || b.kind !== undefined) {
    if (!KINDS.includes(b.kind)) throw new HttpError(400, `Jenis sumber harus salah satu: ${KINDS.join(', ')}`);
    out.kind = b.kind;
  }
  if (!partial || b.url !== undefined) {
    let u;
    try { u = new URL(String(b.url || '')); } catch { throw new HttpError(400, 'URL tidak valid.'); }
    if (!['http:', 'https:'].includes(u.protocol)) throw new HttpError(400, 'URL harus http/https.');
    out.url = u.toString();
  }
  if (b.category !== undefined || !partial) {
    const c = b.category || (out.kind === 'oai_pmh' ? 'publication' : 'internet');
    if (!CATEGORIES.includes(c)) throw new HttpError(400, 'Kategori tidak valid.');
    out.category = c;
  }
  if (b.schedule !== undefined || !partial) {
    const s = b.schedule || 'nightly';
    if (!SCHEDULES.includes(s)) throw new HttpError(400, 'Jadwal tidak valid.');
    out.schedule = s;
  }
  if (b.enabled !== undefined) out.enabled = bool(b.enabled, true);
  if (b.config !== undefined) {
    let cfg = b.config;
    if (typeof cfg === 'string') {
      try { cfg = cfg.trim() ? JSON.parse(cfg) : {}; } catch { throw new HttpError(400, 'Konfigurasi harus JSON yang valid.'); }
    }
    if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)) throw new HttpError(400, 'Konfigurasi harus objek JSON.');
    for (const k of ['include', 'exclude']) {
      if (cfg[k]) { try { new RegExp(cfg[k]); } catch { throw new HttpError(400, `Regex ${k} tidak valid.`); } }
    }
    out.config = cfg;
  }
  return out;
}

router.get('/sources', wrap(async (_req, res) => {
  const r = await db.query(`
    SELECT s.*, coalesce(d.n, 0)::int AS documents, coalesce(d.words, 0)::bigint AS words, lr.id AS last_run_id,
           lr.status AS last_run_status, lr.docs_new AS last_run_new, lr.docs_updated AS last_run_updated,
           lr.docs_failed AS last_run_failed, lr.finished_at AS last_run_finished
    FROM sim_sources s
    LEFT JOIN (SELECT source_id, count(*) AS n, sum(word_count) AS words FROM sim_documents GROUP BY source_id) d ON d.source_id = s.id
    LEFT JOIN LATERAL (SELECT * FROM sim_crawl_runs r WHERE r.source_id = s.id ORDER BY r.id DESC LIMIT 1) lr ON true
    ORDER BY s.system DESC, s.id`);
  res.json({ items: r.rows.map((x) => ({ ...x, words: Number(x.words) })) });
}));

router.post('/sources', express.json(), requireLearnToken, wrap(async (req, res) => {
  const v = validateSource(req.body);
  const r = await db.query(
    `INSERT INTO sim_sources (name, kind, category, url, config, schedule, enabled) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id`,
    [v.name, v.kind, v.category, v.url, v.config || {}, v.schedule, v.enabled ?? true]);
  const id = r.rows[0].id;
  if (bool(req.body.crawl_now, true)) await db.query(`INSERT INTO sim_crawl_runs (source_id, trigger) VALUES ($1,'manual')`, [id]);
  res.status(201).json({ id });
}));

router.patch('/sources/:id', express.json(), requireLearnToken, wrap(async (req, res) => {
  const s = await db.query('SELECT system FROM sim_sources WHERE id = $1', [req.params.id]);
  if (!s.rows.length) throw new HttpError(404, 'Sumber tidak ditemukan.');
  if (s.rows[0].system) throw new HttpError(400, 'Sumber bawaan sistem tidak dapat diubah.');
  const v = validateSource(req.body, true);
  if (req.body.reset_state) v.state = {};
  const keys = Object.keys(v);
  if (!keys.length) return res.json({ ok: true });
  await db.query(`UPDATE sim_sources SET ${keys.map((k, i) => `${k} = $${i + 1}`).join(', ')}, updated_at = now() WHERE id = $${keys.length + 1}`,
    [...keys.map((k) => v[k]), req.params.id]);
  res.json({ ok: true });
}));

router.delete('/sources/:id', requireLearnToken, wrap(async (req, res) => {
  const r = await db.query('DELETE FROM sim_sources WHERE id = $1 AND NOT system RETURNING id', [req.params.id]);
  if (!r.rows.length) throw new HttpError(400, 'Sumber tidak ditemukan atau merupakan sumber bawaan.');
  res.json({ ok: true });
}));

router.post('/sources/:id/crawl', requireLearnToken, wrap(async (req, res) => {
  const s = await db.query('SELECT kind, system FROM sim_sources WHERE id = $1', [req.params.id]);
  if (!s.rows.length) throw new HttpError(404, 'Sumber tidak ditemukan.');
  if (s.rows[0].system) throw new HttpError(400, 'Sumber bawaan tidak di-crawl.');
  const busy = await db.query(`SELECT id FROM sim_crawl_runs WHERE source_id = $1 AND status IN ('queued','running')`, [req.params.id]);
  if (busy.rows.length) throw new HttpError(409, 'Crawl untuk sumber ini sudah berjalan/antre.');
  const r = await db.query(`INSERT INTO sim_crawl_runs (source_id, trigger) VALUES ($1,'manual') RETURNING id`, [req.params.id]);
  res.status(201).json({ run_id: Number(r.rows[0].id) });
}));

router.get('/runs', wrap(async (req, res) => {
  const params = [];
  let where = '';
  if (req.query.source_id) { params.push(req.query.source_id); where = 'WHERE r.source_id = $1'; }
  const r = await db.query(`SELECT r.id, r.source_id, s.name AS source_name, r.trigger, r.status, r.docs_seen, r.docs_new,
      r.docs_updated, r.docs_skipped, r.docs_failed, r.error, r.created_at, r.started_at, r.finished_at
    FROM sim_crawl_runs r JOIN sim_sources s ON s.id = r.source_id ${where} ORDER BY r.id DESC LIMIT 50`, params);
  res.json({ items: r.rows });
}));

router.get('/runs/:id', wrap(async (req, res) => {
  const r = await db.query(`SELECT r.*, s.name AS source_name FROM sim_crawl_runs r JOIN sim_sources s ON s.id = r.source_id WHERE r.id = $1`, [req.params.id]);
  if (!r.rows.length) throw new HttpError(404, 'Riwayat crawl tidak ditemukan.');
  res.json(r.rows[0]);
}));

// ---------------- Dokumen terindeks ----------------
router.get('/documents', wrap(async (req, res) => {
  const limit = int(req.query.limit, 25, 1, 100);
  const page = int(req.query.page, 1, 1, 100000);
  const where = [], params = [];
  if (req.query.source_id) { params.push(req.query.source_id); where.push(`d.source_id = $${params.length}`); }
  if (req.query.q) { params.push(`%${req.query.q}%`); where.push(`(d.title ILIKE $${params.length} OR d.url ILIKE $${params.length} OR d.authors ILIKE $${params.length})`); }
  const w = where.length ? `WHERE ${where.join(' AND ')}` : '';
  const [rows, total] = await Promise.all([
    db.query(`SELECT d.id, d.title, d.url, d.authors, d.published, d.word_count, d.fetched_at, s.name AS source_name, s.category
              FROM sim_documents d JOIN sim_sources s ON s.id = d.source_id ${w}
              ORDER BY d.id DESC LIMIT ${limit} OFFSET ${(page - 1) * limit}`, params),
    db.query(`SELECT count(*)::int AS n FROM sim_documents d ${w}`, params),
  ]);
  res.json({ items: rows.rows, total: total.rows[0].n, page, limit });
}));

router.delete('/documents/:id', requireLearnToken, wrap(async (req, res) => {
  await db.query('DELETE FROM sim_documents WHERE id = $1', [req.params.id]);
  res.json({ ok: true });
}));

// Unggah dokumen ke repositori internal (mis. arsip jurnal/skripsi tanpa OAI-PMH)
router.post('/repository/upload', upload.array('files', 30), requireLearnToken, wrap(async (req, res) => {
  if (!req.files?.length) throw new HttpError(400, 'Pilih minimal satu dokumen.');
  const results = [];
  for (const f of req.files) {
    const filename = fixName(f.originalname);
    try {
      const ex = await ml.extract(f.buffer, filename, f.mimetype);
      const key = 'upload:' + crypto.createHash('sha256').update(ex.text).digest('hex').slice(0, 32);
      const out = await ml.simIndex({
        text: ex.text, doc_key: key, title: (req.files.length === 1 && req.body.title) || filename.replace(/\.[^.]+$/, ''),
        authors: req.body.authors || null, published: req.body.published || null,
      });
      results.push({ filename, ok: true, ...out });
    } catch (err) {
      results.push({ filename, ok: false, error: err.message });
    }
  }
  res.json({ results });
}));

module.exports = router;
