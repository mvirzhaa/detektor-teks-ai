const crypto = require('crypto');
const config = require('./config');

const WORD_RE = /[\p{L}\p{N}]+(?:[-'][\p{L}\p{N}]+)*/gu;

function normalizeText(text) {
  return String(text || '')
    .replace(/\r\n?/g, '\n')
    .replace(/ /g, ' ')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

const countWords = (text) => (String(text).match(WORD_RE) || []).length;

// Harus sama dengan app/cli.py:text_hash agar duplikat terdeteksi lintas jalur
const textHash = (text) =>
  crypto.createHash('sha256').update(text.toLowerCase().replace(/\s+/g, ' ').trim(), 'utf8').digest('hex');

function detectLanguage(text) {
  const w = (text.slice(0, 3000).toLowerCase().match(WORD_RE) || []);
  const id = new Set(['yang', 'dan', 'di', 'ini', 'itu', 'dengan', 'untuk', 'tidak', 'adalah', 'dari']);
  const en = new Set(['the', 'and', 'of', 'to', 'is', 'in', 'that', 'it', 'for', 'with']);
  let a = 0, b = 0;
  for (const x of w) { if (id.has(x)) a++; if (en.has(x)) b++; }
  if (!a && !b) return 'lain';
  return a >= b ? 'id' : 'en';
}

const AI = new Set(['1', 'ai', 'a.i.', 'generated', 'machine', 'gpt', 'llm', 'bot', 'true', 'ya', 'buatan ai', 'mesin']);
const HUMAN = new Set(['0', 'human', 'manusia', 'human-written', 'orang', 'false', 'tidak', 'asli', 'buatan manusia']);
function parseLabel(v) {
  if (v === 0 || v === 1) return v;
  const s = String(v ?? '').trim().toLowerCase();
  if (AI.has(s)) return 1;
  if (HUMAN.has(s)) return 0;
  const f = parseFloat(s);
  return Number.isFinite(f) ? (f >= 0.5 ? 1 : 0) : null;
}

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function requireLearnToken(req, _res, next) {
  if (!config.learnToken) return next();
  const given = Buffer.from(String(req.get('x-learn-token') || ''));
  const want = Buffer.from(config.learnToken);
  if (given.length === want.length && crypto.timingSafeEqual(given, want)) return next();
  next(new HttpError(401, 'Token belajar salah atau belum diisi. Atur di menu Pengaturan (ikon gembok).'));
}

const wrap = (fn) => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);

module.exports = { normalizeText, countWords, textHash, detectLanguage, parseLabel, HttpError, requireLearnToken, wrap };
