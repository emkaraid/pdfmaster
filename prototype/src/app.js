// PDF-мастерская — всё работает локально в браузере.
const { PDFDocument, degrees, rgb } = PDFLib;
const $ = (s) => document.querySelector(s);
const DPR = Math.min(window.devicePixelRatio || 1, 2);
const MAXPIX = 14e6;
const LH = 1.2; // межстрочный интервал для текста

const S = {
  sources: {}, pages: [], overlays: [], imgs: {},
  scale: 1, fit: true, mode: 'select', sel: null,
  fileHandle: null, fileName: '', dirty: false, mainSrc: null,
};
let uid = 1; const nid = (p) => p + (uid++) + '_' + Math.random().toString(36).slice(2, 6);
const PV = new Map(); // pageId -> {pv, pdfPage, div, canvas, lay, tl, renderedScale, tlBuilt}
const FONTFACE = { serif: 'PDFM Serif', sans: 'PDFM Sans' };

/* ---------- утилиты ---------- */
function toast(msg, ms = 2600) { const t = $('#toast'); t.textContent = msg; t.style.display = 'block'; clearTimeout(toast._t); toast._t = setTimeout(() => t.style.display = 'none', ms); }
function busy(on, txt) { const b = $('#busy'); b.textContent = txt || 'Сохраняю…'; b.style.display = on ? 'flex' : 'none'; }
function b64ToBytes(b64) { const s = atob(b64); const u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u; }
function lsGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); return true; } catch { return false; } }
const dist = (p, q) => Math.hypot(q[0] - p[0], q[1] - p[1]);
function hexToRgb(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function setDirty(v = true) { S.dirty = v; updTitle(); }
function updTitle() { $('#fname').textContent = (S.dirty ? '● ' : '') + (S.fileName || ''); document.title = (S.fileName ? S.fileName + ' — ' : '') + 'PDF‑мастерская'; }

async function loadFonts() {
  for (const fam of ['serif', 'sans']) for (const b of ['r', 'b']) {
    const ff = new FontFace(FONTFACE[fam], b64ToBytes(FONTDATA[fam][b]).buffer, { weight: b === 'b' ? '700' : '400' });
    await ff.load(); document.fonts.add(ff);
  }
}
function famMetrics(fam) { const m = METRICS[fam]; const hl = (LH - (m.asc + m.desc)) / 2; return { top: hl + m.asc, bottom: LH - hl - m.asc }; }

/* ---------- геометрия: объект хранится в координатах PDF ---------- */
// g = {x,y,w,h,a}: (x,y) — левый нижний угол (как видно на экране при создании), a — угол в радианах
function corners(g) { const c = Math.cos(g.a), s = Math.sin(g.a); const P0 = [g.x, g.y]; return { P0, P1: [g.x + g.w * c, g.y + g.w * s], P3: [g.x - g.h * s, g.y + g.h * c] }; }
function toV(pv, p) { return pv.convertToViewportPoint(p[0], p[1]); }
function toP(pv, v) { return pv.convertToPdfPoint(v[0], v[1]); }
function placeBox(el, g, pv) {
  const { P0, P1, P3 } = corners(g); const V0 = toV(pv, P0), V1 = toV(pv, P1), V3 = toV(pv, P3);
  const th = Math.atan2(V1[1] - V0[1], V1[0] - V0[0]);
  el.style.left = V3[0] + 'px'; el.style.top = V3[1] + 'px';
  el.style.width = dist(V0, V1) + 'px'; el.style.height = dist(V0, V3) + 'px';
  el.style.transform = Math.abs(th) > 1e-4 ? `rotate(${th}rad)` : '';
  return th;
}
function geomFromScreen(pv, left, top, W, H) { // прямоугольник, ровный на экране
  const P0 = toP(pv, [left, top + H]), P1 = toP(pv, [left + W, top + H]);
  return { x: P0[0], y: P0[1], a: Math.atan2(P1[1] - P0[1], P1[0] - P0[0]), w: W / pv.scale, h: H / pv.scale };
}
function screenAngle(g, pv) { const { P0, P1 } = corners(g); const V0 = toV(pv, P0), V1 = toV(pv, P1); return Math.atan2(V1[1] - V0[1], V1[0] - V0[0]); }

/* ---------- история ---------- */
const H = { stack: [], i: -1 };
function snap() { return JSON.stringify({ pages: S.pages, overlays: S.overlays }); }
function commit(structural = false) {
  H.stack = H.stack.slice(0, H.i + 1); H.stack.push({ s: snap(), structural }); H.i = H.stack.length - 1;
  if (H.stack.length > 200) { H.stack.shift(); H.i--; }
  setDirty(true); updUndo();
}
function resetHistory() { H.stack = [{ s: snap(), structural: true }]; H.i = 0; updUndo(); }
function restore(k) {
  const prev = H.stack[H.i]; H.i = k; const st = JSON.parse(H.stack[k].s);
  const structural = (prev && prev.structural) || H.stack[k].structural || JSON.stringify(st.pages) !== JSON.stringify(S.pages);
  S.pages = st.pages; S.overlays = st.overlays; S.sel = null; hideProps();
  if (structural) rebuildAll(); else renderAllOverlays();
  setDirty(true); updUndo();
}
function undo() { if (H.i > 0) restore(H.i - 1); }
function redo() { if (H.i < H.stack.length - 1) restore(H.i + 1); }
function updUndo() { $('#bUndo').disabled = H.i <= 0; $('#bRedo').disabled = H.i >= H.stack.length - 1; }

/* ---------- открытие файлов ---------- */
async function addSourceFromBytes(bytes, name) {
  const id = nid('s');
  let doc;
  try { doc = await pdfjsLib.getDocument({ data: bytes.slice(), isEvalSupported: false }).promise; }
  catch (e) {
    if (e && e.name === 'PasswordException') {
      const pw = prompt('Файл «' + name + '» защищён паролем. Введи пароль:');
      if (!pw) throw new Error('Нужен пароль');
      doc = await pdfjsLib.getDocument({ data: bytes.slice(), password: pw, isEvalSupported: false }).promise;
    } else throw e;
  }
  S.sources[id] = { id, name, bytes, doc, lib: null, pageCache: {} };
  return id;
}
async function imageToPdfBytes(file) {
  const buf = new Uint8Array(await file.arrayBuffer());
  const d = await PDFDocument.create();
  const img = /png$/i.test(file.type) ? await d.embedPng(buf) : await d.embedJpg(buf);
  // лист A4, картинка вписана с полями
  const A4 = [595.28, 841.89]; const land = img.width > img.height; const [W, Hh] = land ? [A4[1], A4[0]] : A4;
  const m = 20, k = Math.min((W - 2 * m) / img.width, (Hh - 2 * m) / img.height);
  const p = d.addPage([W, Hh]); const w = img.width * k, h = img.height * k;
  p.drawImage(img, { x: (W - w) / 2, y: (Hh - h) / 2, width: w, height: h });
  return await d.save();
}
async function readAsBytes(file) { return new Uint8Array(await file.arrayBuffer()); }
async function openFiles(files, { replace, handle } = {}) {
  files = [...files]; if (!files.length) return;
  if (replace && S.dirty && !confirm('Есть несохранённые изменения. Открыть другой файл без сохранения?')) return;
  try {
    busy(true, 'Открываю…');
    if (replace) {
      for (const s of Object.values(S.sources)) try { s.doc.destroy(); } catch {}
      S.sources = {}; S.pages = []; S.overlays = []; S.sel = null; S.fileHandle = handle || null; S.fileName = files[0].name; S.mainSrc = null; S.fit = true;
    }
    for (const f of files) {
      const isImg = /^image\//.test(f.type) || /\.(png|jpe?g)$/i.test(f.name);
      const bytes = isImg ? await imageToPdfBytes(f) : await readAsBytes(f);
      const sid = await addSourceFromBytes(bytes, f.name);
      if (!S.mainSrc) S.mainSrc = sid;
      const n = S.sources[sid].doc.numPages;
      for (let i = 0; i < n; i++) S.pages.push({ id: nid('p'), src: sid, idx: i, rot: 0 });
    }
    if (replace) { await rebuildAll(); resetHistory(); setDirty(false); }
    else { await rebuildAll(); commit(true); toast('Добавлено файлов: ' + files.length); }
  } catch (e) { console.error(e); alert('Не получилось открыть файл: ' + (e.message || e)); }
  finally { busy(false); updTitle(); }
}
async function pickOpen(replace) {
  if (window.showOpenFilePicker) {
    try {
      const hs = await showOpenFilePicker({ multiple: !replace, types: [{ description: 'PDF', accept: replace ? { 'application/pdf': ['.pdf'] } : { 'application/pdf': ['.pdf'], 'image/png': ['.png'], 'image/jpeg': ['.jpg', '.jpeg'] } }] });
      const files = await Promise.all(hs.map(h => h.getFile()));
      return openFiles(files, { replace, handle: replace ? hs[0] : null });
    } catch (e) { if (e.name === 'AbortError') return; console.warn(e); }
  }
  (replace ? $('#fOpen') : $('#fAdd')).click();
}

/* ---------- страницы ---------- */
async function getPdfPage(src, idx) {
  const s = S.sources[src]; if (!s.pageCache[idx]) s.pageCache[idx] = await s.doc.getPage(idx + 1); return s.pageCache[idx];
}
function pageRotation(pp, pe) { return ((pp.rotate || 0) + pe.rot) % 360; }

let io = null, tio = null;
async function rebuildAll() { await buildViewer(); buildThumbs(); }

async function computeFitScale() {
  if (!S.pages.length) return 1;
  let maxw = 0;
  for (const pe of S.pages.slice(0, 30)) { const pp = await getPdfPage(pe.src, pe.idx); maxw = Math.max(maxw, pp.getViewport({ scale: 1, rotation: pageRotation(pp, pe) }).width); }
  const avail = $('#viewer').clientWidth - 48; return Math.max(0.1, Math.min(4, avail / maxw));
}
async function buildViewer() {
  const viewer = $('#viewer'), host = $('#pages');
  const ratio = viewer.scrollHeight ? viewer.scrollTop / viewer.scrollHeight : 0;
  if (io) io.disconnect();
  for (const v of PV.values()) if (v.task) try { v.task.cancel(); } catch {}
  PV.clear(); host.innerHTML = '';
  $('#empty').style.display = S.pages.length ? 'none' : 'flex';
  if (!S.pages.length) { updZoom(); return; }
  if (S.fit) S.scale = await computeFitScale();
  updZoom();
  io = new IntersectionObserver(onVisible, { root: viewer, rootMargin: '800px 0px' });
  for (const pe of S.pages) {
    const pp = await getPdfPage(pe.src, pe.idx);
    const pv = pp.getViewport({ scale: S.scale, rotation: pageRotation(pp, pe) });
    const div = document.createElement('div'); div.className = 'page'; div.dataset.pid = pe.id;
    div.style.width = pv.width + 'px'; div.style.height = pv.height + 'px';
    const canvas = document.createElement('canvas'); canvas.className = 'pc';
    const tl = document.createElement('div'); tl.className = 'tl';
    const lay = document.createElement('div'); lay.className = 'lay';
    div.append(canvas, tl, lay); host.append(div);
    PV.set(pe.id, { pv, pp, div, canvas, lay, tl, renderedScale: 0, tlBuilt: false, visible: false });
    div.addEventListener('pointerdown', (e) => onLayerDown(e, pe.id));
    io.observe(div);
    renderOverlays(pe.id);
  }
  viewer.scrollTop = ratio * viewer.scrollHeight;
}
// смена масштаба без пересоздания листов
async function rescaleViewer() {
  if (!S.pages.length || PV.size !== S.pages.length) return buildViewer();
  const viewer = $('#viewer'); const ratio = viewer.scrollHeight ? viewer.scrollTop / viewer.scrollHeight : 0;
  if (S.fit) S.scale = await computeFitScale();
  updZoom();
  for (const pe of S.pages) {
    const v = PV.get(pe.id); if (!v) return buildViewer();
    if (v.task) try { v.task.cancel(); } catch {}
    v.pv = v.pp.getViewport({ scale: S.scale, rotation: pageRotation(v.pp, pe) });
    v.div.style.width = v.pv.width + 'px'; v.div.style.height = v.pv.height + 'px';
    v.tl.innerHTML = ''; v.tlBuilt = false;
    renderOverlays(pe.id);
    if (v.visible) { if (!rq.includes(pe.id)) rq.push(pe.id); if (S.mode === 'edit') buildTextLayer(pe.id); }
  }
  viewer.scrollTop = ratio * viewer.scrollHeight;
  if (S.sel) showProps();
  pump();
}
const rq = []; let rBusy = false;
function onVisible(entries) {
  for (const en of entries) {
    const pid = en.target.dataset.pid; const v = PV.get(pid); if (!v) continue;
    v.visible = en.isIntersecting;
    if (en.isIntersecting) { if (v.renderedScale !== S.scale) { if (!rq.includes(pid)) rq.push(pid); pump(); } if (S.mode === 'edit') buildTextLayer(pid); }
  }
}
async function pump() {
  if (rBusy) return; rBusy = true;
  while (rq.length) {
    const pid = rq.shift(); const v = PV.get(pid); if (!v || !v.visible || v.renderedScale === S.scale) continue;
    let k = DPR; const px = v.pv.width * v.pv.height * k * k; if (px > MAXPIX) k = Math.sqrt(MAXPIX / (v.pv.width * v.pv.height));
    const c = document.createElement('canvas'); c.width = Math.floor(v.pv.width * k); c.height = Math.floor(v.pv.height * k);
    const ctx = c.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);
    try {
      v.task = v.pp.render({ canvasContext: ctx, viewport: v.pv, transform: k !== 1 ? [k, 0, 0, k, 0, 0] : undefined });
      await v.task.promise;
      if (PV.get(pid) === v && v.pv.scale === S.scale) { c.className = 'pc'; v.canvas.replaceWith(c); v.canvas = c; v.renderedScale = S.scale; v.k = k; }
    } catch (e) { if (e && e.name !== 'RenderingCancelledException') console.warn(e); }
  }
  rBusy = false;
}

