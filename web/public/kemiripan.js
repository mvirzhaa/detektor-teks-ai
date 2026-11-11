/* Modul Cek Kemiripan — antarmuka (vanilla JS) */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtDate = (d) => (d ? new Date(d).toLocaleString('id-ID', { dateStyle: 'medium', timeStyle: 'short' }) : '–');
  const fmtNum = (n) => Number(n || 0).toLocaleString('id-ID');
  const pct = (p) => `${p == null ? '–' : (p >= 1 || p === 0 ? Math.round(p) : '<1')}%`;
  const countWords = (t) => (t.match(/[\p{L}\p{N}]+/gu) || []).length;
  const lv = (p) => (p <= 0 ? 0 : p < 25 ? 1 : p < 50 ? 2 : p < 75 ? 3 : 4);
  const CAT = { internet: 'Sumber internet', publication: 'Publikasi', submission: 'Tugas mahasiswa' };
  const KIND = { oai_pmh: 'OAI-PMH', sitemap: 'Sitemap', web_crawl: 'Telusur web', rss: 'RSS/Atom', upload: 'Unggahan', web_search: 'Pencarian web', submission: 'Tugas' };
  const SCHED = { nightly: 'Setiap malam', weekly: 'Mingguan', manual: 'Manual' };
  const STATUS = { queued: 'antre', running: 'berjalan', done: 'selesai', failed: 'gagal' };
  const TOKEN_KEY = 'detektor.learnToken';
  const state = { mode: 'file', page: 1, docPage: 1, poll: null, report: null, excluded: new Set(), selected: null, reportPoll: null };

  const applyWidths = (root) => $$('[data-w]', root).forEach((el) => { el.style.width = `${el.dataset.w}%`; });
  const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; } };
  const setToken = (v) => { try { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY); } catch { /* */ } };

  const baseHref = document.querySelector('base')?.getAttribute('href') || '/';
  const cleanBase = baseHref.replace(/\/+$/, '');

  async function api(path, opts = {}) {
    const headers = {};
    if (getToken()) headers['x-learn-token'] = getToken();
    let body = opts.body;
    if (body && !(body instanceof FormData)) { headers['content-type'] = 'application/json'; body = JSON.stringify(body); }
    const url = path.startsWith('http') ? path : `${cleanBase}/${path.replace(/^\/+/, '')}`;
    const res = await fetch(url, { method: opts.method || (body ? 'POST' : 'GET'), headers, body });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { const e = new Error(data.error || `Gagal (${res.status})`); e.status = res.status; throw e; }
    return data;
  }
  let toastT;
  function toast(msg, err = false) {
    const t = $('#toast');
    t.textContent = msg;
    t.className = `toast show${err ? ' error' : ''}`;
    clearTimeout(toastT);
    toastT = setTimeout(() => { t.className = 'toast'; }, err ? 5500 : 3000);
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

  // ================= navigasi =================
  function show(tab) {
    $$('#simTabs .tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === tab));
    $$('main .panel').forEach((p) => p.classList.toggle('active', p.id === `tab-${tab}`));
  }
  function route() {
    clearInterval(state.reportPoll);
    const h = location.hash.replace('#', '');
    const m = h.match(/^laporan\/(\d+)$/);
    if (m) { show('report'); $('#simTabs').classList.add('hidden'); openReport(Number(m[1])); return; }
    $('#simTabs').classList.remove('hidden');
    const tab = ['checks', 'sources', 'index'].includes(h) ? h : 'checks';
    show(tab);
    if (tab === 'checks') { loadOverview(); loadChecks(); }
    if (tab === 'sources') { loadSources(); loadRuns(); }
    if (tab === 'index') loadDocs();
  }
  $$('#simTabs .tab').forEach((t) => t.addEventListener('click', () => { location.hash = t.dataset.tab; }));
  window.addEventListener('hashchange', route);
  $('#backToList').addEventListener('click', () => { location.hash = 'checks'; });

  // ================= kirim dokumen =================
  $$('#submitSeg button').forEach((b) => b.addEventListener('click', () => {
    $$('#submitSeg button').forEach((x) => x.classList.toggle('active', x === b));
    state.mode = b.dataset.mode;
    $$('#tab-checks .input-mode').forEach((m) => m.classList.toggle('hidden', m.dataset.for !== b.dataset.mode));
  }));
  function bindDrop(inputId, nameId) {
    const input = $(`#${inputId}`);
    const drop = input.closest('.drop');
    const showName = () => { $(`#${nameId}`).textContent = [...input.files].map((f) => f.name).join(', '); };
    input.addEventListener('change', showName);
    ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove('over')));
    drop.addEventListener('drop', (e) => { e.preventDefault(); input.files = e.dataTransfer.files; showName(); });
  }
  bindDrop('subFile', 'subFileName');
  bindDrop('repoFiles', 'repoFilesName');
  $('#subText').addEventListener('input', (e) => { $('#subWords').textContent = `${countWords(e.target.value)} kata`; });

  $('#runSubmit').addEventListener('click', (e) => busy(e.currentTarget, 'Mengirim…', async () => {
    const fd = new FormData();
    if (state.mode === 'file') {
      const f = $('#subFile').files[0];
      if (!f) throw new Error('Pilih dokumen terlebih dahulu.');
      fd.append('file', f);
    } else fd.append('text', $('#subText').value);
    fd.append('title', $('#subTitle').value);
    fd.append('author', $('#subAuthor').value);
    const cats = [['optInternet', 'internet'], ['optPublication', 'publication'], ['optSubmission', 'submission']]
      .filter(([id]) => $(`#${id}`).checked).map(([, c]) => c);
    if (!cats.length) throw new Error('Pilih minimal satu jenis sumber pembanding.');
    fd.append('categories', cats.join(','));
    fd.append('web', $('#optWeb').checked);
    fd.append('exclude_bibliography', $('#optBiblio').checked);
    fd.append('exclude_quotes', $('#optQuotes').checked);
    fd.append('min_words', $('#optMinWords').value);
    fd.append('save_to_repository', $('#optSave').checked);
    const r = await api('/api/sim/checks', { method: 'POST', body: fd });
    toast(`Dokumen dikirim (${fmtNum(r.words)} kata). Laporan sedang disusun…`);
    $('#subFile').value = ''; $('#subFileName').textContent = ''; $('#subText').value = ''; $('#subTitle').value = ''; $('#subAuthor').value = '';
    location.hash = `laporan/${r.id}`;
  }));

  // ================= ringkasan =================
  async function loadOverview() {
    try {
      const o = await api('/api/sim/overview');
      const w = o.worker;
      const el = $('#overview');
      el.innerHTML = `
        <h3>Basis data pembanding</h3>
        <div class="ov-stats">
          <div><b>${fmtNum(o.documents.publication.docs)}</b><span>Publikasi</span></div>
          <div><b>${fmtNum(o.documents.internet.docs)}</b><span>Halaman internet</span></div>
          <div><b>${fmtNum(o.documents.submission.docs)}</b><span>Tugas mahasiswa</span></div>
        </div>
        <div class="ov-line"><span>Worker</span><span><i class="status-dot ${w.alive ? 'on' : ''}"></i>${w.alive ? 'aktif' : 'tidak berjalan'}</span></div>
        <div class="ov-line"><span>Pencarian web</span><span>${w.search_provider ? `${esc(w.search_provider)} · ${w.search_ready ? 'siap' : '<span class="st-failed">belum dikonfigurasi</span>'}` : '–'}</span></div>
        <div class="ov-line"><span>Crawl otomatis</span><span>${w.crawl_time ? `setiap hari ${esc(w.crawl_time)} (${esc(w.crawl_tz)})` : '–'}</span></div>
        <div class="ov-line"><span>Crawl terjadwal terakhir</span><span>${esc(o.last_scheduled_crawl || 'belum pernah')}</span></div>
        <div class="ov-line"><span>Pemeriksaan</span><span>${fmtNum(o.checks.total)} total · ${o.checks.antre} dalam antrean</span></div>
        ${!w.alive ? '<div class="warn">Worker kemiripan tidak berjalan, sehingga pemeriksaan tidak akan diproses. Jalankan <code>python -m app.similarity.worker</code> (atau layanan <code>sim-worker</code> di Docker).</div>' : ''}`;
    } catch (e) { $('#overview').innerHTML = `<div class="warn">${esc(e.message)}</div>`; }
  }

  // ================= daftar pemeriksaan =================
  let searchT;
  $('#checkSearch').addEventListener('input', () => { clearTimeout(searchT); searchT = setTimeout(() => { state.page = 1; loadChecks(); }, 300); });

  async function loadChecks() {
    clearTimeout(state.poll);
    try {
      const list = await api(`/api/sim/checks?page=${state.page}&limit=15&q=${encodeURIComponent($('#checkSearch').value)}`);
      const tbl = $('#checkTable');
      tbl.innerHTML = list.items.length ? `<thead><tr><th>Judul / penulis</th><th>Dikirim</th><th>Kata</th><th>Kemiripan</th><th>Rincian</th><th></th></tr></thead>
        <tbody>${list.items.map((c) => {
          const running = c.status === 'queued' || c.status === 'running';
          const sim = c.status === 'done'
            ? `<button class="sim-badge lv${lv(c.similarity)}" data-open="${c.id}">${pct(c.similarity)}</button>`
            : running ? `<div class="mini-progress"><div class="progress"><b data-w="${c.progress || 2}"></b></div><small>${esc(c.stage || STATUS[c.status])}</small></div>`
              : `<span class="st-failed" title="${esc(c.error)}">gagal</span>`;
          return `<tr>
            <td class="t"><b>${esc(c.title)}</b><small>${esc(c.author || '')}${c.filename ? ` · ${esc(c.filename)}` : ''}${c.in_repository ? ' · disimpan di repositori' : ''}</small></td>
            <td class="muted">${fmtDate(c.created_at)}</td>
            <td>${fmtNum(c.word_count)}</td>
            <td>${sim}</td>
            <td>${c.status === 'done' ? `<div class="cats"><span>Internet ${pct(c.internet)}</span><span>Publikasi ${pct(c.publication)}</span><span>Tugas ${pct(c.submission)}</span></div>` : ''}</td>
            <td><div class="actions"><button class="btn small" data-open="${c.id}">Laporan</button><button class="btn small ghost" data-del="${c.id}">Hapus</button></div></td></tr>`;
        }).join('')}</tbody>`
        : '<tbody><tr><td class="muted">Belum ada pemeriksaan.</td></tr></tbody>';
      applyWidths(tbl);
      $$('[data-open]', tbl).forEach((b) => b.addEventListener('click', () => { location.hash = `laporan/${b.dataset.open}`; }));
      $$('[data-del]', tbl).forEach((b) => b.addEventListener('click', () => busy(b, '', async () => {
        await api(`/api/sim/checks/${b.dataset.del}`, { method: 'DELETE' });
        toast('Pemeriksaan dihapus'); loadChecks();
      })));
      pager('#checkPager', list, (p) => { state.page = p; loadChecks(); });
      if (list.items.some((c) => c.status === 'queued' || c.status === 'running')) state.poll = setTimeout(loadChecks, 2500);
    } catch (e) { toast(e.message, true); }
  }

  function pager(sel, list, go) {
    const pages = Math.max(1, Math.ceil(list.total / list.limit));
    const el = $(sel);
    el.innerHTML = `<span>Halaman ${list.page} dari ${pages} · ${fmtNum(list.total)} data</span>
      <button class="btn small" data-p="${list.page - 1}" ${list.page <= 1 ? 'disabled' : ''}>‹</button>
      <button class="btn small" data-p="${list.page + 1}" ${list.page >= pages ? 'disabled' : ''}>›</button>`;
    $$('[data-p]', el).forEach((b) => b.addEventListener('click', () => go(Number(b.dataset.p))));
  }

  // ================= LAPORAN =================
  async function openReport(id) {
    $('#reportGrid').classList.add('hidden');
    $('#filtersCard').classList.add('hidden');
    const prog = $('#reportProgress');
    prog.classList.remove('hidden');
    prog.innerHTML = '<div class="card progress-card"><span class="spinner"></span></div>';
    let c;
    try { c = await api(`/api/sim/checks/${id}`); } catch (e) { prog.innerHTML = `<div class="card warn">${esc(e.message)}</div>`; return; }
    $('#rTitle').textContent = c.title || `Pemeriksaan #${id}`;
    $('#rMeta').textContent = `${c.author ? `${c.author} · ` : ''}${fmtDate(c.created_at)} · ${fmtNum(c.word_count)} kata · #${id}`;
    if (c.status === 'queued' || c.status === 'running') {
      const tick = async () => {
        try {
          const s = await api(`/api/sim/checks/${id}/status`);
          if (s.status === 'done' || s.status === 'failed') { clearInterval(state.reportPoll); openReport(id); return; }
          prog.innerHTML = `<div class="card progress-card"><h2>Menyusun laporan kemiripan…</h2>
            <div class="progress"><b data-w="${s.progress || 2}"></b></div><div class="muted">${esc(s.stage || 'menunggu antrean')}</div></div>`;
          applyWidths(prog);
        } catch { /* coba lagi */ }
      };
      tick();
      state.reportPoll = setInterval(tick, 2000);
      return;
    }
    if (c.status === 'failed') {
      prog.innerHTML = `<div class="card"><h2>Pemeriksaan gagal</h2><p class="warn">${esc(c.error)}</p><button class="btn" id="retryBtn">Coba lagi</button></div>`;
      $('#retryBtn').addEventListener('click', (e) => busy(e.currentTarget, 'Mengantre…', async () => {
        await api(`/api/sim/checks/${id}/rerun`, { method: 'POST', body: {} }); openReport(id);
      }));
      return;
    }
    prog.classList.add('hidden');
    state.report = c;
    state.excluded = new Set();
    state.selected = null;
    fillFilters(c.options || {});
    renderReport();
    $('#reportGrid').classList.remove('hidden');
  }

  function compute() {
    const r = state.report.result;
    const total = r.total_words || 1;
    const owner = new Int32Array(r.total_words).fill(-1);
    const owned = r.sources.map(() => 0);
    const catSets = { internet: new Uint8Array(r.total_words), publication: new Uint8Array(r.total_words), submission: new Uint8Array(r.total_words) };
    r.sources.forEach((s, idx) => {
      if (state.excluded.has(idx)) return;
      for (const [a, b] of s.ranges) {
        for (let i = a; i < b; i++) {
          if (owner[i] === -1) { owner[i] = idx; owned[idx]++; }
          if (catSets[s.category]) catSets[s.category][i] = 1;
        }
      }
    });
    const covered = owner.reduce((n, o) => n + (o !== -1 ? 1 : 0), 0);
    const cat = Object.fromEntries(Object.entries(catSets).map(([k, arr]) => [k, (100 * arr.reduce((n, x) => n + x, 0)) / total]));
    return { owner, owned, similarity: (100 * covered) / total, cat, total };
  }

  function renderReport() {
    const c = state.report;
    const r = c.result;
    const comp = compute();
    // ---------- dokumen ----------
    const text = c.text;
    const wo = r.word_offsets;
    const marks = [];
    let i = 0;
    while (i < comp.owner.length) {
      const o = comp.owner[i];
      if (o === -1) { i++; continue; }
      let j = i;
      while (j + 1 < comp.owner.length && comp.owner[j + 1] === o) j++;
      marks.push({ start: wo[i][0], end: wo[j][1], src: o, ws: i });
      i = j + 1;
    }
    for (const x of r.excluded || []) marks.push({ start: x.start, end: x.end, exc: x.kind });
    marks.sort((a, b) => a.start - b.start);
    let html = `<div class="paper-head"><b>${esc(c.title)}</b>${esc(c.author || '')}${c.author ? ' · ' : ''}${fmtNum(c.word_count)} kata · diperiksa ${fmtDate(c.finished_at)}</div>`;
    let pos = 0;
    const startNum = new Set();
    for (const m of marks) {
      if (m.start < pos) continue;
      html += esc(text.slice(pos, m.start));
      if (m.exc) {
        html += `<span class="exc" title="${m.exc === 'biblio' ? 'Daftar pustaka (dikecualikan)' : 'Kutipan (dikecualikan)'}">${esc(text.slice(m.start, m.end))}</span>`;
      } else {
        const first = !startNum.has(`${m.src}:${m.ws}`);
        startNum.add(`${m.src}:${m.ws}`);
        html += `<span class="hl c${m.src % 12}${state.selected === m.src ? ' sel' : ''}" data-src="${m.src}" data-ws="${m.ws}">${first ? `<sup class="sn">${m.src + 1}</sup>` : ''}${esc(text.slice(m.start, m.end))}</span>`;
      }
      pos = m.end;
    }
    html += esc(text.slice(pos));
    const paper = $('#paper');
    paper.innerHTML = html;
    paper.classList.toggle('selecting', state.selected != null);
    $$('.hl', paper).forEach((el) => el.addEventListener('click', () => selectSource(Number(el.dataset.src), Number(el.dataset.ws))));

    // ---------- panel kecocokan ----------
    const sim = comp.similarity;
    const web = r.web || {};
    const items = r.sources.map((s, idx) => ({ s, idx })).filter(({ idx }) => comp.owned[idx] > 0 || state.excluded.has(idx));
    const panel = $('#matchPanel');
    panel.innerHTML = `
      <div class="mp-score lv${lv(sim)}">
        <div class="lbl">Indeks kemiripan</div>
        <div class="big">${pct(sim)}</div>
        <div class="sub2">${fmtNum(Math.round((sim / 100) * comp.total))} dari ${fmtNum(comp.total)} kata cocok dengan ${items.filter(({ idx }) => !state.excluded.has(idx)).length} sumber</div>
      </div>
      <div class="mp-cats">
        ${Object.entries(CAT).map(([k, label]) => `<div class="mp-cat"><span>${label}</span><b>${pct(comp.cat[k])}</b><div class="bar"><b data-w="${Math.min(100, comp.cat[k]).toFixed(1)}"></b></div></div>`).join('')}
      </div>
      <div class="mp-h"><span>Sumber utama</span><span>${state.excluded.size ? `${state.excluded.size} dikecualikan` : ''}</span></div>
      <ol class="src-list">${items.length ? items.map(({ s, idx }) => {
        const off = state.excluded.has(idx);
        const p = off ? 0 : (100 * comp.owned[idx]) / comp.total;
        return `<li>
          <div class="src c${idx % 12}${state.selected === idx ? ' sel' : ''}${off ? ' off' : ''}" data-sel="${idx}">
            <span class="num">${idx + 1}</span>
            <div><div class="ttl">${esc(s.title)}</div><div class="dom">${esc(CAT[s.category])}${s.domain ? ` · ${esc(s.domain)}` : s.source_name ? ` · ${esc(s.source_name)}` : ''}</div></div>
            <div><div class="pct">${off ? '–' : pct(p)}</div><button class="x" data-ex="${idx}">${off ? 'sertakan' : 'kecualikan'}</button></div>
          </div>
          ${state.selected === idx ? detail(s, idx) : ''}</li>`;
      }).join('') : '<li class="muted small">Tidak ditemukan kecocokan dengan sumber mana pun.</li>'}</ol>
      <div class="mp-web"><details${web.errors?.length ? ' open' : ''}>
        <summary>Pencarian web: ${web.enabled ? `${(web.queries || []).length} kueri · ${web.urls_found || 0} URL ditemukan · ${web.fetched || 0} diunduh${web.cached ? ` · ${web.cached} dari cache` : ''}${web.reused ? ' (dipakai ulang)' : ''}` : 'tidak dijalankan'}</summary>
        ${(web.queries || []).length ? `<ol>${web.queries.map((q) => `<li>“${esc(q)}”</li>`).join('')}</ol>` : ''}
        ${(web.errors || []).map((e) => `<div class="err">${esc(e)}</div>`).join('')}
        <div class="muted">Kandidat dibandingkan: ${r.candidates ?? '–'} dokumen</div>
      </details></div>`;
    applyWidths(panel);
    $$('[data-sel]', panel).forEach((el) => el.addEventListener('click', (e) => {
      if (e.target.closest('[data-ex]') || e.target.closest('a')) return;
      const idx = Number(el.dataset.sel);
      selectSource(state.selected === idx ? null : idx);
    }));
    $$('[data-ex]', panel).forEach((b) => b.addEventListener('click', () => {
      const idx = Number(b.dataset.ex);
      state.excluded.has(idx) ? state.excluded.delete(idx) : state.excluded.add(idx);
      if (state.selected === idx) state.selected = null;
      renderReport();
    }));
  }

  function detail(s, idx) {
    const meta = [s.authors, s.published, s.source_name].filter(Boolean).map(esc).join(' · ');
    return `<div class="src-detail c${idx % 12}" id="detail-${idx}">
      <div class="meta">${meta}${s.url ? `<br><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.url)} ↗</a>` : ''}</div>
      ${s.matches.slice(0, 12).map((m) => `<div class="m" data-qs="${m.qs}"><q>${esc(m.source_text || '(teks sumber tidak tersedia)')}</q><small>${m.words} kata cocok</small></div>`).join('')}
      ${s.matches.length > 12 ? `<small class="muted">+${s.matches.length - 12} kecocokan lain</small>` : ''}
    </div>`;
  }

  function selectSource(idx, ws) {
    state.selected = idx;
    renderReport();
    if (idx == null) return;
    const target = ws != null ? $(`#paper .hl[data-ws="${ws}"]`) : $(`#paper .hl[data-src="${idx}"]`);
    if (target) {
      target.classList.add('focus');
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setTimeout(() => target.classList.remove('focus'), 1500);
    }
    const d = $(`#detail-${idx}`);
    if (d) {
      d.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      if (ws != null) {
        const m = [...$$('.m', d)].filter((x) => Number(x.dataset.qs) <= ws).pop();
        if (m) m.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }
  }

  function fillFilters(o) {
    const cats = o.categories || ['internet', 'publication', 'submission'];
    $('#fInternet').checked = cats.includes('internet');
    $('#fPublication').checked = cats.includes('publication');
    $('#fSubmission').checked = cats.includes('submission');
    $('#fBiblio').checked = o.exclude_bibliography !== false;
    $('#fQuotes').checked = Boolean(o.exclude_quotes);
    $('#fMinWords').value = o.min_words || 8;
    $('#fSearchAgain').checked = false;
  }
  $('#toggleFilters').addEventListener('click', () => $('#filtersCard').classList.toggle('hidden'));
  $('#printReport').addEventListener('click', () => window.print());
  $('#applyFilters').addEventListener('click', (e) => busy(e.currentTarget, 'Mengantre…', async () => {
    const cats = [['fInternet', 'internet'], ['fPublication', 'publication'], ['fSubmission', 'submission']].filter(([id]) => $(`#${id}`).checked).map(([, c]) => c);
    if (!cats.length) throw new Error('Pilih minimal satu jenis sumber.');
    const id = state.report.id;
    await api(`/api/sim/checks/${id}/rerun`, { method: 'POST', body: {
      categories: cats, exclude_bibliography: $('#fBiblio').checked, exclude_quotes: $('#fQuotes').checked,
      min_words: $('#fMinWords').value, search_again: $('#fSearchAgain').checked,
    } });
    openReport(id);
  }));

  // ================= SUMBER DATA =================
  const KIND_HELP = {
    oai_pmh: ['https://journal.contoh.ac.id/index.php/jti/oai',
      'Endpoint OAI-PMH. OJS 3: …/index.php/<jurnal>/oai · EPrints: …/cgi/oai2 · DSpace 7: …/server/oai/request. Teks lengkap diambil dari PDF artikel; panen berikutnya hanya rekaman baru/berubah.',
      { set: '', fulltext: true, max_docs: 500 }],
    sitemap: ['https://www.contoh.ac.id/', 'URL situs (sitemap dicari otomatis dari robots.txt) atau langsung URL sitemap.xml. Setiap malam hanya halaman baru/berubah yang diunduh.',
      { include: '/(berita|artikel)/', exclude: '/(tag|kategori)/', max_docs: 500, recrawl_days: 30 }],
    web_crawl: ['https://blog.contoh.com/', 'Tautan pada domain yang sama ditelusuri dari URL awal sampai kedalaman tertentu.',
      { depth: 2, include: '', exclude: '/(tag|kategori|page)/', max_docs: 300 }],
    rss: ['https://www.contoh.com/feed', 'Feed RSS/Atom: setiap artikel baru diunduh halamannya.', { max_docs: 100 }],
  };
  function updateKindHelp() {
    const [ph, hint, cfg] = KIND_HELP[$('#srcKind').value];
    $('#srcUrl').placeholder = ph;
    $('#srcHint').textContent = hint;
    $('#srcConfig').placeholder = JSON.stringify(cfg, null, 2);
  }
  $('#srcKind').addEventListener('change', updateKindHelp);
  updateKindHelp();

  $('#sourceForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const f = e.currentTarget;
    const btn = $('button[type=submit]', f);
    busy(btn, 'Menyimpan…', async () => {
      const fd = new FormData(f);
      const body = Object.fromEntries(fd.entries());
      body.crawl_now = fd.get('crawl_now') === 'on';
      if (!body.category) delete body.category;
      if (!body.config.trim()) body.config = {};
      await api('/api/sim/sources', { method: 'POST', body });
      toast(body.crawl_now ? 'Sumber disimpan, crawl pertama dimulai' : 'Sumber disimpan');
      f.reset(); updateKindHelp();
      loadSources(); loadRuns();
    });
  });

  async function loadSources() {
    try {
      const [{ items }, o] = await Promise.all([api('/api/sim/sources'), api('/api/sim/overview')]);
      const w = o.worker;
      $('#schedInfo').textContent = w.alive ? `Crawl otomatis setiap hari pukul ${w.crawl_time} (${w.crawl_tz})` : 'Worker tidak berjalan — crawl terjadwal tidak akan dieksekusi';
      const tbl = $('#sourceTable');
      tbl.innerHTML = `<thead><tr><th>Sumber</th><th>Jenis</th><th>Kategori</th><th>Jadwal</th><th>Dokumen</th><th>Crawl terakhir</th><th></th></tr></thead>
        <tbody>${items.map((s) => `<tr>
          <td><b>${esc(s.name)}</b>${s.url ? `<div class="url-cell" title="${esc(s.url)}">${esc(s.url)}</div>` : ''}</td>
          <td><span class="kind">${esc(KIND[s.kind] || s.kind)}</span></td>
          <td>${esc(CAT[s.category])}</td>
          <td>${s.system ? '<span class="muted">bawaan</span>' : `${esc(SCHED[s.schedule])}${s.enabled ? '' : ' <span class="st-failed">(nonaktif)</span>'}`}</td>
          <td>${fmtNum(s.documents)}<br><small class="muted">${fmtNum(s.words)} kata</small></td>
          <td>${s.last_run_id ? `<span class="st-${esc(s.last_run_status)}">${esc(STATUS[s.last_run_status] || s.last_run_status)}</span><br><small class="muted">${s.last_run_finished ? `${fmtDate(s.last_run_finished)} · +${s.last_run_new} baru` : ''}</small>` : '<span class="muted">–</span>'}</td>
          <td>${s.system ? '' : `<div class="actions">
            <button class="btn small" data-crawl="${s.id}">Crawl sekarang</button>
            <button class="btn small ghost" data-toggle="${s.id}" data-on="${s.enabled}">${s.enabled ? 'Nonaktifkan' : 'Aktifkan'}</button>
            <button class="btn small ghost" data-reset="${s.id}" title="Panen ulang dari awal pada crawl berikutnya">Reset</button>
            <button class="btn small ghost" data-delsrc="${s.id}">Hapus</button></div>`}</td></tr>`).join('')}</tbody>`;
      $$('[data-crawl]', tbl).forEach((b) => b.addEventListener('click', () => busy(b, '', async () => {
        await api(`/api/sim/sources/${b.dataset.crawl}/crawl`, { method: 'POST' }); toast('Crawl diantrekan'); loadRuns(); loadSources();
      })));
      $$('[data-toggle]', tbl).forEach((b) => b.addEventListener('click', () => busy(b, '', async () => {
        await api(`/api/sim/sources/${b.dataset.toggle}`, { method: 'PATCH', body: { enabled: b.dataset.on !== 'true' } }); loadSources();
      })));
      $$('[data-reset]', tbl).forEach((b) => b.addEventListener('click', () => busy(b, '', async () => {
        await api(`/api/sim/sources/${b.dataset.reset}`, { method: 'PATCH', body: { reset_state: true } }); toast('Kursor crawl direset');
      })));
      $$('[data-delsrc]', tbl).forEach((b) => b.addEventListener('click', () => busy(b, '', async () => {
        await api(`/api/sim/sources/${b.dataset.delsrc}`, { method: 'DELETE' }); toast('Sumber & dokumennya dihapus dari indeks'); loadSources();
      })));
    } catch (e) { toast(e.message, true); }
  }

  let runsPoll;
  async function loadRuns() {
    clearTimeout(runsPoll);
    try {
      const { items } = await api('/api/sim/runs');
      const tbl = $('#runTable');
      tbl.innerHTML = items.length ? `<thead><tr><th>#</th><th>Sumber</th><th>Pemicu</th><th>Status</th><th>Dilihat</th><th>Baru</th><th>Diperbarui</th><th>Dilewati</th><th>Gagal</th><th>Waktu</th><th></th></tr></thead>
        <tbody>${items.map((r) => `<tr><td class="muted">${r.id}</td><td>${esc(r.source_name)}</td><td>${r.trigger === 'manual' ? 'manual' : 'terjadwal'}</td>
          <td><span class="st-${esc(r.status)}">${esc(STATUS[r.status])}</span></td><td>${fmtNum(r.docs_seen)}</td><td>${fmtNum(r.docs_new)}</td>
          <td>${fmtNum(r.docs_updated)}</td><td>${fmtNum(r.docs_skipped)}</td><td>${fmtNum(r.docs_failed)}</td>
          <td class="muted">${fmtDate(r.started_at || r.created_at)}</td><td><button class="btn small ghost" data-log="${r.id}">Log</button></td></tr>`).join('')}</tbody>`
        : '<tbody><tr><td class="muted">Belum ada crawl.</td></tr></tbody>';
      $$('[data-log]', tbl).forEach((b) => b.addEventListener('click', async () => {
        try {
          const r = await api(`/api/sim/runs/${b.dataset.log}`);
          $('#logTitle').textContent = `Log crawl #${r.id} — ${r.source_name}`;
          $('#logBody').textContent = (r.log || '(kosong)') + (r.error ? `\n\nERROR: ${r.error}` : '');
          $('#logDialog').showModal();
        } catch (e) { toast(e.message, true); }
      }));
      if (items.some((r) => r.status === 'queued' || r.status === 'running') && $('#tab-sources').classList.contains('active')) {
        runsPoll = setTimeout(() => { loadRuns(); loadSources(); }, 3000);
      }
    } catch (e) { toast(e.message, true); }
  }
  $('#refreshRuns').addEventListener('click', () => { loadRuns(); loadSources(); });

  $('#runRepoUpload').addEventListener('click', (e) => busy(e.currentTarget, 'Mengindeks…', async () => {
    const files = $('#repoFiles').files;
    if (!files.length) throw new Error('Pilih dokumen terlebih dahulu.');
    const fd = new FormData();
    [...files].forEach((f) => fd.append('files', f));
    fd.append('authors', $('#repoAuthors').value);
    fd.append('published', $('#repoYear').value);
    const out = await api('/api/sim/repository/upload', { method: 'POST', body: fd });
    $('#repoResult').innerHTML = `<div class="learn-out"><ul>${out.results.map((r) => `<li>${esc(r.filename)}: ${r.ok ? `<span class="ok">${esc(r.status)}</span> (${fmtNum(r.words)} kata)` : `<span class="bad">${esc(r.error)}</span>`}</li>`).join('')}</ul></div>`;
    $('#repoFiles').value = ''; $('#repoFilesName').textContent = '';
    loadSources();
  }));

  // ================= INDEKS =================
  let docT;
  $('#docSearch').addEventListener('input', () => { clearTimeout(docT); docT = setTimeout(() => { state.docPage = 1; loadDocs(); }, 300); });
  $('#docSource').addEventListener('change', () => { state.docPage = 1; loadDocs(); });
  async function loadDocs() {
    try {
      if ($('#docSource').options.length <= 1) {
        const { items } = await api('/api/sim/sources');
        $('#docSource').innerHTML = '<option value="">Semua sumber</option>' + items.map((s) => `<option value="${s.id}">${esc(s.name)} (${fmtNum(s.documents)})</option>`).join('');
      }
      const list = await api(`/api/sim/documents?page=${state.docPage}&source_id=${$('#docSource').value}&q=${encodeURIComponent($('#docSearch').value)}`);
      const tbl = $('#docTable');
      tbl.innerHTML = list.items.length ? `<thead><tr><th>Judul</th><th>Sumber</th><th>Kata</th><th>Diambil</th><th></th></tr></thead>
        <tbody>${list.items.map((d) => `<tr><td><b>${esc(d.title || '(tanpa judul)')}</b>${d.authors ? `<br><small class="muted">${esc(d.authors)}${d.published ? ` · ${esc(d.published)}` : ''}</small>` : ''}
          ${d.url ? `<div class="url-cell"><a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">${esc(d.url)}</a></div>` : ''}</td>
          <td>${esc(d.source_name)}<br><small class="muted">${esc(CAT[d.category])}</small></td><td>${fmtNum(d.word_count)}</td>
          <td class="muted">${fmtDate(d.fetched_at)}</td><td><button class="btn small ghost" data-deldoc="${d.id}">Hapus</button></td></tr>`).join('')}</tbody>`
        : '<tbody><tr><td class="muted">Belum ada dokumen terindeks. Tambahkan sumber data atau unggah dokumen ke repositori.</td></tr></tbody>';
      $$('[data-deldoc]', tbl).forEach((b) => b.addEventListener('click', () => busy(b, '', async () => {
        await api(`/api/sim/documents/${b.dataset.deldoc}`, { method: 'DELETE' }); toast('Dokumen dihapus dari indeks'); loadDocs();
      })));
      pager('#docPager', list, (p) => { state.docPage = p; loadDocs(); });
    } catch (e) { toast(e.message, true); }
  }

  // ================= token =================
  const dlg = $('#settings');
  const markToken = () => $('#openSettings').classList.toggle('on', Boolean(getToken()));
  $('#openSettings').addEventListener('click', () => { $('#tokenInput').value = getToken(); dlg.showModal(); });
  dlg.addEventListener('close', () => { if (dlg.returnValue === 'save') { setToken($('#tokenInput').value.trim()); markToken(); toast('Token disimpan'); } });

  markToken();
  route();
})();
