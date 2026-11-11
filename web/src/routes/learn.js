// MODE BELAJAR: tambah contoh berlabel (teks, dokumen, impor massal), kelola dataset & model
const express = require('express');
const multer = require('multer');
const { parse } = require('csv-parse/sync');
const config = require('../config');
const db = require('../db');
const ml = require('../ml');
const { upsertSample } = require('../samples');
const { normalizeText, countWords, parseLabel, HttpError, requireLearnToken, wrap } = require('../util');

const router = express.Router();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: config.maxUploadMb * 1024 * 1024, files: 20 } });
const uploadImport = multer({ storage: multer.memoryStorage(), limits: { fileSize: config.maxImportMb * 1024 * 1024, files: 1 } });

const fixName = (n) => Buffer.from(n, 'latin1').toString('utf8');

function requireLabel(v) {
  const label = parseLabel(v);
  if (label === null) throw new HttpError(400, 'Pilih label: "ai" atau "manusia".');
  return label;
}

async function learnIds(ids) {
  if (!ids.length) return null;
  return ml.learn(ids);
}

// Satu teks berlabel
router.post('/learn/text', express.json({ limit: '2mb' }), requireLearnToken, wrap(async (req, res) => {
  const label = requireLabel(req.body.label);
  const text = normalizeText(req.body.text);
  const words = countWords(text);
  if (words < config.minWordsLearn) throw new HttpError(400, `Teks terlalu pendek (${words} kata). Minimal ${config.minWordsLearn} kata agar bermakna untuk dipelajari.`);
  const s = await upsertSample({ text, label, source: 'manual', note: req.body.note || null });
  const learn = s.status === 'sudah_ada' ? null : await learnIds([s.id]);
  res.json({ sample: s, learn });
}));

// Satu atau beberapa dokumen dengan label yang sama
router.post('/learn/files', upload.array('files', 20), requireLearnToken, wrap(async (req, res) => {
  const label = requireLabel(req.body.label);
  if (!req.files?.length) throw new HttpError(400, 'Unggah minimal satu dokumen.');
  const results = [];
  const ids = [];
  for (const f of req.files) {
    const filename = fixName(f.originalname);
    try {
      const ex = await ml.extract(f.buffer, filename, f.mimetype);
      const words = countWords(ex.text);
      if (words < config.minWordsLearn) {
        results.push({ filename, ok: false, error: `Terlalu pendek (${words} kata)` });
        continue;
      }
      const s = await upsertSample({ text: ex.text, label, source: 'file', filename, note: req.body.note || null });
      if (s.status !== 'sudah_ada') ids.push(s.id);
      results.push({ filename, ok: true, ...s });
    } catch (err) {
      results.push({ filename, ok: false, error: err.message });
    }
  }
  const learn = await learnIds(ids);
  res.json({ results, learn });
}));

// Impor massal CSV / JSONL (kolom teks + label)
router.post('/learn/import', uploadImport.single('file'), requireLearnToken, wrap(async (req, res) => {
  if (!req.file) throw new HttpError(400, 'Unggah file CSV atau JSONL.');
  const name = fixName(req.file.originalname).toLowerCase();
  const raw = req.file.buffer.toString('utf8').replace(/^﻿/, '');
  let rows;
  try {
    if (name.endsWith('.jsonl') || name.endsWith('.ndjson')) {
      rows = raw.split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
    } else if (name.endsWith('.json')) {
      rows = JSON.parse(raw);
      if (!Array.isArray(rows)) throw new Error('JSON harus berupa array objek');
    } else {
      const first = raw.split('\n', 1)[0];
      const delimiter = [',', ';', '\t'].sort((a, b) => first.split(b).length - first.split(a).length)[0];
      rows = parse(raw, { columns: true, skip_empty_lines: true, delimiter, relax_quotes: true, trim: false });
    }
  } catch (err) {
    throw new HttpError(400, `File tidak dapat dibaca: ${err.message}`);
  }
  const textCol = (req.body.text_col || '').toLowerCase();
  const labelCol = (req.body.label_col || '').toLowerCase();
  const pick = (row, pref, cands) => {
    const keys = Object.fromEntries(Object.keys(row).map((k) => [k.toLowerCase().trim(), k]));
    for (const c of (pref ? [pref] : []).concat(cands)) if (keys[c] !== undefined) return row[keys[c]];
    return undefined;
  };

  const stats = { dibaca: rows.length, baru: 0, label_diubah: 0, sudah_ada: 0, dilewati: 0 };
  const ids = [];
  const client = await db.pool.connect();
  try {
    await client.query('BEGIN');
    for (const row of rows) {
      const text = normalizeText(pick(row, textCol, ['text', 'teks', 'content', 'isi', 'essay', 'body', 'document']));
      const label = parseLabel(pick(row, labelCol, ['label', 'kelas', 'generated', 'is_ai', 'class', 'target']));
      if (label === null || countWords(text) < config.minWordsLearn) { stats.dilewati++; continue; }
      const s = await upsertSample({ text, label, source: 'import', filename: name }, client);
      stats[s.status]++;
      if (s.status !== 'sudah_ada') ids.push(s.id);
    }
    await client.query('COMMIT');
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }

  let learn = null, retrain = null;
  if (ids.length >= config.bulkRetrainThreshold) {
    retrain = await ml.trainFull(`impor ${ids.length} sampel dari ${name}`);
  } else if (ids.length) {
    learn = { learned: 0 };
    for (let i = 0; i < ids.length; i += 50) {
      const out = await ml.learn(ids.slice(i, i + 50));
      learn.learned += out.learned;
      learn.status = out.status;
    }
  }
  res.json({ stats, learn, retrain });
}));