/* ---------- миниатюры ---------- */
const thumbCache = new Map(); // key -> dataURL
const picked = new Set();
let sortable = null;
function buildThumbs() {
  const host = $('#thumbs'); host.innerHTML = ''; if (tio) tio.disconnect();
  tio = new IntersectionObserver(onThumbVisible, { root: $('#thumbsWrap'), rootMargin: '400px 0px' });
  S.pages.forEach((pe, i) => {
    const d = document.createElement('div'); d.className = 'thumb' + (picked.has(pe.id) ? ' picked' : ''); d.dataset.pid = pe.id;
    d.innerHTML = `<input type="checkbox" class="chk" ${picked.has(pe.id) ? 'checked' : ''} title="Выбрать лист"><div class="tools"><button data-a="rot" title="Повернуть">↻</button><button data-a="del" title="Удалить лист">✕</button></div><canvas width="10" height="14"></canvas><div class="n">${i + 1}</div>`;
    d.addEventListener('click', (e) => {
      const a = e.target.dataset && e.target.dataset.a;
      if (e.target.classList.contains('chk')) { e.target.checked ? picked.add(pe.id) : picked.delete(pe.id); d.classList.toggle('picked', e.target.checked); updSelActs(); return; }
      if (a === 'rot') return rotatePages([pe.id]);
      if (a === 'del') return deletePages([pe.id]);
      scrollToPage(pe.id);
    });
    host.append(d); tio.observe(d);
  });
  $('#pcount').textContent = S.pages.length ? `Листов: ${S.pages.length}  ·  перетаскивай, чтобы поменять порядок` : 'Листы';
  if (sortable) sortable.destroy();
  sortable = Sortable.create(host, { animation: 150, ghostClass: 'ghost', filter: '.chk,.tools button', preventOnFilter: false, onEnd: onReorder });
  for (const id of [...picked]) if (!S.pages.find(p => p.id === id)) picked.delete(id);
  updSelActs();
}
async function onThumbVisible(entries) {
  for (const en of entries) {
    if (!en.isIntersecting) continue; tio.unobserve(en.target);
    const pe = S.pages.find(p => p.id === en.target.dataset.pid); if (!pe) continue;
    const key = pe.src + ':' + pe.idx + ':' + pe.rot; const cv = en.target.querySelector('canvas');
    try {
      let url = thumbCache.get(key);
      if (!url) {
        const pp = await getPdfPage(pe.src, pe.idx); const v1 = pp.getViewport({ scale: 1, rotation: pageRotation(pp, pe) });
        const sc = 300 / Math.max(v1.width, v1.height); const vp = pp.getViewport({ scale: sc, rotation: pageRotation(pp, pe) });
        const c = document.createElement('canvas'); c.width = Math.ceil(vp.width); c.height = Math.ceil(vp.height);
        const ctx = c.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);
        await pp.render({ canvasContext: ctx, viewport: vp }).promise; url = c.toDataURL('image/jpeg', .8); thumbCache.set(key, url);
      }
      const im = new Image(); im.onload = () => { cv.width = im.width; cv.height = im.height; cv.style.maxHeight = '150px'; cv.style.width = 'auto'; cv.getContext('2d').drawImage(im, 0, 0); }; im.src = url;
    } catch (e) { console.warn(e); }
  }
}
function onReorder() {
  const ids = [...$('#thumbs').children].map(d => d.dataset.pid);
  const map = Object.fromEntries(S.pages.map(p => [p.id, p]));
  const np = ids.map(id => map[id]);
  if (np.map(p => p.id).join() === S.pages.map(p => p.id).join()) return;
  S.pages = np; commit(true); rebuildAll();
}
function updSelActs() { $('#selActs').style.display = picked.size ? 'flex' : 'none'; if (picked.size) $('#sDel').textContent = `Удалить (${picked.size})`; }
function rotatePages(ids) { for (const pe of S.pages) if (ids.includes(pe.id)) pe.rot = (pe.rot + 90) % 360; commit(true); rebuildAll(); }
function deletePages(ids) {
  if (ids.length === S.pages.length && !confirm('Удалить все листы?')) return;
  S.pages = S.pages.filter(p => !ids.includes(p.id)); S.overlays = S.overlays.filter(o => !ids.includes(o.page));
  ids.forEach(i => picked.delete(i)); commit(true); rebuildAll();
}
function scrollToPage(pid) { const v = PV.get(pid); if (v) $('#viewer').scrollTo({ top: v.div.offsetTop - 12, behavior: 'smooth' }); }
function trackCurrent() {
  const vw = $('#viewer'); const mid = vw.scrollTop + vw.clientHeight / 3; let cur = null;
  for (const [pid, v] of PV) { if (v.div.offsetTop <= mid) cur = pid; else break; }
  for (const t of $('#thumbs').children) t.classList.toggle('cur', t.dataset.pid === cur);
}

