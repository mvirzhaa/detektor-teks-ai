// Penyimpanan sampel latih
const db = require('./db');
const { normalizeText, countWords, textHash, detectLanguage } = require('./util');

/**
 * Simpan satu sampel. Bila teks sama sudah ada, labelnya diperbarui.
 * @returns {{id:number, status:'baru'|'label_diubah'|'sudah_ada', previousLabel:number|null, words:number}}
 */
async function upsertSample({ text, label, source, filename = null, note = null }, client = db) {
  const clean = normalizeText(text);
  const hash = textHash(clean);
  const words = countWords(clean);
  const existing = await client.query('SELECT id, label FROM samples WHERE text_hash = $1', [hash]);
  if (existing.rows.length) {
    const row = existing.rows[0];
    if (row.label === label) return { id: Number(row.id), status: 'sudah_ada', previousLabel: row.label, words };
    await client.query(
      'UPDATE samples SET label = $1, source = $2, note = COALESCE($3, note), learned_at = NULL, updated_at = now() WHERE id = $4',
      [label, source, note, row.id]
    );
    return { id: Number(row.id), status: 'label_diubah', previousLabel: row.label, words };
  }
  const r = await client.query(
    `INSERT INTO samples (text, text_hash, label, source, filename, language, word_count, note)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id`,
    [clean, hash, label, source, filename, detectLanguage(clean), words, note]
  );
  return { id: Number(r.rows[0].id), status: 'baru', previousLabel: null, words };
}

module.exports = { upsertSample };