// ---------- Dataset ----------
router.get('/samples', wrap(async (req, res) => {
  const limit = Math.min(parseInt(req.query.limit, 10) || 25, 200);
  const page = Math.max(parseInt(req.query.page, 10) || 1, 1);
  const where = [], params = [];
  if (req.query.label === '0' || req.query.label === '1') { params.push(Number(req.query.label)); where.push(`label = $${params.length}`); }
  if (req.query.source) { params.push(req.query.source); where.push(`source = $${params.length}`); }
  if (req.query.q) { params.push(`%${req.query.q}%`); where.push(`(text ILIKE $${params.length} OR filename ILIKE $${params.length})`); }
  const w = where.length ? `WHERE ${where.join(' AND ')}` : '';
  const [rows, total] = await Promise.all([
    db.query(`SELECT id, left(text, 240) AS excerpt, label, source, filename, language, word_count, note, learned_at, created_at
              FROM samples ${w} ORDER BY id DESC LIMIT ${limit} OFFSET ${(page - 1) * limit}`, params),
    db.query(`SELECT count(*)::int AS n FROM samples ${w}`, params),
  ]);
  res.json({ items: rows.rows, total: total.rows[0].n, page, limit });
}));

router.get('/samples/export.csv', wrap(async (_req, res) => {
  const r = await db.query('SELECT id, label, source, language, word_count, created_at, text FROM samples ORDER BY id');
  const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  res.setHeader('content-type', 'text/csv; charset=utf-8');
  res.setHeader('content-disposition', 'attachment; filename="dataset-detektor.csv"');
  res.write('﻿id,label,source,language,word_count,created_at,text\n');
  for (const s of r.rows) {
    res.write([s.id, s.label === 1 ? 'ai' : 'manusia', s.source, s.language, s.word_count, s.created_at.toISOString(), s.text].map(esc).join(',') + '\n');
  }
  res.end();
}));

router.get('/samples/:id', wrap(async (req, res) => {
  const r = await db.query('SELECT * FROM samples WHERE id = $1', [req.params.id]);
  if (!r.rows.length) throw new HttpError(404, 'Sampel tidak ditemukan.');
  res.json(r.rows[0]);
}));

router.patch('/samples/:id', express.json(), requireLearnToken, wrap(async (req, res) => {
  const label = requireLabel(req.body.label);
  const r = await db.query('UPDATE samples SET label = $1, learned_at = NULL, updated_at = now() WHERE id = $2 RETURNING id', [label, req.params.id]);
  if (!r.rows.length) throw new HttpError(404, 'Sampel tidak ditemukan.');
  const learn = await ml.learn([Number(req.params.id)]);
  res.json({ ok: true, learn, hint: 'Label diubah dan dipelajari. Jalankan latih ulang penuh agar koreksi diterapkan sepenuhnya.' });
}));

router.delete('/samples/:id', requireLearnToken, wrap(async (req, res) => {
  await db.query('DELETE FROM samples WHERE id = $1', [req.params.id]);
  res.json({ ok: true, hint: 'Sampel dihapus. Pengaruhnya baru hilang dari model setelah latih ulang penuh.' });
}));

// ---------- Model ----------
router.get('/model/status', wrap(async (_req, res) => res.json(await ml.status())));
router.get('/model/versions', wrap(async (_req, res) => res.json(await ml.versions())));
router.post('/model/retrain', requireLearnToken, wrap(async (_req, res) => {
  const out = await ml.trainFull('manual dari dashboard');
  if (!out.started) throw new HttpError(409, 'Pelatihan lain sedang berjalan.');
  res.json(out);
}));
router.post('/model/versions/:id/activate', requireLearnToken, wrap(async (req, res) => res.json(await ml.activate(req.params.id))));

module.exports = router;