/* ---------- слой правки текста ---------- */
async function buildTextLayer(pid) {
  const v = PV.get(pid); if (!v || v.tlBuilt) return; v.tlBuilt = true;
  try {
    const tc = await v.pp.getTextContent(); v.tc = tc;
    if (PV.get(pid) !== v) return;
    const frag = document.createDocumentFragment();
    tc.items.forEach((it, i) => {
      if (!it.str || !it.str.trim()) return;
      const g = itemGeom(it, 0); if (!g) return;
      const sp = document.createElement('div'); sp.className = 'tspan'; sp.dataset.i = i; placeBox(sp, g, v.pv);
      sp.title = 'Кликни, чтобы изменить';
      frag.append(sp);
    });
    v.tl.append(frag); refreshTextLayerMarks(pid);
    v.tl.onclick = (e) => { const sp = e.target.closest('.tspan'); if (sp) startEditItem(pid, +sp.dataset.i); };
  } catch (e) { console.warn(e); }
}
function refreshTextLayerMarks(pid) {
  const v = PV.get(pid); if (!v) return;
  const done = new Set(S.overlays.filter(o => o.page === pid && o.orig != null).map(o => o.orig));
  for (const sp of v.tl.children) sp.style.display = done.has(+sp.dataset.i) ? 'none' : '';
}
function itemGeom(it, pad) {
  const [a, b, c, d, e, f] = it.transform; const ang = Math.atan2(b, a);
  let fh = Math.hypot(c, d) || it.height; if (!fh || !isFinite(fh)) return null;
  const w = it.width || 0; if (w <= 0) return null;
  const co = Math.cos(ang), si = Math.sin(ang); const up = [-si, co], dir = [co, si];
  const down = 0.24 * fh, padx = pad ? 0.6 : 0;
  return { x: e - down * up[0] - padx * dir[0], y: f - down * up[1] - padx * dir[1], w: w + 2 * padx, h: fh * (pad ? 1.3 : 1.22), a: ang };
}
function sampleColors(v, g) {
  // самый светлый (фон) и самый тёмный (текст) пиксель в области
  try {
    const { P0, P1, P3 } = corners(g); const k = v.k || 1;
    const pts = [toV(v.pv, P0), toV(v.pv, P1), toV(v.pv, P3)];
    const xs = pts.map(p => p[0] * k), ys = pts.map(p => p[1] * k);
    const P2 = [pts[1][0] + pts[2][0] - pts[0][0], pts[1][1] + pts[2][1] - pts[0][1]]; xs.push(P2[0] * k); ys.push(P2[1] * k);
    const x0 = Math.max(0, Math.floor(Math.min(...xs))), y0 = Math.max(0, Math.floor(Math.min(...ys)));
    const x1 = Math.min(v.canvas.width, Math.ceil(Math.max(...xs))), y1 = Math.min(v.canvas.height, Math.ceil(Math.max(...ys)));
    if (x1 <= x0 || y1 <= y0) return null;
    const data = v.canvas.getContext('2d').getImageData(x0, y0, x1 - x0, y1 - y0).data;
    let best = -1, bc = [255, 255, 255], worst = 999, wc = [0, 0, 0];
    const step = Math.max(1, Math.floor(Math.sqrt((data.length / 4) / 6000)));
    for (let y = 0; y < y1 - y0; y += step) for (let x = 0; x < x1 - x0; x += step) {
      const i = (y * (x1 - x0) + x) * 4; const l = data[i] * .3 + data[i + 1] * .59 + data[i + 2] * .11;
      if (l > best) { best = l; bc = [data[i], data[i + 1], data[i + 2]]; }
      if (l < worst) { worst = l; wc = [data[i], data[i + 1], data[i + 2]]; }
    }
    const hex = (c) => '#' + c.map(x => x.toString(16).padStart(2, '0')).join('');
    const sat = Math.max(...wc) - Math.min(...wc);
    return { bg: best > 225 ? '#ffffff' : hex(bc), ink: worst < 170 && sat > 50 ? hex(wc) : '#000000' };
  } catch { return null; }
}
function guessFont(v, it) {
  let fam = 'serif', bold = false;
  try {
    const st = v.tc.styles[it.fontName]; if (st && /sans/i.test(st.fontFamily)) fam = 'sans';
    let nm = ''; try { const fo = v.pp.commonObjs.get(it.fontName); nm = (fo && (fo.name || fo.loadedName)) || ''; } catch {}
    if (/arial|helvet|sans|verdana|tahoma|calibri|segoe|gost|isocpeur|roboto|pt.?sans/i.test(nm)) fam = 'sans';
    if (/times|serif|georgia|cambria|garamond/i.test(nm) && !/sans/i.test(nm)) fam = 'serif';
    if (/bold|black|heavy|semibold|,b\b/i.test(nm)) bold = true;
  } catch {}
  return { fam, bold };
}
function startEditItem(pid, i) {
  const v = PV.get(pid); const it = v.tc.items[i];
  const gw = itemGeom(it, 1); const gt = itemGeom(it, 0); if (!gw) return;
  const col = sampleColors(v, gt) || { bg: '#ffffff', ink: '#000000' };
  const { fam, bold } = guessFont(v, it);
  const fh = Math.hypot(it.transform[2], it.transform[3]) || it.height;
  const ang = gw.a, up = [-Math.sin(ang), Math.cos(ang)];
  const m = famMetrics(fam); const off = m.bottom * fh; const B = [it.transform[4], it.transform[5]];
  const white = { id: nid('o'), page: pid, type: 'white', x: gw.x, y: gw.y, w: gw.w, h: gw.h, a: ang, bg: col.bg, orig: i };
  const txt = { id: nid('o'), page: pid, type: 'text', x: B[0] - off * up[0], y: B[1] - off * up[1], w: it.width, h: LH * fh, a: ang, text: it.str, size: +fh.toFixed(2), fam, bold, color: col.ink, link: white.id };
  S.overlays.push(white, txt); commit();
  renderOverlays(pid); refreshTextLayerMarks(pid);
  select(txt.id); setEditing(txt.id, true);
}

