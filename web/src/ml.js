// Klien HTTP ke layanan ML Python
const config = require('./config');

class MlError extends Error {
  constructor(message, status = 502) {
    super(message);
    this.status = status;
  }
}

async function call(method, path, { json, form, timeoutMs = 120000 } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(config.mlUrl + path, {
      method,
      signal: ctrl.signal,
      headers: json ? { 'content-type': 'application/json' } : undefined,
      body: json ? JSON.stringify(json) : form,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body);
      throw new MlError(detail || `Layanan ML error ${res.status}`, res.status >= 500 ? 502 : res.status);
    }
    return body;
  } catch (err) {
    if (err instanceof MlError) throw err;
    if (err.name === 'AbortError') throw new MlError('Layanan ML tidak merespons (timeout).', 504);
    throw new MlError(`Layanan ML tidak dapat dihubungi: ${err.cause?.code || err.message}`, 503);
  } finally {
    clearTimeout(timer);
  }
}

module.exports = {
  MlError,
  health: () => call('GET', '/health', { timeoutMs: 5000 }),
  status: () => call('GET', '/model/status', { timeoutMs: 10000 }),
  predict: (text) => call('POST', '/predict', { json: { text, segments: true, explain: true } }),
  learn: (sampleIds) => call('POST', '/learn', { json: { sample_ids: sampleIds }, timeoutMs: 300000 }),
  trainFull: (reason) => call('POST', '/train/full', { json: { reason } }),
  versions: () => call('GET', '/model/versions'),
  activate: (id) => call('POST', `/model/versions/${encodeURIComponent(id)}/activate`),
  simIndex: (doc) => call('POST', '/sim/index', { json: doc }),
  extract: (buffer, filename, mimetype) => {
    const form = new FormData();
    form.append('file', new Blob([buffer], { type: mimetype || 'application/octet-stream' }), filename);
    return call('POST', '/extract', { form, timeoutMs: 120000 });
  },
};
