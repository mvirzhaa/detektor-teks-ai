const fs = require('fs');
const path = require('path');
const express = require('express');
const helmet = require('helmet');
const morgan = require('morgan');
const rateLimit = require('express-rate-limit');
const multer = require('multer');
const config = require('./config');
const db = require('./db');
const ml = require('./ml');
const { wrap } = require('./util');

const app = express();
app.disable('x-powered-by');
app.set('trust proxy', 1);
app.use(helmet({
  contentSecurityPolicy: { directives: { 'default-src': ["'self'"], 'script-src': ["'self'"], 'style-src': ["'self'"], 'img-src': ["'self'", 'data:'] } },
}));
app.use(morgan(process.env.NODE_ENV === 'production' ? 'combined' : 'dev'));
app.use('/api', rateLimit({ windowMs: 60_000, limit: 120, standardHeaders: 'draft-8', legacyHeaders: false,
  message: { error: 'Terlalu banyak permintaan, coba lagi sebentar.' } }));
app.use(express.json({ limit: '2mb' }));
app.use(express.urlencoded({ extended: false, limit: '2mb' }));

app.get('/api/health', wrap(async (_req, res) => {
  const out = { web: true, db: false, ml: false };
  try { await db.query('SELECT 1'); out.db = true; } catch { /* db mati */ }
  try { out.ml = (await ml.health()).ok; } catch { /* ml mati */ }
  res.status(out.db && out.ml ? 200 : 503).json(out);
}));

app.get('/api/stats', wrap(async (_req, res) => {
  const [samples, bySource, checks] = await Promise.all([
    db.query('SELECT label, count(*)::int AS n FROM samples GROUP BY label'),
    db.query('SELECT source, count(*)::int AS n FROM samples GROUP BY source ORDER BY n DESC'),
    db.query(`SELECT count(*)::int AS total,
                     count(*) FILTER (WHERE created_at > now() - interval '7 days')::int AS minggu_ini,
                     count(*) FILTER (WHERE feedback_label IS NOT NULL)::int AS dengan_umpan_balik
              FROM checks`),
  ]);
  const c = { 0: 0, 1: 0 };
  samples.rows.forEach((r) => { c[r.label] = r.n; });
  res.json({
    samples: { human: c[0], ai: c[1], total: c[0] + c[1], by_source: bySource.rows },
    checks: checks.rows[0],
    learn_token_required: Boolean(config.learnToken),
  });
}));

app.use('/api', require('./routes/check'));
app.use('/api', require('./routes/learn'));
app.use('/api/sim', require('./modules/similarity/routes'));
app.use('/api', (_req, res) => res.status(404).json({ error: 'Endpoint tidak ditemukan.' }));

const indexHtmlTemplate = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const kemiripanHtmlTemplate = fs.readFileSync(path.join(__dirname, '..', 'public', 'kemiripan.html'), 'utf8');

function sendHtmlWithBase(res, template, req) {
  const prefix = req.headers['x-forwarded-prefix'] || config.basePath || '';
  const baseHref = prefix ? `/${prefix.replace(/^\/+|\/+$/g, '')}/` : '/';
  const html = template.replace(/<base href="[^"]*">/, `<base href="${baseHref}">`);
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.send(html);
}

app.use(express.static(path.join(__dirname, '..', 'public'), { maxAge: '1h', index: false }));
app.get(['/kemiripan', '/kemiripan/*'], (req, res) => sendHtmlWithBase(res, kemiripanHtmlTemplate, req));
app.get('*', (req, res) => sendHtmlWithBase(res, indexHtmlTemplate, req));

// eslint-disable-next-line no-unused-vars
app.use((err, _req, res, _next) => {
  let status = err.status || 500;
  let message = err.message || 'Terjadi kesalahan.';
  if (err instanceof multer.MulterError) {
    status = 400;
    message = err.code === 'LIMIT_FILE_SIZE' ? `File terlalu besar (maks ${config.maxUploadMb} MB).` : message;
  }
  if (status >= 500) console.error(err);
  res.status(status).json({ error: status >= 500 && !err.status ? 'Terjadi kesalahan pada server.' : message });
});

(async () => {
  await db.migrate();
  app.listen(config.port, () => console.log(`Detektor Teks AI berjalan di http://localhost:${config.port}`));
})().catch((err) => {
  console.error('Gagal memulai server:', err);
  process.exit(1);
});