/* ---------- объекты поверх листа ---------- */
function renderAllOverlays() { for (const pid of PV.keys()) { renderOverlays(pid); if (PV.get(pid).tlBuilt) refreshTextLayerMarks(pid); } }
function renderOverlays(pid) {
  const v = PV.get(pid); if (!v) return; v.lay.innerHTML = '';
  for (const o of S.overlays) if (o.page === pid) v.lay.append(makeOvEl(o, v));
}
function makeOvEl(o, v) {
  const el = document.createElement('div'); el.className = 'ov ' + o.type + (S.sel === o.id ? ' sel' : ''); el.dataset.id = o.id;
  if (o.type === 'img') { const im = document.createElement('img'); im.src = S.imgs[o.img].data; im.draggable = false; el.append(im); }
  if (o.type === 'white') el.style.background = o.bg || '#fff';
  if (o.type === 'text') {
    el.textContent = o.text; el.style.fontFamily = `"${FONTFACE[o.fam]}"`; el.style.fontWeight = o.bold ? '700' : '400';
    el.style.fontSize = (o.size * v.pv.scale) + 'px'; el.style.lineHeight = LH; el.style.color = o.color || '#000';
  }
  placeBox(el, o, v.pv);
  if (o.type === 'text') { el.style.width = 'auto'; el.style.minWidth = '6px'; }
  if (o.type === 'img') { const rs = document.createElement('div'); rs.className = 'rs'; el.append(rs); }
  el.addEventListener('pointerdown', (e) => onOvDown(e, o.id));
  el.addEventListener('dblclick', (e) => { if (o.type === 'text') { e.stopPropagation(); setEditing(o.id, true); } });
  return el;
}
function ovEl(id) { return document.querySelector(`.ov[data-id="${id}"]`); }
function ov(id) { return S.overlays.find(o => o.id === id); }
function select(id) {
  S.sel = id; document.querySelectorAll('.ov.sel').forEach(e => e.classList.remove('sel'));
  if (id) { const el = ovEl(id); if (el) el.classList.add('sel'); showProps(); } else hideProps();
}
let editingId = null;
function setEditing(id, on) {
  const el = ovEl(id); const o = ov(id); if (!el || !o) return;
  if (on) {
    if (editingId && editingId !== id) setEditing(editingId, false);
    editingId = id; o._t0 = o.text; el.setAttribute('contenteditable', 'plaintext-only'); el.focus();
    const r = document.createRange(); r.selectNodeContents(el); const s = getSelection(); s.removeAllRanges(); s.addRange(r);
    el.oninput = () => { o.text = el.innerText.replace(/\n$/, ''); fitTextBox(o, el); };
    el.onblur = () => setEditing(id, false);
    el.onkeydown = (e) => { if (e.key === 'Escape') { e.preventDefault(); el.blur(); } e.stopPropagation(); };
  } else {
    if (editingId === id) editingId = null;
    el.removeAttribute('contenteditable'); el.oninput = el.onblur = el.onkeydown = null;
    o.text = el.innerText.replace(/\n$/, '');
    if (!o.text.trim()) { S.overlays = S.overlays.filter(x => x.id !== id); el.remove(); hideProps(); S.sel = null; }
    else fitTextBox(o, el);
    const changed = o._t0 !== o.text; delete o._t0;
    if (changed || !H.stack[H.i] || !H.stack[H.i].s.includes(o.id)) commit();
  }
}
function fitTextBox(o, el) {
  const v = PV.get(o.page); if (!v) return;
  const lines = (o.text.match(/\n/g) || []).length + 1;
  const oldH = o.h; o.h = LH * o.size * lines; o.w = Math.max(4, el.offsetWidth / v.pv.scale);
  // держим верхний край на месте
  const dh = o.h - oldH; o.x += dh * Math.sin(o.a); o.y -= dh * Math.cos(o.a);
  const w = el.style.width; placeBox(el, o, v.pv); el.style.width = 'auto'; void w;
  if (S.sel === o.id) showProps();
}

