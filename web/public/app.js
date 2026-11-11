/* Detektor Teks AI — antarmuka (vanilla JS, tanpa build) */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pct = (p) => (p == null ? '–' : `${Math.round(p * 100)}%`);
  const fmtDate = (d) => new Date(d).toLocaleString('id-ID', { dateStyle: 'medium', timeStyle: 'short' });
  const countWords = (t) => (t.match(/[\p{L}\p{N}]+(?:[-'][\p{L}\p{N}]+)*/gu) || []).length;
  const TOKEN_KEY = 'detektor.learnToken';
  const state = { checkMode: 'text', learnMode: 'text', learnLabel: null, current: null, dsPage: 1, histPage: 1, trainPoll: null };

  // Lebar elemen diatur lewat CSSOM (CSP memblokir atribut style inline)
  const applyWidths = (root) => {
    $$('[data-w]', root).forEach((el) => { el.style.width = `${el.dataset.w}%`; });
    $$('[data-left]', root).forEach((el) => { el.style.left = `${el.dataset.left}%`; });
  };

  function getToken() { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; } }
  function setToken(v) { try { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY); } catch { /* abaikan */ } }

  const baseHref = document.querySelector('base')?.getAttribute('href') || '/';
  const cleanBase = baseHref.replace(/\/+$/, '');

  async function api(path, opts = {}) {
    const headers = { ...(opts.headers || {}) };
    const tok = getToken();
    if (tok) headers['x-learn-token'] = tok;
    let body = opts.body;
    if (body && !(body instanceof FormData)) { headers['content-type'] = 'application/json'; body = JSON.stringify(body); }
    const url = path.startsWith('http') ? path : `${cleanBase}/${path.replace(/^\/+/, '')}`;
    const res = await fetch(url, { method: opts.method || (body ? 'POST' : 'GET'), headers, body });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || `Gagal (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  let toastTimer;
  function toast(msg, isError = false) {
    const t = $('#toast');
    t.textContent = msg;
    t.className = `toast show${isError ? ' error' : ''}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = 'toast'; }, isError ? 5200 : 3000);
  }

  async function busy(btn, label, fn) {
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>${esc(label)}`;
    try { return await fn(); } catch (e) {
      toast(e.message, true);
      if (e.status === 401) $('#settings').showModal();
    } finally { btn.disabled = false; btn.innerHTML = orig; }
  }

  // ---------- Tab ----------
  function showTab(name) {
    $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
    $$('.panel').forEach((p) => p.classList.toggle('active', p.id === `tab-${name}`));
    if (name === 'dataset') loadDataset();
    if (name === 'model') loadModel();
    if (name === 'history') loadHistory();
    try { history.replaceState(null, '', `#${name}`); } catch { /* abaikan */ }
  }
  $$('.tab').forEach((t) => t.addEventListener('click', () => showTab(t.dataset.tab)));
  $('#modelPill').addEventListener('click', () => showTab('model'));

  function bindSeg(segId, key, scope) {
    $$(`#${segId} button`).forEach((b) => b.addEventListener('click', () => {
      $$(`#${segId} button`).forEach((x) => x.classList.toggle('active', x === b));
      state[key] = b.dataset.mode;
      $$('.input-mode', scope).forEach((m) => m.classList.toggle('hidden', m.dataset.for !== b.dataset.mode));
      if (key === 'learnMode') $('#labelPick').classList.toggle('locked', b.dataset.mode === 'import');
    }));
  }
  bindSeg('checkInputSeg', 'checkMode', $('#tab-check .input-card'));
  bindSeg('learnSeg', 'learnMode', $('#tab-learn .card'));

  function bindFile(inputId, nameId) {
    const input = $(`#${inputId}`);
    const drop = input.closest('.drop');
    const show = () => { $(`#${nameId}`).textContent = [...input.files].map((f) => f.name).join(', '); };
    input.addEventListener('change', show);
    ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove('over')));
    drop.addEventListener('drop', (e) => { e.preventDefault(); input.files = e.dataTransfer.files; show(); });
  }
  bindFile('checkFile', 'checkFileName');
  bindFile('learnFiles', 'learnFilesName');
  bindFile('importFile', 'importFileName');

  const wc = (ta, out) => ta.addEventListener('input', () => { $(out).textContent = `${countWords(ta.value)} kata`; });
  wc($('#checkText'), '#checkWordCount');
  wc($('#learnText'), '#learnWordCount');
  $('#clearCheck').addEventListener('click', () => { $('#checkText').value = ''; $('#checkWordCount').textContent = '0 kata'; });

  // ---------- Status model (header) ----------
  async function refreshPill() {
    const pill = $('#modelPill');
    try {
      const s = await api('/api/model/status');
      pill.classList.toggle('ready', s.ready);
      pill.classList.remove('down');
      const n = (s.dataset?.human || 0) + (s.dataset?.ai || 0);
      $('#modelPillText').textContent = s.ready
        ? `Model v${s.version.id} · ${n} contoh`
        : `Heuristik · ${n} contoh`;
      return s;
    } catch {
      pill.classList.add('down');
      $('#modelPillText').textContent = 'Layanan ML tidak terhubung';
      return null;
    }
  }

  // ================= MODE PENGECEKAN =================
  $('#runCheck').addEventListener('click', (e) => busy(e.currentTarget, 'Menganalisis…', async () => {
    const fd = new FormData();
    if (state.checkMode === 'file') {
      const f = $('#checkFile').files[0];
      if (!f) throw new Error('Pilih dokumen terlebih dahulu.');
      fd.append('file', f);
    } else {
      fd.append('text', $('#checkText').value);
    }
    const r = await api('/api/check', { method: 'POST', body: fd });
    renderResult(r);
    refreshPill();
  }));

  function bucket(p) { return p < 0.2 ? 0 : p < 0.32 ? 1 : p < 0.44 ? 2 : p <= 0.56 ? 3 : p <= 0.68 ? 4 : p <= 0.8 ? 5 : 6; }

  function renderResult(r) {
    state.current = r;
    const p = r.ai_probability;
    const ex = r.explanation || { indicators: [], phrases_ai: [], phrases_human: [] };
    const maxC = Math.max(0.0001, ...ex.indicators.map((i) => Math.abs(i.contribution)));
    const fb = r.feedback_label;
    const html = `
      <div class="card-head"><h2>Hasil analisis</h2><span class="muted">#${r.id}${r.filename ? ` · ${esc(r.filename)}` : ''}</span></div>
      <div class="score"><span class="num">${Math.round(p * 100)}<small>%</small></span><span class="unit">probabilitas buatan AI</span></div>
      <div class="verdict ${esc(r.verdict)}">${esc(r.verdict_label)}</div>
      <div class="gauge"><span class="needle" data-left="${(p * 100).toFixed(1)}"></span></div>
      <div class="gauge-labels"><span>Manusia</span><span>Ragu</span><span>AI</span></div>
      <div class="chips">
        <span class="chip">${r.mode === 'model' ? `Model v${r.model_version_id}` : 'Mode heuristik'}</span>
        <span class="chip rel-${esc(r.reliability)}">Keandalan ${esc(r.reliability)}</span>
        <span class="chip">${r.stats.words} kata · ${r.stats.sentences} kalimat</span>
        ${r.segments.length ? `<span class="chip">${pct(r.ai_share)} teks ditandai AI</span>` : ''}
        ${r.pages ? `<span class="chip">${r.pages} halaman</span>` : ''}
      </div>
      ${r.warnings.map((w) => `<div class="warn">${esc(w)}</div>`).join('')}
      ${ex.indicators.length ? `<div class="sec-title">Indikator gaya bahasa</div>
        ${ex.indicators.slice(0, 8).map((i) => `
          <div class="ind"><div>${esc(i.label)} <small>${i.value}</small></div>
            <div class="ibar" title="${i.toward === 'ai' ? 'mendorong ke AI' : 'mendorong ke manusia'}"><b class="${i.toward}" data-w="${(Math.abs(i.contribution) / maxC * 50).toFixed(1)}"></b></div></div>`).join('')}` : ''}
      ${ex.phrases_ai.length ? `<div class="sec-title">Kata/frasa yang mengarah ke AI</div><div class="phr ai">${ex.phrases_ai.map((x) => `<span>${esc(x.phrase)}</span>`).join('')}</div>` : ''}
      ${ex.phrases_human.length ? `<div class="sec-title">Kata/frasa yang mengarah ke manusia</div><div class="phr human">${ex.phrases_human.map((x) => `<span>${esc(x.phrase)}</span>`).join('')}</div>` : ''}
      <div class="feedback" id="feedbackBox">
        ${fb != null ? `<p>Umpan balik tercatat: teks ini <b>${fb === 1 ? 'buatan AI' : 'buatan manusia'}</b>. Terima kasih, model sudah mempelajarinya.</p>` : `
        <p><b>Apakah hasil ini benar?</b> Bila Anda tahu asal teks ini, beri tahu model agar ia belajar.</p>
        <div class="acts">
          <button class="btn small ai-btn" data-fb="ai">Sebenarnya buatan AI</button>
          <button class="btn small human-btn" data-fb="manusia">Sebenarnya buatan manusia</button>
        </div>`}
      </div>`;
    const body = $('#resultBody');
    body.innerHTML = html;
    applyWidths(body);
    $('#resultEmpty').classList.add('hidden');
    body.classList.remove('hidden');
    $$('[data-fb]', body).forEach((b) => b.addEventListener('click', () => sendFeedback(r.id, b.dataset.fb, b)));
    renderHighlight(r);
  }

  function renderHighlight(r) {
    const card = $('#highlightCard');
    const text = r.text || '';
    if (!text) { card.classList.add('hidden'); return; }
    let out = '', pos = 0;
    const segs = r.segments.length ? r.segments : [{ start: 0, end: text.length, ai_probability: r.ai_probability }];
    for (const s of segs) {
      out += esc(text.slice(pos, s.start));
      out += `<span class="seg-h h${bucket(s.ai_probability)}" title="${pct(s.ai_probability)} AI">${esc(text.slice(s.start, s.end))}</span>`;
      pos = s.end;
    }
    out += esc(text.slice(pos));
    $('#highlightText').innerHTML = out;
    card.classList.remove('hidden');
  }

  async function sendFeedback(id, label, btn) {
    await busy(btn, 'Menyimpan…', async () => {
      const out = await api(`/api/check/${id}/feedback`, { method: 'POST', body: { label } });
      state.current.feedback_label = label === 'ai' ? 1 : 0;
      $('#feedbackBox').innerHTML = `<p>Terima kasih! Teks ini disimpan ke dataset sebagai <b>${label === 'ai' ? 'buatan AI' : 'buatan manusia'}</b>${out.learn ? ' dan langsung dipelajari model' : ' (sudah ada di dataset)'}.</p>`;
      toast('Umpan balik dipelajari model');
      refreshPill();
    });
  }

  // ================= MODE BELAJAR =================
  $$('#labelPick .lp').forEach((b) => b.addEventListener('click', () => {
    state.learnLabel = b.dataset.label;
    $$('#labelPick .lp').forEach((x) => x.classList.toggle('active', x === b));
  }));

  function learnSummary(out) {
    const lines = [];
    const res = out.learn?.results || [];
    for (const r of res) {
      if (r.predicted_ai_probability == null) continue;
      lines.push(`Sebelum belajar, model menebak <b>${pct(r.predicted_ai_probability)} AI</b> — ${r.correct ? '<span class="ok">tebakan benar</span>' : '<span class="bad">tebakan salah, kini dikoreksi</span>'}.`);
    }
    const st = out.learn?.status || out.status;
    if (st) {
      lines.push(`Model kini telah mempelajari <b>${st.seen.human}</b> contoh manusia &amp; <b>${st.seen.ai}</b> contoh AI.${st.ready ? '' : ` Model aktif setelah minimal ${st.min_samples_per_class} contoh per kelas.`}`);
      if (st.auto_retrain_every) lines.push(`Latih ulang penuh otomatis: ${st.since_full}/${st.auto_retrain_every} contoh baru.`);
    }
    if (out.learn?.auto_retrain_started || out.retrain?.started) lines.push('<b>Latih ulang penuh dimulai</b> di latar belakang — pantau di tab Model.');
    return lines;
  }

  $('#runLearn').addEventListener('click', (e) => busy(e.currentTarget, 'Belajar…', async () => {
    const box = $('#learnResult');
    let out, lines = [];
    if (state.learnMode !== 'import' && !state.learnLabel) throw new Error('Pilih label terlebih dahulu: Manusia atau AI.');
    if (state.learnMode === 'text') {
      out = await api('/api/learn/text', { method: 'POST', body: { text: $('#learnText').value, label: state.learnLabel, note: $('#learnNote').value } });
      const st = { baru: 'Contoh baru disimpan', label_diubah: 'Teks sudah ada — labelnya diperbarui', sudah_ada: 'Teks ini sudah ada di dataset dengan label yang sama' }[out.sample.status];
      lines.push(`<b>${st}</b> (#${out.sample.id}, ${out.sample.words} kata).`);
      if (out.sample.status !== 'sudah_ada') { $('#learnText').value = ''; $('#learnWordCount').textContent = '0 kata'; }
    } else if (state.learnMode === 'files') {
      const files = $('#learnFiles').files;
      if (!files.length) throw new Error('Pilih dokumen terlebih dahulu.');
      const fd = new FormData();
      [...files].forEach((f) => fd.append('files', f));
      fd.append('label', state.learnLabel);
      out = await api('/api/learn/files', { method: 'POST', body: fd });
      lines.push(`<b>${out.results.filter((r) => r.ok).length} dari ${out.results.length} dokumen</b> diproses.<ul>${out.results.map((r) => `<li>${esc(r.filename)}: ${r.ok ? esc(r.status.replace('_', ' ')) + ` (${r.words} kata)` : `<span class="bad">${esc(r.error)}</span>`}</li>`).join('')}</ul>`);
    } else {
      const f = $('#importFile').files[0];
      if (!f) throw new Error('Pilih file dataset terlebih dahulu.');
      const fd = new FormData();
      fd.append('file', f);
      fd.append('text_col', $('#importTextCol').value.trim());
      fd.append('label_col', $('#importLabelCol').value.trim());
      out = await api('/api/learn/import', { method: 'POST', body: fd });
      const s = out.stats;
      lines.push(`<b>Impor selesai.</b> Dibaca ${s.dibaca} baris: ${s.baru} baru, ${s.label_diubah} label diubah, ${s.sudah_ada} duplikat, ${s.dilewati} dilewati (label tidak dikenali / teks terlalu pendek).`);
    }
    lines = lines.concat(learnSummary(out));
    box.innerHTML = `<div class="learn-out">${lines.map((l) => `<div>${l}</div>`).join('')}</div>`;
    toast('Model telah belajar');
    refreshPill();
  }));

  // ================= DATASET =================
  let dsTimer;
  $('#dsSearch').addEventListener('input', () => { clearTimeout(dsTimer); dsTimer = setTimeout(() => { state.dsPage = 1; loadDataset(); }, 300); });
  $('#dsLabel').addEventListener('change', () => { state.dsPage = 1; loadDataset(); });
  $('#dsSource').addEventListener('change', () => { state.dsPage = 1; loadDataset(); });

  const SRC = { manual: 'Manual', file: 'Dokumen', import: 'Impor', feedback: 'Umpan balik', cli: 'CLI' };

  async function loadDataset() {
    try {
      const [stats, list] = await Promise.all([
        api('/api/stats'),
        api(`/api/samples?page=${state.dsPage}&limit=25&label=${$('#dsLabel').value}&source=${$('#dsSource').value}&q=${encodeURIComponent($('#dsSearch').value)}`),
      ]);
      const s = stats.samples;
      const ratio = s.total ? (s.human / s.total) * 100 : 50;
      const statsEl = $('#datasetStats');
      statsEl.innerHTML = `
        <div class="stat"><div class="k">Total contoh</div><div class="v">${s.total}</div></div>
        <div class="stat"><div class="k">Manusia / AI</div><div class="v">${s.human} <small>/</small> ${s.ai}</div><div class="balance"><b data-w="${ratio.toFixed(1)}"></b></div></div>
        <div class="stat"><div class="k">Pengecekan</div><div class="v">${stats.checks.total} <small>${stats.checks.minggu_ini} minggu ini</small></div></div>
        <div class="stat"><div class="k">Umpan balik</div><div class="v">${stats.checks.dengan_umpan_balik}</div></div>`;
      applyWidths(statsEl);
      const tbl = $('#dsTable');
      tbl.innerHTML = list.items.length ? `
        <thead><tr><th>#</th><th>Cuplikan</th><th>Label</th><th>Sumber</th><th>Kata</th><th>Ditambahkan</th><th></th></tr></thead>
        <tbody>${list.items.map((r) => `<tr>
          <td class="muted">${r.id}</td>
          <td class="ex">${esc(r.excerpt)}${r.excerpt.length >= 240 ? '…' : ''}${r.filename ? `<br><small class="muted">${esc(r.filename)}</small>` : ''}</td>
          <td><button class="badge ${r.label === 1 ? 'ai' : 'human'}" data-flip="${r.id}" data-label="${r.label}" title="Klik untuk mengubah label">${r.label === 1 ? 'AI' : 'MANUSIA'}</button></td>
          <td>${esc(SRC[r.source] || r.source)}</td>
          <td>${r.word_count}</td>
          <td class="muted">${fmtDate(r.created_at)}</td>
          <td><button class="btn small ghost" data-del="${r.id}">Hapus</button></td></tr>`).join('')}</tbody>`
        : '<tbody><tr><td class="muted">Belum ada data. Tambahkan contoh di Mode Belajar.</td></tr></tbody>';
      $$('[data-flip]', tbl).forEach((b) => b.addEventListener('click', () => busy(b, '', async () => {
        const to = b.dataset.label === '1' ? 'manusia' : 'ai';
        await api(`/api/samples/${b.dataset.flip}`, { method: 'PATCH', body: { label: to } });
        toast(`Label diubah menjadi ${to.toUpperCase()}`);
        loadDataset();
      })));
      $$('[data-del]', tbl).forEach((b) => b.addEventListener('click', () => busy(b, '', async () => {
        await api(`/api/samples/${b.dataset.del}`, { method: 'DELETE' });
        toast('Sampel dihapus. Latih ulang penuh untuk menghapus pengaruhnya dari model.');
        loadDataset();
      })));
      pager('#dsPager', list, (p) => { state.dsPage = p; loadDataset(); });
    } catch (e) { toast(e.message, true); }
  }

  function pager(sel, list, go) {
    const pages = Math.max(1, Math.ceil(list.total / list.limit));
    const el = $(sel);
    el.innerHTML = `<span>Halaman ${list.page} dari ${pages} · ${list.total} data</span>
      <button class="btn small" data-p="${list.page - 1}" ${list.page <= 1 ? 'disabled' : ''}>‹</button>
      <button class="btn small" data-p="${list.page + 1}" ${list.page >= pages ? 'disabled' : ''}>›</button>`;
    $$('[data-p]', el).forEach((b) => b.addEventListener('click', () => go(Number(b.dataset.p))));
  }

  // ================= MODEL =================
  async function loadModel() {
    let s, v;
    try { [s, v] = await Promise.all([api('/api/model/status'), api('/api/model/versions')]); } catch (e) { toast(e.message, true); return; }
    const m = s.version?.metrics;
    const pre = s.prequential;
    const cm = m?.confusion_matrix;
    const cards = $('#modelCards');
    cards.innerHTML = `
      <div class="card"><div class="mk">Mode aktif</div><div class="mv">${s.ready ? `Model v${s.version.id}` : 'Heuristik'}</div>
        <div class="md">${s.ready ? `${s.version.kind === 'full' ? 'Hasil latih ulang penuh' : 'Belajar incremental'} · ${s.version.updates} pembaruan sejak dibuat` : `Model aktif setelah ≥${s.min_samples_per_class} contoh per kelas.`}</div></div>
      <div class="card"><div class="mk">Contoh dipelajari</div><div class="mv">${s.seen.human + s.seen.ai}</div>
        <div class="md">${s.seen.human} manusia · ${s.seen.ai} AI · dataset ${s.dataset ? s.dataset.human + s.dataset.ai : '–'}</div></div>
      <div class="card"><div class="mk">Akurasi online</div><div class="mv">${pre.accuracy == null ? '–' : pct(pre.accuracy)}</div>
        <div class="md">${pre.n ? `${pre.correct} dari ${pre.n} contoh baru ditebak benar sebelum dipelajari` : 'Dihitung saat contoh baru masuk setelah model aktif.'}</div></div>
      <div class="card"><div class="mk">Latih ulang otomatis</div><div class="mv">${s.auto_retrain_every ? `${s.since_full}/${s.auto_retrain_every}` : 'Nonaktif'}</div>
        <div class="md">contoh baru sejak latih ulang penuh terakhir</div>
        ${s.auto_retrain_every ? `<div class="progress"><b data-w="${Math.min(100, (s.since_full / s.auto_retrain_every) * 100).toFixed(1)}"></b></div>` : ''}</div>
      ${m ? `<div class="card"><div class="mk">Evaluasi data uji (v${s.version.id})</div>
        <div class="metrics">
          <div><b>${pct(m.accuracy)}</b><span>Akurasi</span></div><div><b>${pct(m.precision_ai)}</b><span>Presisi AI</span></div>
          <div><b>${pct(m.recall_ai)}</b><span>Recall AI</span></div><div><b>${pct(m.f1_ai)}</b><span>F1 AI</span></div>
          <div><b>${m.roc_auc == null ? '–' : m.roc_auc.toFixed(3)}</b><span>ROC-AUC</span></div></div>
        <div class="md muted">${m.n_train} latih · ${m.n_test} uji · alpha ${m.alpha}</div></div>
      <div class="card"><div class="mk">Matriks kebingungan</div>
        <div class="cm"><div class="h"></div><div class="h">tebak Manusia</div><div class="h">tebak AI</div>
          <div class="h">Manusia</div><div class="good">${cm[0][0]}</div><div class="badc">${cm[0][1]}</div>
          <div class="h">AI</div><div class="badc">${cm[1][0]}</div><div class="good">${cm[1][1]}</div></div>
        <div class="md muted">Salah tandai manusia sebagai AI: ${cm[0][1]}</div></div>` : ''}`;
    applyWidths(cards);
    renderTraining(s.training);
    const tbl = $('#versionTable');
    tbl.innerHTML = v.versions.length ? `<thead><tr><th>Versi</th><th>Jenis</th><th>Contoh</th><th>Akurasi uji</th><th>F1 AI</th><th>Pembaruan</th><th>Dibuat</th><th></th></tr></thead>
      <tbody>${v.versions.map((x) => `<tr class="${x.is_active ? 'active-row' : ''}">
        <td><b>v${x.id}</b></td><td>${x.kind === 'full' ? 'Latih penuh' : 'Incremental'}</td>
        <td>${x.n_human ?? 0} / ${x.n_ai ?? 0}</td><td>${x.metrics ? pct(x.metrics.accuracy) : '–'}</td>
        <td>${x.metrics ? pct(x.metrics.f1_ai) : '–'}</td><td>${x.updates}</td><td class="muted">${fmtDate(x.created_at)}</td>
        <td>${x.is_active ? '<span class="badge neutral">AKTIF</span>' : `<button class="btn small" data-act="${x.id}">Aktifkan</button>`}</td></tr>`).join('')}</tbody>`
      : '<tbody><tr><td class="muted">Belum ada versi model. Model pertama dibuat otomatis saat Anda menambahkan contoh di Mode Belajar.</td></tr></tbody>';
    $$('[data-act]', tbl).forEach((b) => b.addEventListener('click', () => busy(b, '', async () => {
      await api(`/api/model/versions/${b.dataset.act}/activate`, { method: 'POST' });
      toast(`Versi v${b.dataset.act} diaktifkan`);
      loadModel(); refreshPill();
    })));
  }

  function renderTraining(t) {
    const el = $('#trainStatus');
    if (t.running) {
      el.innerHTML = `<div class="train-box"><span class="spinner"></span>Latih ulang penuh berjalan — tahap: <b>${esc(t.stage)}</b></div>`;
      if (!state.trainPoll) state.trainPoll = setInterval(pollTraining, 2500);
    } else if (t.error) {
      el.innerHTML = `<div class="train-box err">Latih ulang terakhir gagal: ${esc(t.error)}</div>`;
    } else if (t.finished_at) {
      el.innerHTML = `<div class="train-box done">Latih ulang terakhir selesai ${fmtDate(t.finished_at)} → v${t.last_version_id}</div>`;
    } else el.innerHTML = '';
  }

  async function pollTraining() {
    try {
      const s = await api('/api/model/status');
      if (!s.training.running) {
        clearInterval(state.trainPoll); state.trainPoll = null;
        if ($('#tab-model').classList.contains('active')) loadModel();
        refreshPill();
        toast(s.training.error ? 'Latih ulang gagal' : 'Latih ulang penuh selesai', Boolean(s.training.error));
      } else renderTraining(s.training);
    } catch { /* coba lagi */ }
  }

  $('#runRetrain').addEventListener('click', (e) => busy(e.currentTarget, 'Memulai…', async () => {
    const out = await api('/api/model/retrain', { method: 'POST' });
    renderTraining(out.training);
    toast('Latih ulang penuh dimulai');
  }));

  // ================= RIWAYAT =================
  async function loadHistory() {
    try {
      const list = await api(`/api/history?page=${state.histPage}&limit=20`);
      const el = $('#historyList');
      el.innerHTML = list.items.length ? list.items.map((h) => {
        const cls = h.ai_probability >= 0.58 ? 'ai' : h.ai_probability <= 0.42 ? 'human' : 'mid';
        return `<button class="hitem" data-h="${h.id}"><div class="p ${cls}">${pct(h.ai_probability)}</div>
          <div><div><b>${esc(h.verdict_label)}</b>${h.filename ? ` · <span class="muted">${esc(h.filename)}</span>` : ''}</div><div class="t">${esc(h.excerpt)}</div></div>
          <div class="m">${fmtDate(h.created_at)}<br>${h.word_count} kata · ${h.mode === 'model' ? `v${h.model_version_id}` : 'heuristik'}${h.feedback_label != null ? `<br><span class="badge ${h.feedback_label ? 'ai' : 'human'}">umpan balik: ${h.feedback_label ? 'AI' : 'MANUSIA'}</span>` : ''}</div></button>`;
      }).join('') : '<p class="muted">Belum ada pengecekan.</p>';
      $$('[data-h]', el).forEach((b) => b.addEventListener('click', async () => {
        try {
          const r = await api(`/api/history/${b.dataset.h}`);
          showTab('check');
          renderResult(r);
          window.scrollTo({ top: 0, behavior: 'smooth' });
        } catch (e) { toast(e.message, true); }
      }));
      pager('#histPager', list, (p) => { state.histPage = p; loadHistory(); });
    } catch (e) { toast(e.message, true); }
  }

  // ---------- Pengaturan token ----------
  const dlg = $('#settings');
  const markToken = () => $('#openSettings').classList.toggle('on', Boolean(getToken()));
  $('#openSettings').addEventListener('click', () => { $('#tokenInput').value = getToken(); dlg.showModal(); });
  dlg.addEventListener('close', () => {
    if (dlg.returnValue === 'save') { setToken($('#tokenInput').value.trim()); markToken(); toast('Token disimpan'); }
  });

  // ---------- Mulai ----------
  markToken();
  refreshPill().then((s) => { if (s?.training?.running && !state.trainPoll) state.trainPoll = setInterval(pollTraining, 2500); });
  const start = location.hash.replace('#', '');
  if (['check', 'learn', 'dataset', 'model', 'history'].includes(start)) showTab(start);
})();
