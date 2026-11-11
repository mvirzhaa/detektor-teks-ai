// MODE PENGECEKAN: cek teks / dokumen, simpan riwayat, umpan balik
const express = require('express');
const multer = require('multer');
const config = require('../config');
const db = require('../db');
const ml = require('../ml');
const { upsertSample } = require('../samples');
const { normalizeText, countWords, parseLabel, HttpError, requireLearnToken, wrap } = require('../util');

const router = express.Router();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: config.maxUploadMb * 1024 * 1024, files: 1 } });

router.post('/check', upload.single('file'), wrap(async (req, res) => {
  let text = req.body.text || '';
  let filename = null;
  let extractInfo = null;
  if (req.file) {
    filename = Buffer.from(req.file.originalname, 'latin1').toString('utf8');
    extractInfo = await ml.extract(req.file.buffer, filename, req.file.mimetype);
    text = extractInfo.text;
  }
  text = normalizeText(text);
  const words = countWords(text);
  if (!text) throw new HttpError(400, 'Masukkan teks atau unggah dokumen terlebih dahulu.');
  if (words < config.minWordsCheck) throw new HttpError(400, `Teks terlalu pendek (${words} kata). Minimal ${config.minWordsCheck} kata.`);

  const result = await ml.predict(text);
  const { text: analyzedText, ...stored } = result;
  const r = await db.query(
    `INSERT INTO checks (input_type, filename, text, word_count, ai_probability, verdict, verdict_label, mode, model_version_id, result)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id, created_at`,
    [filename ? 'file' : 'text', filename, config.storeCheckText ? analyzedText : analyzedText.slice(0, 500),
      words, result.ai_probability, result.verdict, result.verdict_label, result.mode,
      result.model_version_id, stored]
  );
  res.json({ id: Number(r.rows[0].id), created_at: r.rows[0].created_at, filename,
    pages: extractInfo?.pages ?? null, ...result });
}));

// Umpan balik: pengguna mengonfirmasi/mengoreksi hasil → menjadi data latih
router.post('/check/:id/feedback', express.json(), requireLearnToken, wrap(async (req, res) => {
  const label = parseLabel(req.body.label);
  if (label === null) throw new HttpError(400, 'Label harus "ai" atau "manusia".');
  const c = await db.query('SELECT id, text, filename, ai_probability FROM checks WHERE id = $1', [req.params.id]);
  if (!c.rows.length) throw new HttpError(404, 'Riwayat pengecekan tidak ditemukan.');
  const check = c.rows[0];
  if (!config.storeCheckText) throw new HttpError(400, 'Teks lengkap tidak disimpan (STORE_CHECK_TEXT=false).');
  const s = await upsertSample({ text: check.text, label, source: 'feedback', filename: check.filename,
    note: `Umpan balik dari pengecekan #${check.id}` });
  await db.query('UPDATE checks SET feedback_label = $1, feedback_sample_id = $2 WHERE id = $3', [label, s.id, check.id]);
  const learn = s.status === 'sudah_ada' ? null : await ml.learn([s.id]);
  res.json({ sample: s, learn });
}));

router.get('/history', wrap(async (req, res) => {
  const limit = Math.min(parseInt(req.query.limit, 10) || 20, 100);
  const page = Math.max(parseInt(req.query.page, 10) || 1, 1);
  const [rows, total] = await Promise.all([
    db.query(
      `SELECT id, input_type, filename, left(text, 220) AS excerpt, word_count, ai_probability, verdict, verdict_label,
              mode, model_version_id, feedback_label, created_at
       FROM checks ORDER BY id DESC LIMIT $1 OFFSET $2`, [limit, (page - 1) * limit]),
    db.query('SELECT count(*)::int AS n FROM checks'),
  ]);
  res.json({ items: rows.rows, total: total.rows[0].n, page, limit });
}));

router.get('/history/:id', wrap(async (req, res) => {
  const r = await db.query('SELECT * FROM checks WHERE id = $1', [req.params.id]);
  if (!r.rows.length) throw new HttpError(404, 'Riwayat tidak ditemukan.');
  const row = r.rows[0];
  res.json({ id: Number(row.id), created_at: row.created_at, filename: row.filename,
    feedback_label: row.feedback_label, ...row.result, text: row.text });
}));

router.delete('/history/:id', requireLearnToken, wrap(async (req, res) => {
  await db.query('DELETE FROM checks WHERE id = $1', [req.params.id]);
  res.json({ ok: true });
}));

module.exports = router;