/* ---------- мышь: перемещение, размер, клики по листу ---------- */
let drag = null;
function onOvDown(e, id) {
  if (e.button !== 0) return; const o = ov(id); if (!o) return;
  const el = ovEl(id);
  if (el && el.getAttribute('contenteditable')) return; // идёт ввод текста
  e.stopPropagation();
  if (S.mode === 'edit' && o.type === 'text') { select(id); setEditing(id, true); return; }
  const wasSel = S.sel === id; select(id);
  const v = PV.get(o.page); const resize = e.target.classList.contains('rs');
  drag = { id, v, resize, sx: e.clientX, sy: e.clientY, o0: { ...o }, moved: false, wasSel };
  el.setPointerCapture(e.pointerId);
  el.onpointermove = onDragMove; el.onpointerup = onDragUp;
}
function onDragMove(e) {
  if (!drag) return; const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
  if (!drag.moved && Math.hypot(dx, dy) < 3) return; drag.moved = true;
  const o = ov(drag.id); const pv = drag.v.pv; const o0 = drag.o0;
  if (drag.resize) {
    const th = screenAngle(o0, pv); // проекция смещения на ширину
    const proj = dx * Math.cos(th) + dy * Math.sin(th);
    const W0 = o0.w * pv.scale; const W1 = Math.max(12, W0 + proj); const k = W1 / W0;
    // верхний левый угол неподвижен
    const { P3 } = corners(o0); o.w = o0.w * k; o.h = o0.h * k;
    o.x = P3[0] + o.h * Math.sin(o.a); o.y = P3[1] - o.h * Math.cos(o.a);
  } else {
    const V0 = toV(pv, [o0.x, o0.y]); const P = toP(pv, [V0[0] + dx, V0[1] + dy]); o.x = P[0]; o.y = P[1];
  }
  const el = ovEl(o.id); placeBox(el, o, pv); if (o.type === 'text') el.style.width = 'auto';
  hideProps();
}
function onDragUp(e) {
  const el = ovEl(drag.id); if (el) { el.onpointermove = el.onpointerup = null; try { el.releasePointerCapture(e.pointerId); } catch {} }
  const o = ov(drag.id);
  if (drag.moved) commit(); else if (o && o.type === 'text' && S.sel === o.id && drag.wasSel) setEditing(o.id, true);
  drag = null; showProps();
}
function onLayerDown(e, pid) {
  if (e.button !== 0 || e.target.closest('.ov') || S.mode === 'edit') return;
  const v = PV.get(pid); const r = v.div.getBoundingClientRect(); const x = e.clientX - r.left, y = e.clientY - r.top;
  if (S.mode === 'stamp' && pickedLib) {
    const im = S.imgs[pickedLib]; const aspect = im.h / im.w;
    const wpt = aspect < 0.5 ? 130 : 115; const W = wpt * v.pv.scale, Hh = W * aspect;
    const g = geomFromScreen(v.pv, x - W / 2, y - Hh / 2, W, Hh);
    const o = { id: nid('o'), page: pid, type: 'img', img: pickedLib, ...g };
    S.overlays.push(o); commit(); renderOverlays(pid); select(o.id); e.preventDefault(); return;
  }
  if (S.mode === 'addtext') {
    const size = lastText.size, fam = lastText.fam; const Hh = LH * size * v.pv.scale;
    const g = geomFromScreen(v.pv, x, y - Hh * 0.6, 10, Hh);
    const o = { id: nid('o'), page: pid, type: 'text', ...g, text: ' ', size, fam, bold: lastText.bold, color: lastText.color };
    S.overlays.push(o); renderOverlays(pid); select(o.id); e.preventDefault();
    setTimeout(() => setEditing(o.id, true), 0); return;
  }
  select(null);
}
const lastText = { size: 12, fam: 'serif', bold: false, color: '#000000' };

/* ---------- панель свойств ---------- */
function hideProps() { $('#props').style.display = 'none'; }
function showProps() {
  const o = ov(S.sel); const el = ovEl(S.sel); const p = $('#props'); if (!o || !el) return hideProps();
  let h = '';
  if (o.type === 'text') {
    h += `<button data-p="sz-">A−</button><span class="sz">${(+o.size).toFixed(o.size % 1 ? 1 : 0)}</span><button data-p="sz+">A+</button>`;
    h += `<button data-p="fam">${o.fam === 'serif' ? 'С засечками' : 'Без засечек'}</button>`;
    h += `<button data-p="bold" style="font-weight:700;${o.bold ? 'background:#5b6a8c' : ''}">Ж</button>`;
    for (const c of ['#000000', '#1a3fa3', '#c0262d']) h += `<button class="col" data-p="col" data-c="${c}" style="background:${c}"></button>`;
    h += `<button data-p="dup" title="Копия">⧉</button>`;
  }
  if (o.type === 'img') h += `<button data-p="all">На все листы</button><button data-p="dup" title="Копия">⧉</button>`;
  if (o.type === 'white') h += `<span style="padding:0 6px;font-size:12px">Закраска</span>`;
  h += `<button data-p="del" title="Удалить (Delete)">${o.type === 'white' ? 'Вернуть исходный текст' : 'Удалить'}</button>`;
  p.innerHTML = h; p.style.display = 'flex';
  const r = el.getBoundingClientRect(); const pr = p.getBoundingClientRect();
  let top = r.top - pr.height - 8; if (top < 60) top = r.bottom + 8;
  p.style.top = top + 'px'; p.style.left = Math.max(8, Math.min(innerWidth - pr.width - 8, r.left)) + 'px';
}
$('#props').addEventListener('pointerdown', (e) => e.preventDefault()); // не терять фокус при правке
$('#props').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return; const o = ov(S.sel); if (!o) return; const a = b.dataset.p;
  if (a === 'del') { delOverlay(o.id); return; }
  if (a === 'sz-' || a === 'sz+') { const k = a === 'sz+' ? 1.08 : 1 / 1.08; o.size = Math.max(3, Math.round(o.size * k * 10) / 10); lastText.size = o.size; }
  if (a === 'fam') { o.fam = o.fam === 'serif' ? 'sans' : 'serif'; lastText.fam = o.fam; }
  if (a === 'bold') { o.bold = !o.bold; lastText.bold = o.bold; }
  if (a === 'col') { o.color = b.dataset.c; lastText.color = o.color; }
  if (a === 'dup') { const c = { ...o, id: nid('o'), x: o.x + 10, y: o.y - 10, orig: undefined, link: undefined }; S.overlays.push(c); commit(); renderOverlays(o.page); select(c.id); return; }
  if (a === 'all') {
    for (const pe of S.pages) if (pe.id !== o.page) S.overlays.push({ ...o, id: nid('o'), page: pe.id });
    commit(); renderAllOverlays(); toast('Поставлено на все листы'); select(o.id); return;
  }
  const wasEditing = editingId === o.id;
  if (o.type === 'text') { const el = ovEl(o.id); const v = PV.get(o.page); el.style.fontSize = (o.size * v.pv.scale) + 'px'; el.style.fontFamily = `"${FONTFACE[o.fam]}"`; el.style.fontWeight = o.bold ? '700' : '400'; el.style.color = o.color; fitTextBox(o, el); }
  if (!wasEditing) commit();
  showProps();
});
function delOverlay(id) {
  const o = ov(id); if (!o) return;
  S.overlays = S.overlays.filter(x => x.id !== id);
  if (o.type === 'white') S.overlays = S.overlays.filter(x => x.link !== id); // вернуть исходный текст
  S.sel = null; hideProps(); commit(); renderOverlays(o.page); refreshTextLayerMarks(o.page);
}

/* ---------- режимы ---------- */
function setMode(m) {
  if (editingId) setEditing(editingId, false);
  select(null);
  S.mode = m; document.body.className = 'm-' + m;
  for (const b of document.querySelectorAll('[data-mode]')) b.classList.toggle('on', b.dataset.mode === m);
  $('#main').classList.toggle('side', m === 'stamp');
  if (m === 'edit') { for (const [pid, v] of PV) if (v.visible) buildTextLayer(pid); toast('Кликни по тексту на листе, чтобы его изменить'); }
  if (m === 'addtext') toast('Кликни в место на листе, где нужен текст');
  const nowSide = m === 'stamp';
  if (nowSide !== !!setMode._side && S.fit && S.pages.length) setTimeout(() => rescaleViewer(), 0);
  setMode._side = nowSide;
}

/* ---------- библиотека подписей и печатей ---------- */
let pickedLib = null;
function libLoad() {
  try { const arr = JSON.parse(lsGet('pdfm.lib') || '[]'); for (const it of arr) S.imgs[it.id] = it; } catch {}
  renderLib();
}
function libSave() {
  const arr = Object.values(S.imgs).filter(i => i.lib);
  if (!lsSet('pdfm.lib', JSON.stringify(arr))) toast('Не хватило места, чтобы запомнить картинку надолго — она будет только до закрытия');
}
function renderLib() {
  const host = $('#lib'); host.innerHTML = '';
  for (const it of Object.values(S.imgs).filter(i => i.lib)) {
    const d = document.createElement('div'); d.className = 'li' + (pickedLib === it.id ? ' pick' : '');
    d.innerHTML = `<img src="${it.data}"><button class="x" title="Убрать из списка">✕</button>`;
    d.onclick = (e) => {
      if (e.target.classList.contains('x')) { it.lib = false; if (pickedLib === it.id) pickedLib = null; libSave(); renderLib(); return; }
      pickedLib = pickedLib === it.id ? null : it.id; renderLib(); if (pickedLib) toast('Теперь кликни на лист, куда поставить');
    };
    host.append(d);
  }
  if (!host.children.length) host.innerHTML = '<p class="hint" style="grid-column:1/3">Пока пусто. Загрузи скан подписи или печати (лучше на белом фоне) или нарисуй подпись.</p>';
}
function addLibImage(canvas) {
  const t = trimCanvas(canvas); if (!t) { toast('Картинка пустая'); return; }
  const id = nid('i'); S.imgs[id] = { id, data: t.toDataURL('image/png'), w: t.width, h: t.height, lib: true };
  pickedLib = id; libSave(); renderLib(); toast('Добавлено. Кликни на лист, куда поставить');
}
function trimCanvas(c) {
  const ctx = c.getContext('2d'); const { width: w, height: h } = c; const d = ctx.getImageData(0, 0, w, h).data;
  let x0 = w, y0 = h, x1 = -1, y1 = -1;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (d[(y * w + x) * 4 + 3] > 8) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  if (x1 < 0) return null; const pad = 4;
  x0 = Math.max(0, x0 - pad); y0 = Math.max(0, y0 - pad); x1 = Math.min(w - 1, x1 + pad); y1 = Math.min(h - 1, y1 + pad);
  const o = document.createElement('canvas'); o.width = x1 - x0 + 1; o.height = y1 - y0 + 1; o.getContext('2d').drawImage(c, x0, y0, o.width, o.height, 0, 0, o.width, o.height); return o;
}
async function importImageFile(file) {
  const url = URL.createObjectURL(file); const im = new Image(); im.src = url; await im.decode();
  const k = Math.min(1, 1600 / Math.max(im.naturalWidth, im.naturalHeight));
  const c = document.createElement('canvas'); c.width = Math.round(im.naturalWidth * k); c.height = Math.round(im.naturalHeight * k);
  const ctx = c.getContext('2d'); ctx.drawImage(im, 0, 0, c.width, c.height); URL.revokeObjectURL(url);
  if ($('#cWhite').checked) removeWhite(c);
  addLibImage(c);
}
function removeWhite(c) {
  const ctx = c.getContext('2d'); const id = ctx.getImageData(0, 0, c.width, c.height); const d = id.data;
  const HI = 232, LO = 150;
  for (let i = 0; i < d.length; i += 4) {
    const r = d[i], g = d[i + 1], b = d[i + 2]; const wv = Math.min(r, g, b);
    let a = wv >= HI ? 0 : wv <= LO ? 1 : (HI - wv) / (HI - LO);
    if (a > 0 && a < 1) { d[i] = Math.max(0, Math.min(255, (r - 255 * (1 - a)) / a)); d[i + 1] = Math.max(0, Math.min(255, (g - 255 * (1 - a)) / a)); d[i + 2] = Math.max(0, Math.min(255, (b - 255 * (1 - a)) / a)); }
    d[i + 3] = Math.round(d[i + 3] * a);
  }
  ctx.putImageData(id, 0, 0);
}
// рисование подписи
const sig = { c: null, ctx: null, drawing: false, color: '#1a3fa3', last: null, mid: null };
function openSig() {
  $('#modal').style.display = 'flex'; sig.c = $('#sigc'); const k = 2;
  sig.c.width = 560 * k; sig.c.height = 220 * k; sig.c.style.width = '560px'; sig.c.style.height = '220px';
  sig.ctx = sig.c.getContext('2d'); sig.ctx.scale(k, k); sig.ctx.lineCap = 'round'; sig.ctx.lineJoin = 'round';
  sig.ctx.lineWidth = 2.4; sig.ctx.strokeStyle = sig.color;
}
function sigPt(e) { const r = sig.c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
$('#sigc').addEventListener('pointerdown', (e) => { sig.drawing = true; sig.c.setPointerCapture(e.pointerId); sig.last = sigPt(e); sig.mid = sig.last; sig.ctx.strokeStyle = sig.color; });
$('#sigc').addEventListener('pointermove', (e) => {
  if (!sig.drawing) return; const p = sigPt(e); const m = [(sig.last[0] + p[0]) / 2, (sig.last[1] + p[1]) / 2];
  sig.ctx.beginPath(); sig.ctx.moveTo(sig.mid[0], sig.mid[1]); sig.ctx.quadraticCurveTo(sig.last[0], sig.last[1], m[0], m[1]); sig.ctx.stroke();
  sig.last = p; sig.mid = m;
});
$('#sigc').addEventListener('pointerup', () => { sig.drawing = false; });
document.querySelectorAll('.sc').forEach(b => b.onclick = () => { sig.color = b.dataset.c; });
$('#sigClear').onclick = () => sig.ctx.clearRect(0, 0, 560, 220);
$('#sigCancel').onclick = () => $('#modal').style.display = 'none';
$('#sigOk').onclick = () => { $('#modal').style.display = 'none'; addLibImage(sig.c); };

/* ---------- сохранение ---------- */
async function getLibDoc(src) {
  const s = S.sources[src];
  if (!s.lib) {
    s.lib = await PDFDocument.load(s.bytes, { ignoreEncryption: true, updateMetadata: false });
    if (s.lib.isEncrypted && !confirm('Файл «' + s.name + '» защищён от изменений. Сохранённая копия может открыться с ошибками. Всё равно сохранить?')) { s.lib = null; throw new Error('Отменено'); }
  }
  return s.lib;
}
function dataUrlBytes(u) { return b64ToBytes(u.split(',')[1]); }
async function buildPdf(pageIds) {
  const pages = pageIds ? S.pages.filter(p => pageIds.includes(p.id)) : S.pages;
  if (!pages.length) throw new Error('Нет листов');
  const out = await PDFDocument.create(); out.registerFontkit(fontkit);
  const bySrc = {}; pages.forEach((p, i) => (bySrc[p.src] = bySrc[p.src] || []).push(i));
  const copied = new Array(pages.length);
  for (const src of Object.keys(bySrc)) {
    const lib = await getLibDoc(src); const idxs = bySrc[src];
    const cp = await out.copyPages(lib, idxs.map(i => pages[i].idx)); idxs.forEach((pi, k) => copied[pi] = cp[k]);
  }
  const fonts = {}, imgs = {};
  const getFont = async (fam, bold) => { const k = fam + (bold ? 'b' : 'r'); if (!fonts[k]) fonts[k] = await out.embedFont(b64ToBytes(FONTDATA[fam][bold ? 'b' : 'r']), { subset: false }); return fonts[k]; };
  const getImg = async (id) => { if (!imgs[id]) imgs[id] = await out.embedPng(dataUrlBytes(S.imgs[id].data)); return imgs[id]; };
  for (let i = 0; i < pages.length; i++) {
    const pe = pages[i]; const pg = out.addPage(copied[i]);
    const base = pg.getRotation().angle || 0; if (pe.rot) pg.setRotation(degrees((base + pe.rot) % 360));
    const list = S.overlays.filter(o => o.page === pe.id);
    // сначала закраски, потом картинки и текст
    list.sort((a, b) => (a.type === 'white' ? 0 : 1) - (b.type === 'white' ? 0 : 1));
    for (const o of list) {
      const rot = degrees(o.a * 180 / Math.PI);
      if (o.type === 'white') { const [r, g, b] = hexToRgb(o.bg || '#ffffff'); pg.drawRectangle({ x: o.x, y: o.y, width: o.w, height: o.h, rotate: rot, color: rgb(r / 255, g / 255, b / 255), borderWidth: 0 }); }
      if (o.type === 'img') pg.drawImage(await getImg(o.img), { x: o.x, y: o.y, width: o.w, height: o.h, rotate: rot });
      if (o.type === 'text') {
        const font = await getFont(o.fam, o.bold); const cs = new Set(font.getCharacterSet());
        const lines = o.text.split('\n'); const m = famMetrics(o.fam);
        const up = [-Math.sin(o.a), Math.cos(o.a)];
        const [r, g, b] = hexToRgb(o.color || '#000000');
        lines.forEach((ln, li) => {
          const t = [...ln].map(ch => cs.has(ch.codePointAt(0)) ? ch : (ch === '\t' ? ' ' : '?')).join('');
          const fromBottom = (lines.length - 1 - li) * LH * o.size + m.bottom * o.size;
          const bx = o.x + fromBottom * up[0], by = o.y + fromBottom * up[1];
          if (t) pg.drawText(t, { x: bx, y: by, size: o.size, font, rotate: rot, color: rgb(r / 255, g / 255, b / 255) });
        });
      }
    }
  }
  out.setProducer('PDF-мастерская'); out.setCreator('PDF-мастерская');
  return await out.save({ useObjectStreams: true });
}
function suggestName(suffix = '') { const n = (S.fileName || 'документ.pdf').replace(/\.pdf$/i, ''); return n + suffix + '.pdf'; }
async function writeToHandle(h, bytes) { const w = await h.createWritable(); await w.write(bytes); await w.close(); }
function download(bytes, name) {
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
  const a = document.createElement('a'); a.href = url; a.download = name; document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
async function save(asNew, pageIds, suffix) {
  if (!S.pages.length) return;
  if (editingId) setEditing(editingId, false);
  let bytes;
  try { busy(true); bytes = await buildPdf(pageIds); }
  catch (e) { busy(false); console.error(e); alert('Не получилось сохранить: ' + (e.message || e)); return; }
  try {
    if (!asNew && S.fileHandle) {
      await writeToHandle(S.fileHandle, bytes); busy(false); setDirty(false); toast('Сохранено: ' + S.fileName); return;
    }
    if (window.showSaveFilePicker) {
      busy(false);
      const h = await showSaveFilePicker({ suggestedName: suggestName(suffix || (asNew ? '' : '')), types: [{ description: 'PDF', accept: { 'application/pdf': ['.pdf'] } }] });
      busy(true); await writeToHandle(h, bytes); busy(false);
      if (!pageIds) { S.fileHandle = h; S.fileName = h.name; setDirty(false); }
      toast('Сохранено: ' + h.name); return;
    }
    busy(false); download(bytes, suggestName(suffix || '_изм')); if (!pageIds) setDirty(false); toast('Файл сохранён в «Загрузки»');
  } catch (e) { busy(false); if (e.name !== 'AbortError') { console.error(e); alert('Не получилось записать файл: ' + (e.message || e)); } }
}

/* ---------- масштаб ---------- */
function updZoom() { $('#zoomv').textContent = Math.round(S.scale * 100) + '%'; }
function zoom(k) { S.fit = false; S.scale = Math.max(0.15, Math.min(6, S.scale * k)); rescaleViewer(); }

/* ---------- кнопки и клавиши ---------- */
$('#bOpen').onclick = $('#bOpen2').onclick = () => pickOpen(true);
$('#bAdd').onclick = () => S.pages.length ? pickOpen(false) : pickOpen(true);
$('#fOpen').onchange = (e) => { openFiles(e.target.files, { replace: true }); e.target.value = ''; };
$('#fAdd').onchange = (e) => { openFiles(e.target.files, { replace: !S.pages.length }); e.target.value = ''; };
$('#bSave').onclick = () => save(false);
$('#bSaveAs').onclick = () => save(true);
$('#bUndo').onclick = undo; $('#bRedo').onclick = redo;
$('#zIn').onclick = () => zoom(1.2); $('#zOut').onclick = () => zoom(1 / 1.2); $('#zFit').onclick = () => { S.fit = true; rescaleViewer(); };
for (const b of document.querySelectorAll('[data-mode]')) b.onclick = () => setMode(S.mode === b.dataset.mode && b.dataset.mode !== 'select' ? 'select' : b.dataset.mode);
$('#bImg').onclick = () => $('#fImg').click();
$('#fImg').onchange = async (e) => { for (const f of e.target.files) await importImageFile(f); e.target.value = ''; };
$('#bDraw').onclick = openSig;
$('#sRot').onclick = () => rotatePages([...picked]);
$('#sDel').onclick = () => deletePages([...picked]);
$('#sExt').onclick = () => save(true, [...picked], '_листы');
$('#sNone').onclick = () => { picked.clear(); buildThumbs(); };
$('#viewer').addEventListener('scroll', () => { trackCurrent(); if (S.sel) hideProps(); clearTimeout(trackCurrent._t); trackCurrent._t = setTimeout(() => S.sel && showProps(), 150); });
window.addEventListener('resize', () => { clearTimeout(window._rz); window._rz = setTimeout(() => { if (S.fit) rescaleViewer(); }, 200); });
window.addEventListener('keydown', (e) => {
  const mod = e.metaKey || e.ctrlKey; const k = e.key.toLowerCase();
  if (mod && (k === 's' || k === 'ы')) { e.preventDefault(); save(e.shiftKey); return; }
  if (mod && (k === 'o' || k === 'щ')) { e.preventDefault(); pickOpen(true); return; }
  if (editingId) return;
  if (mod && (k === 'z' || k === 'я')) { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if ((e.key === 'Delete' || e.key === 'Backspace') && S.sel && !/input|textarea/i.test(document.activeElement.tagName)) { e.preventDefault(); delOverlay(S.sel); return; }
  if (e.key === 'Escape') { select(null); setMode('select'); }
});
window.addEventListener('beforeunload', (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ''; } });
// перетаскивание файлов
let dragN = 0;
window.addEventListener('dragenter', (e) => { if ([...(e.dataTransfer.types || [])].includes('Files')) { dragN++; $('#drop').style.display = 'flex'; $('#drop').textContent = S.pages.length ? 'Отпусти — файл добавится в конец' : 'Отпусти файл, чтобы открыть'; } });
window.addEventListener('dragleave', () => { if (--dragN <= 0) { dragN = 0; $('#drop').style.display = 'none'; } });
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', async (e) => {
  e.preventDefault(); dragN = 0; $('#drop').style.display = 'none';
  if (S.mode === 'stamp' && [...e.dataTransfer.files].every(f => /^image\//.test(f.type))) { for (const f of e.dataTransfer.files) await importImageFile(f); return; }
  const replace = !S.pages.length; let handle = null;
  if (replace && e.dataTransfer.items && e.dataTransfer.items[0] && e.dataTransfer.items[0].getAsFileSystemHandle) { try { handle = await e.dataTransfer.items[0].getAsFileSystemHandle(); } catch {} }
  openFiles(e.dataTransfer.files, { replace, handle });
});

/* ---------- старт ---------- */
(async function init() {
  setMode('select'); updUndo(); libLoad(); updTitle();
  try { await loadFonts(); } catch (e) { console.warn('fonts', e); }
  window.__pdfm = { S, openFiles, buildPdf, setMode, startEditItem, PV, select, setEditing, addLibImage, removeWhite, save, ov };
})();
