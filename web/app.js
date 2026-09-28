/* Open Suno · interfaz web
 * Vanilla JS (sin build). Habla con la API FastAPI de /api/*.
 */

// ============================================================ utilidades
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icon = (name, cls = '') => `<svg class="${cls}"><use href="#i-${name}"/></svg>`;

function h(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

const store = {
  get(k, d = null) { try { const v = localStorage.getItem('openSuno.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('openSuno.' + k, JSON.stringify(v)); } catch { /* almacenamiento no disponible */ } },
};

async function api(path, { method = 'GET', body, form } = {}) {
  const init = { method, headers: { 'X-Open-Suno': '1' } }; // protección CSRF exigida por el servidor
  if (form) init.body = form;
  else if (body !== undefined) { init.headers['content-type'] = 'application/json'; init.body = JSON.stringify(body); }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { const j = await r.json(); msg = j.detail || j.error || msg; } catch { /* sin cuerpo JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg, type = '', action = null, ms = 5000) {
  const el = h(`<div class="toast ${type}" role="status"><span>${msg}</span></div>`);
  if (action) {
    const b = h(`<button class="btn ghost xs" type="button">${esc(action.label)}</button>`);
    b.onclick = () => { action.fn(); el.remove(); };
    el.append(b);
  }
  $('#toasts').append(el);
  setTimeout(() => el.remove(), ms);
}

const fmtTime = (s) => {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, '0')}`;
};
const fmtBytes = (b) => {
  if (!b) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
};
const fmtDate = (t) => new Date(t * 1000).toLocaleString('es', { dateStyle: 'medium', timeStyle: 'short' });
const fmtDur = (s) => (s >= 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${s.toFixed(s < 10 ? 1 : 0)} s`);
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

function hashStr(s) { let x = 2166136261; for (const c of String(s)) { x ^= c.charCodeAt(0); x = Math.imul(x, 16777619); } return x >>> 0; }
function coverBg(id) {
  const n = hashStr(id);
  const h1 = n % 360, h2 = (h1 + 40 + ((n >> 8) % 90)) % 360, h3 = (h1 + 170 + ((n >> 16) % 70)) % 360;
  const x = 18 + ((n >> 4) % 64), y = 14 + ((n >> 12) % 60);
  return `radial-gradient(circle at ${x}% ${y}%, hsl(${h1} 92% 66% / .95), transparent 55%),` +
    `radial-gradient(circle at ${100 - x}% ${95 - y / 2}%, hsl(${h3} 85% 58% / .85), transparent 52%),` +
    `linear-gradient(135deg, hsl(${h2} 70% 36%), hsl(${(h2 + 70) % 360} 60% 16%))`;
}
function coverWaves(id) {
  const n = hashStr(id + 'w');
  let paths = '';
  for (let k = 0; k < 3; k++) {
    const amp = 6 + ((n >> (k * 5)) % 14), y0 = 60 + k * 12, f = 1 + ((n >> (k * 3)) % 3);
    let d = `M0 ${y0}`;
    for (let x = 0; x <= 100; x += 5) d += ` L${x} ${(y0 + Math.sin((x / 100) * Math.PI * 2 * f + k) * amp).toFixed(1)}`;
    paths += `<path d="${d}" stroke="#fff" stroke-opacity="${0.16 + k * 0.07}" stroke-width="1.2" fill="none"/>`;
  }
  return `<svg class="cover-art" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">${paths}</svg>`;
}
function setRangeFill(el) {
  const min = +el.min || 0, max = +el.max || 1, v = +el.value;
  el.style.setProperty('--p', `${((v - min) / (max - min || 1)) * 100}%`);
}

// ============================================================ datos
const LANGS = [
  ['', 'Auto (lo decide el LM)'], ['es', 'Español'], ['en', 'Inglés'], ['pt', 'Portugués'], ['fr', 'Francés'], ['it', 'Italiano'],
  ['de', 'Alemán'], ['ca', 'Catalán'], ['ja', 'Japonés'], ['ko', 'Coreano'], ['zh', 'Chino (mandarín)'], ['yue', 'Cantonés'],
  ['ar', 'Árabe'], ['az', 'Azerí'], ['bn', 'Bengalí'], ['bg', 'Búlgaro'], ['cs', 'Checo'], ['hr', 'Croata'], ['ht', 'Criollo haitiano'],
  ['da', 'Danés'], ['sk', 'Eslovaco'], ['fi', 'Finés'], ['el', 'Griego'], ['he', 'Hebreo'], ['hi', 'Hindi'], ['hu', 'Húngaro'],
  ['id', 'Indonesio'], ['is', 'Islandés'], ['la', 'Latín'], ['lt', 'Lituano'], ['ms', 'Malayo'], ['nl', 'Neerlandés'], ['ne', 'Nepalí'],
  ['no', 'Noruego'], ['pa', 'Panyabí'], ['fa', 'Persa'], ['pl', 'Polaco'], ['ro', 'Rumano'], ['ru', 'Ruso'], ['sa', 'Sánscrito'],
  ['sr', 'Serbio'], ['sw', 'Suajili'], ['sv', 'Sueco'], ['tl', 'Tagalo'], ['th', 'Tailandés'], ['ta', 'Tamil'], ['te', 'Telugu'],
  ['tr', 'Turco'], ['uk', 'Ucraniano'], ['ur', 'Urdu'], ['vi', 'Vietnamita'], ['unknown', 'Sin idioma concreto'],
];
const NOTES = ['C', 'C#', 'Db', 'D', 'D#', 'Eb', 'E', 'F', 'F#', 'Gb', 'G', 'G#', 'Ab', 'A', 'A#', 'Bb', 'B'];
const NOTE_ES = { C: 'Do', D: 'Re', E: 'Mi', F: 'Fa', G: 'Sol', A: 'La', B: 'Si' };
const TRACKS = [
  ['vocals', 'Voz principal'], ['backing_vocals', 'Coros'], ['drums', 'Batería'], ['bass', 'Bajo'], ['guitar', 'Guitarra'],
  ['keyboard', 'Teclados'], ['percussion', 'Percusión'], ['strings', 'Cuerdas'], ['synth', 'Sintetizador'], ['fx', 'Efectos'],
  ['brass', 'Metales'], ['woodwinds', 'Vientos madera'],
];
const TASKS = [
  { id: 'cover', name: 'Cover', desc: 'Reinterpreta el audio con el estilo que describas.', base: false },
  { id: 'cover-nofsq', name: 'Remix fiel', desc: 'Conserva melodía y estructura; cambia la producción.', base: false },
  { id: 'repaint', name: 'Repintar región', desc: 'Regenera un tramo o alarga el audio.', base: false },
  { id: 'lego', name: 'Añadir instrumento', desc: 'Crea una pista nueva sobre la base.', base: true },
  { id: 'extract', name: 'Extraer pista', desc: 'Aísla la voz o un instrumento.', base: true },
  { id: 'complete', name: 'Completar arreglo', desc: 'Construye la mezcla a partir de una pista.', base: true },
];
const STYLE_CHIPS = [
  ['Pop', 'pop'], ['Rock', 'rock'], ['Reguetón', 'reggaeton'], ['Salsa', 'salsa'], ['Cumbia', 'cumbia'], ['Bachata', 'bachata'],
  ['Flamenco', 'flamenco'], ['Hip hop', 'hip hop'], ['Trap', 'trap'], ['R&B', 'r&b'], ['Electrónica', 'electronic'], ['House', 'house'],
  ['Lo-fi', 'lo-fi'], ['Jazz', 'jazz'], ['Soul', 'soul'], ['Metal', 'heavy metal'], ['Balada', 'ballad'], ['Bolero', 'bolero'],
  ['Cinemática', 'epic cinematic orchestral'], ['Ambient', 'ambient'], ['Synthwave', 'synthwave'], ['Acústico', 'acoustic'],
  ['Voz femenina', 'female vocals'], ['Voz masculina', 'male vocals'], ['Alegre', 'upbeat'], ['Melancólico', 'melancholic'],
];
const LYRIC_TAGS = ['Intro', 'Verse', 'Pre-Chorus', 'Chorus', 'Bridge', 'Outro', 'Instrumental', 'Guitar Solo', 'Drop', 'Hook', 'Break', 'Fade Out'];
const INSPIRATIONS = [
  'Dreamy synth-pop with shimmering arpeggios, gated reverb drums, warm analog bass and breathy female vocals, nostalgic 80s summer night mood',
  'Energetic reggaeton with a punchy dembow beat, deep 808 bass, bright plucked synths and confident male vocals, club-ready',
  'Intimate acoustic folk ballad with fingerpicked guitar, soft cello, gentle harmonies and a heartfelt male voice',
  'Lo-fi hip hop beat with dusty vinyl crackle, mellow Rhodes chords and laid-back boom bap drums, instrumental study vibe',
  'Epic orchestral cinematic score with thundering taiko drums, soaring strings, brass fanfares and a choir, heroic and triumphant',
  'Salsa dura with a tight horn section, piano montuno, congas and timbales, passionate male lead with call-and-response coros',
  'Dark techno with a driving four-on-the-floor kick, hypnotic acid bassline, metallic percussion and eerie pads',
  'Smooth late-night jazz trio with brushed drums, walking upright bass and a lyrical piano, warm and relaxed',
  'Anthemic pop rock with big distorted guitars, pounding drums, a soaring chorus and a powerful female vocal',
  'Flamenco fusion with rasgueado guitar, palmas, cajón, modern electronic bass and a passionate cante',
  'Melancholic indie pop with jangly guitars, lo-fi drums, dreamy reverb and a vulnerable male vocal',
  'Upbeat cumbia with accordion hooks, güira and a bouncy bass groove, festive and joyful male and female duet',
];
const STATE_LABEL = { stopped: 'Detenido', starting: 'Iniciando…', running: 'En ejecución', stopping: 'Deteniendo…', error: 'Error' };
const JOB_STATUS = { queued: 'En cola', running: 'Generando', done: 'Lista', failed: 'Error', cancelled: 'Cancelada' };

// Idioma probable de la descripción (el LM pequeño a veces elige un idioma
// al azar cuando la voz está en "Auto"; así respetamos el idioma del usuario).
const LANG_HINTS = {
  es: [' el ', ' la ', ' los ', ' las ', ' con ', ' y ', ' del ', ' una ', ' un ', ' en ', ' para ', ' que ', ' voz ', ' sobre ', 'ñ', 'ó', 'í', 'á', 'é', 'ú', 'ción'],
  en: [' the ', ' with ', ' and ', ' of ', ' a ', ' for ', ' in ', ' vocals', ' voice', ' song', ' beat', ' drums'],
  pt: [' com ', ' e ', ' uma ', ' um ', ' para ', ' voz ', 'ção', 'ões', 'ã', 'õ', ' guitarra', ' bateria'],
  fr: [' avec ', ' et ', ' le ', ' les ', ' des ', ' une ', ' pour ', ' voix ', 'è', 'ê', 'à '],
  it: [' con ', ' e ', ' il ', ' gli ', ' della ', ' una ', ' per ', ' voce ', 'ò', 'à'],
  de: [' mit ', ' und ', ' der ', ' die ', ' das ', ' eine ', ' für ', ' stimme', 'ß', 'ü', 'ö', 'ä'],
};
const EXPLICIT_LANG = { es: /(en |in )?(español|spanish|castellano)/i, en: /(en |in )?(inglés|english)/i, pt: /(português|portugués|portuguese)/i, fr: /(francés|français|french)/i, it: /(italiano|italian)/i, de: /(alemán|deutsch|german)/i };
function detectLang(text) {
  const t = ` ${String(text || '').toLowerCase().replace(/[,.;:!?()]/g, ' ')} `;
  for (const [code, re] of Object.entries(EXPLICIT_LANG)) if (re.test(t)) return code;
  let best = '', score = 0, second = 0;
  for (const [code, words] of Object.entries(LANG_HINTS)) {
    const sc = words.reduce((n, w) => n + (t.split(w).length - 1), 0);
    if (sc > score) { second = score; score = sc; best = code; } else if (sc > second) second = sc;
  }
  return score >= 2 && score >= second * 1.5 ? best : '';
}
function effectiveLang(f) {
  if (f.vocal_language || f.instrumental) return f.vocal_language;
  return detectLang(`${f.caption} ${f.lyrics || ''}`);
}
function updateLangHint() {
  const opt = $('#f-lang').options[0];
  const code = detectLang(`${$('#f-caption').value} ${$('#f-lyrics').value}`);
  const name = LANGS.find(([c]) => c === code)?.[1];
  opt.textContent = name ? `Auto · ${name}` : 'Auto (lo decide el LM)';
}

// ============================================================ estado
const S = {
  view: 'create',
  mode: store.get('mode', 'custom'),
  st: null, // /api/state
  settings: null,
  profiles: {},
  devices: null,
  models: null,
  songs: [],
  songsSig: '',
  jobs: [],
  jobParams: store.get('jobParams', {}),
  presets: {},
  catalog: null,
  roleTab: store.get('roleTab', 'dit'),
  downloads: [],
  srcFile: null,
  refFile: null,
  engDirty: false,
  logs: [],
  logSeq: 0,
  es: null,
  libFav: false,
  lastJobStatus: {},
};

// ============================================================ navegación
function setView(view) {
  if (!['create', 'library', 'engine', 'models'].includes(view)) view = 'create';
  S.view = view;
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${view}`));
  $$('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.view === view));
  $('.main').scrollTop = 0;
  if (view === 'library') renderLibrary();
  if (view === 'engine') enterEngine(); else leaveEngine();
  if (view === 'models') enterModels();
  tick();
}
window.addEventListener('hashchange', () => setView(location.hash.slice(1)));

// ============================================================ CREAR · formulario
function initCreate() {
  // selects estáticos
  $('#f-lang').innerHTML = LANGS.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join('');
  $('#f-key').innerHTML = '<option value="">auto</option>' + NOTES.flatMap((n) => ['major', 'minor'].map((m) => {
    const es = NOTE_ES[n[0]] + (n[1] === '#' ? '♯' : n[1] === 'b' ? '♭' : '');
    return `<option value="${n} ${m}">${n} ${m} · ${es} ${m === 'major' ? 'mayor' : 'menor'}</option>`;
  })).join('');
  $('#f-track').innerHTML = TRACKS.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join('');
  $('#styleChips').innerHTML = STYLE_CHIPS.map(([l, v]) => `<button class="chip" type="button" data-style="${esc(v)}">${esc(l)}</button>`).join('');
  $('#tagBar').innerHTML = LYRIC_TAGS.map((t) => `<button type="button" data-tag="${t}">[${t}]</button>`).join('');
  $('#taskGrid').innerHTML = TASKS.map((t) => `
    <button type="button" class="task" role="radio" data-task="${t.id}">
      <b>${esc(t.name)}${t.base ? ' <span class="req">DiT base</span>' : ''}</b><small>${esc(t.desc)}</small>
    </button>`).join('');

  // modo
  $$('#modeSeg button').forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
  // chips de estilo: un clic añade el estilo a la descripción, otro lo quita
  $('#styleChips').onclick = (e) => {
    const b = e.target.closest('[data-style]'); if (!b) return;
    const ta = $('#f-caption');
    const style = b.dataset.style;
    ta.value = hasStyle(ta.value, style) ? removeStyle(ta.value, style) : addStyle(ta.value, style);
    syncChips(); updateLangHint(); saveFormSoon();
  };
  $('#f-caption').addEventListener('input', syncChips);
  $('#inspireBtn').onclick = () => {
    const pick = INSPIRATIONS[Math.floor(Math.random() * INSPIRATIONS.length)];
    $('#f-caption').value = pick;
    syncChips(); updateLangHint(); saveFormSoon();
  };
  // etiquetas de letra
  $('#tagBar').onclick = (e) => {
    const b = e.target.closest('[data-tag]'); if (!b) return;
    const ta = $('#f-lyrics');
    if (ta.disabled) return;
    const tag = `[${b.dataset.tag}]`;
    const { selectionStart: s, selectionEnd: en, value } = ta;
    const before = value.slice(0, s), after = value.slice(en);
    const pre = before && !before.endsWith('\n') ? (before.endsWith('\n\n') ? '' : '\n\n') : (before && !before.endsWith('\n\n') ? '\n' : '');
    const ins = `${pre}${tag}\n`;
    ta.value = before + ins + after;
    ta.focus();
    ta.selectionStart = ta.selectionEnd = before.length + ins.length;
    updateLyricsStats(); saveFormSoon();
  };
  $('#instrumental').onchange = syncInstrumental;
  $('#f-lyrics').addEventListener('input', updateLyricsStats);

  // tareas audio→audio
  $('#taskGrid').onclick = (e) => { const b = e.target.closest('[data-task]'); if (b) setTask(b.dataset.task); };

  // rangos con salida
  $$('input[type=range][data-field]').forEach((r) => r.addEventListener('input', () => updateOutputs()));
  // cualquier cambio guarda el formulario
  $('.create-form').addEventListener('input', () => { saveFormSoon(); updateCreateInfo(); });
  $('#f-caption').addEventListener('input', debounce(updateLangHint, 250));
  $('#f-lyrics').addEventListener('input', debounce(updateLangHint, 250));
  $('.create-form').addEventListener('change', () => { saveFormSoon(); updateCreateInfo(); checkTaskWarn(); });

  // pestañas avanzadas
  $$('#advanced .tabs button').forEach((b) => (b.onclick = () => setAdvTab(b.dataset.tab)));
  setAdvTab(store.get('advTab', 'models'));
  $('#advanced').open = store.get('advOpen', false);
  $('#advanced').addEventListener('toggle', () => store.set('advOpen', $('#advanced').open));

  // semilla
  $('#seedDice').onclick = () => { $('#f-seed').value = Math.floor(Math.random() * 2 ** 31); saveFormSoon(); updateCreateInfo(); };
  $('#seedReset').onclick = () => { $('#f-seed').value = -1; saveFormSoon(); updateCreateInfo(); };

  // audio de origen / referencia
  setupDrop('#srcDrop', '#srcFile', (f) => loadAudioFile(f, 'src'));
  setupDrop('#refDrop', '#refFile', (f) => loadAudioFile(f, 'ref'));
  $('#srcSong').onchange = () => { if ($('#srcSong').value) clearAudio('src', true); saveFormSoon(); };
  $('#refSong').onchange = () => { if ($('#refSong').value) clearAudio('ref', true); saveFormSoon(); };

  // letra con IA
  $('#lyricsAiBtn').onclick = () => lyricsAI('inspire');
  $('#lyricsFormatBtn').onclick = () => lyricsAI('format');

  // presets
  $('#presetSelect').onchange = () => {
    const name = $('#presetSelect').value;
    $('#presetDelete').disabled = !name;
    if (name && S.presets[name]) { writeForm(S.presets[name].params); toast(`Configuración «${esc(name)}» cargada`, 'ok'); }
  };
  $('#presetSave').onclick = savePreset;
  $('#presetDelete').onclick = deletePreset;
  $('#formReset').onclick = () => { writeForm(DEFAULT_FORM); toast('Valores por defecto restablecidos'); };

  // crear
  $('#createBtn').onclick = submitCreate;
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && S.view === 'create') { e.preventDefault(); submitCreate(); }
  });
  $('#jobsClear').onclick = async () => { await api('/api/jobs/clear', { method: 'POST' }); refreshJobs(); };
  $('#jobList').onclick = onJobClick;
  $('#recentList').onclick = onSongRowClick;

  DEFAULT_FORM = readForm();
  const saved = store.get('form');
  if (saved) writeForm(saved);
  setMode(S.mode);
  setTask($('#f-task').value || 'cover');
  updateOutputs(); updateLyricsStats(); syncInstrumental();
}
let DEFAULT_FORM = {};

// La descripción se trata como una lista separada por comas: una etiqueta está
// activa cuando uno de esos segmentos coincide exactamente con su estilo.
const captionSegments = (text) => String(text || '').split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
const hasStyle = (text, style) => captionSegments(text).includes(style.toLowerCase());
function addStyle(text, style) {
  const cur = String(text || '').trim().replace(/[,.;\s]+$/, '');
  return cur ? `${cur}, ${style}` : style;
}
function removeStyle(text, style) {
  return String(text || '').split(',').map((s) => s.trim())
    .filter((s) => s && s.toLowerCase() !== style.toLowerCase()).join(', ');
}
function syncChips() {
  const caption = $('#f-caption').value;
  $$('#styleChips [data-style]').forEach((b) => {
    const on = hasStyle(caption, b.dataset.style);
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', on);
  });
}

function setMode(mode) {
  S.mode = mode;
  store.set('mode', mode);
  $$('#modeSeg button').forEach((b) => { b.classList.toggle('active', b.dataset.mode === mode); b.setAttribute('aria-selected', b.dataset.mode === mode); });
  $$('.mode-custom, .mode-audio').forEach((el) => {
    el.hidden = !(el.classList.contains(`mode-${mode}`));
  });
  $('#captionLabel').textContent = mode === 'simple' ? 'Describe tu canción' : mode === 'audio' ? 'Estilo deseado' : 'Estilo y descripción';
  updateCreateInfo(); checkTaskWarn();
}

function setAdvTab(tab) {
  $$('#advanced .tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  $$('#advanced .tab-pane').forEach((p) => p.classList.toggle('active', p.dataset.pane === tab));
  store.set('advTab', tab);
}

function setTask(task) {
  $('#f-task').value = task;
  $$('#taskGrid .task').forEach((b) => { b.classList.toggle('active', b.dataset.task === task); b.setAttribute('aria-checked', b.dataset.task === task); });
  $$('.task-opt').forEach((el) => (el.hidden = !el.dataset.tasks.split(' ').includes(task)));
  checkTaskWarn(); saveFormSoon(); updateCreateInfo();
}

function checkTaskWarn() {
  const w = $('#taskWarn');
  const task = TASKS.find((t) => t.id === $('#f-task').value);
  const dit = $('#f-dit').value || '';
  if (S.mode === 'audio' && task?.base && /turbo/i.test(dit) && !/base/i.test(dit)) {
    w.hidden = false;
    w.querySelector('span').innerHTML = `«${esc(task.name)}» necesita un DiT <b>base</b> o <b>SFT</b> (por ejemplo <code>acestep-v15-base-Q4_K_M</code>). Descárgalo en <a href="#models">Modelos</a> y elígelo en Ajustes avanzados → Modelos.`;
  } else w.hidden = true;
}

function syncInstrumental() {
  const on = $('#instrumental').checked;
  const ta = $('#f-lyrics');
  ta.disabled = on;
  $('#lyricsAiBtn').disabled = on;
  $('#lyricsFormatBtn').disabled = on;
  $('#lyricsCard').classList.toggle('dim', on);
  updateCreateInfo(); saveFormSoon();
}

function updateLyricsStats() {
  const v = $('#f-lyrics').value;
  const lines = v.split('\n').filter((l) => l.trim() && !/^\s*\[.*\]\s*$/.test(l)).length;
  const secs = (v.match(/^\s*\[[^\]]+\]/gm) || []).length;
  $('#lyricsStats').textContent = `${lines} líneas · ${secs} secciones · ${v.length} caracteres`;
}

function updateOutputs() {
  $$('input[type=range][data-field]').forEach((r) => {
    setRangeFill(r);
    const out = $(`output[data-for="${r.dataset.field}"]`);
    if (out) out.textContent = (+r.value).toFixed(r.step && r.step.includes('.') ? (r.step.split('.')[1].length) : 0);
  });
}

function readForm() {
  const p = {};
  for (const el of $$('[data-field]')) p[el.dataset.field] = el.type === 'checkbox' ? el.checked : el.value;
  p.instrumental = $('#instrumental').checked;
  p.mode = S.mode;
  p.src_song_id = $('#srcSong').value;
  p.ref_song_id = $('#refSong').value;
  return p;
}

function writeForm(p) {
  if (!p) return;
  for (const el of $$('[data-field]')) {
    const k = el.dataset.field;
    if (!(k in p)) continue;
    const v = p[k];
    if (el.type === 'checkbox') el.checked = !!v;
    else if (el.tagName === 'SELECT') {
      const val = v === null || v === undefined ? '' : String(v);
      if ([...el.options].some((o) => o.value === val)) el.value = val;
    } else el.value = v === null || v === undefined ? '' : v;
  }
  if ('instrumental' in p) $('#instrumental').checked = !!p.instrumental;
  if (p.src_song_id !== undefined) $('#srcSong').value = p.src_song_id || '';
  if (p.ref_song_id !== undefined) $('#refSong').value = p.ref_song_id || '';
  if (p.mode) setMode(p.mode);
  setTask($('#f-task').value || 'cover');
  updateOutputs(); updateLyricsStats(); syncInstrumental(); updateCreateInfo(); updateLangHint(); syncChips();
}

const saveFormSoon = debounce(() => store.set('form', readForm()), 300);

// Construye los parámetros para /api/generate según el modo.
function buildParams() {
  const f = readForm();
  const p = {};
  const uiOnly = new Set(['instrumental', 'mode', 'src_song_id', 'ref_song_id']);
  for (const [k, v] of Object.entries(f)) {
    if (uiOnly.has(k)) continue;
    if (v === '' || v === null || v === undefined) continue;
    p[k] = v;
  }
  const task = S.mode === 'audio' ? f.task_type : 'text2music';
  p.task_type = task;
  if (f.instrumental) p.lyrics = '[Instrumental]';
  else if (!p.vocal_language) { const lang = effectiveLang(f); if (lang) p.vocal_language = lang; }
  if (S.mode === 'simple') {
    delete p.bpm; delete p.keyscale; delete p.timesignature;
    if (!f.instrumental) delete p.lyrics;
  }
  if (!['cover', 'cover-nofsq'].includes(task)) { delete p.audio_cover_strength; delete p.cover_noise_strength; }
  if (!['repaint', 'lego'].includes(task)) { delete p.repainting_start; delete p.repainting_end; }
  if (!['lego', 'extract', 'complete'].includes(task)) delete p.track;
  if (!p.adapter) delete p.adapter_scale;
  if (S.mode === 'audio' && f.src_song_id && !S.srcFile) p.src_song_id = f.src_song_id;
  if (f.ref_song_id && !S.refFile) p.ref_song_id = f.ref_song_id;
  return p;
}

function updateCreateInfo() {
  if (!S.settings) return;
  const f = readForm();
  const maxB = S.settings.engine.max_batch || 1;
  const lmB = Math.min(Math.max(+f.lm_batch_size || 1, 1), maxB);
  const syB = Math.min(Math.max(+f.synth_batch_size || 1, 1), 9);
  const task = S.mode === 'audio' ? f.task_type : 'text2music';
  const usesLm = f.use_lm && ['text2music', 'lego', 'complete'].includes(task);
  const n = Math.min((usesLm ? lmB : 1) * syB, 9);
  const dev = deviceLabel(S.st?.engine?.device || S.settings.engine.device, true);
  const dit = (f.synth_model || '').replace(/^acestep-v15-/, '').replace('.gguf', '');
  const lm = (f.lm_model || '').replace(/^acestep-5Hz-lm-/, '').replace('.gguf', '');
  $('#createInfo').innerHTML = `<b>${n} ${n === 1 ? 'canción' : 'canciones'}</b> · ${usesLm ? `LM ${esc(lm)} · ` : 'sin LM · '}DiT ${esc(dit)} · ${esc(dev)}`;
  $('#createBtn span').textContent = n > 1 ? `Crear ${n}` : 'Crear';
  $('#maxBatchHint').textContent = `(máx. ${maxB}, ajustable en Motor)`;
  $('#lmNote').hidden = !/0\.6B/i.test(lm);
  // placeholders "auto" según el tipo de DiT
  const turbo = /turbo/i.test(dit) && !/sftturbo50/i.test(dit);
  $('#f-steps').placeholder = `auto (${turbo ? 8 : 50})`;
  $('#f-guid').placeholder = 'auto (1.0)';
  $('#f-shift').placeholder = `auto (${turbo ? 3 : 1})`;
  const seed = +f.seed >= 0 ? `semilla ${f.seed}` : 'semilla aleatoria';
  const steps = f.inference_steps || (turbo ? 8 : 50);
  $('#advSummary').textContent = `${f.solver} · ${steps} pasos · ${f.output_format.toUpperCase()}${f.output_format === 'mp3' ? ` ${f.mp3_bitrate}k` : ''} · ${seed}`;
}

// ------------------------------------------------------------ audio de origen
function setupDrop(zoneSel, inputSel, onFile) {
  const zone = $(zoneSel), input = $(inputSel);
  input.onchange = () => input.files[0] && onFile(input.files[0]);
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault(); zone.classList.remove('drag');
    const f = e.dataTransfer.files[0]; if (f) onFile(f);
  });
}

async function loadAudioFile(file, which) {
  const textEl = $(which === 'src' ? '#srcDropText' : '#refDropText');
  textEl.innerHTML = `Procesando <b>${esc(file.name)}</b>…`;
  try {
    let blob = file, name = file.name;
    const native = /\.(wav|mp3)$/i.test(file.name) || /audio\/(wav|x-wav|wave|mpeg|mp3)/.test(file.type);
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const buf = await ctx.decodeAudioData(await file.arrayBuffer());
    ctx.close();
    if (!native) { // acestep.cpp lee WAV y MP3: convertimos en el navegador
      blob = encodeWav(buf);
      name = file.name.replace(/\.[^.]+$/, '') + '.wav';
    }
    const obj = { blob, name, duration: buf.duration };
    if (which === 'src') {
      S.srcFile = obj; $('#srcSong').value = '';
      const pv = $('#srcPreview');
      if (pv.src.startsWith('blob:')) URL.revokeObjectURL(pv.src);
      pv.src = URL.createObjectURL(blob); pv.hidden = false;
    } else { S.refFile = obj; $('#refSong').value = ''; }
    $(which === 'src' ? '#srcDrop' : '#refDrop').classList.add('has');
    textEl.innerHTML = `<b>${esc(file.name)}</b> · ${fmtTime(buf.duration)}${native ? '' : ' (convertido a WAV)'} <button class="btn ghost xs" type="button" data-clear="${which}">Quitar</button>`;
    textEl.querySelector('[data-clear]').onclick = (e) => { e.preventDefault(); e.stopPropagation(); clearAudio(which); };
  } catch (err) {
    clearAudio(which);
    toast(`No se pudo leer el audio: ${esc(err.message)}`, 'err');
  }
}

function clearAudio(which, keepSelect = false) {
  if (which === 'src') {
    S.srcFile = null; $('#srcFile').value = '';
    const pv = $('#srcPreview');
    if (pv.src.startsWith('blob:')) URL.revokeObjectURL(pv.src);
    pv.hidden = true; pv.removeAttribute('src');
    $('#srcDrop').classList.remove('has');
    $('#srcDropText').innerHTML = '<b>Arrastra un audio</b> o haz clic (WAV, MP3, FLAC, OGG…)';
    if (!keepSelect) $('#srcSong').value = '';
  } else {
    S.refFile = null; $('#refFile').value = '';
    $('#refDrop').classList.remove('has');
    $('#refDropText').textContent = 'Subir audio de referencia';
    if (!keepSelect) $('#refSong').value = '';
  }
}

function encodeWav(buf) {
  const ch = Math.min(2, buf.numberOfChannels), sr = buf.sampleRate, n = buf.length;
  const dv = new DataView(new ArrayBuffer(44 + n * ch * 2));
  const w = (o, s) => [...s].forEach((c, i) => dv.setUint8(o + i, c.charCodeAt(0)));
  w(0, 'RIFF'); dv.setUint32(4, 36 + n * ch * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
  dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, ch, true); dv.setUint32(24, sr, true);
  dv.setUint32(28, sr * ch * 2, true); dv.setUint16(32, ch * 2, true); dv.setUint16(34, 16, true); w(36, 'data'); dv.setUint32(40, n * ch * 2, true);
  const chans = [...Array(ch)].map((_, i) => buf.getChannelData(i));
  let o = 44;
  for (let i = 0; i < n; i++) for (let c = 0; c < ch; c++) { const s = Math.max(-1, Math.min(1, chans[c][i])); dv.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true); o += 2; }
  return new Blob([dv], { type: 'audio/wav' });
}

// ------------------------------------------------------------ enviar
async function submitCreate() {
  const p = buildParams();
  if (!S.st?.setup?.ready) { toast('Faltan el motor o algún modelo. Ve a <a href="#models">Modelos</a>.', 'warn'); return; }
  if (!p.caption && !['lego', 'extract', 'complete'].includes(p.task_type)) {
    toast('Escribe una descripción del estilo.', 'warn'); $('#f-caption').focus(); return;
  }
  if (S.mode === 'audio' && !S.srcFile && !p.src_song_id) { toast('Elige un audio de origen.', 'warn'); return; }
  const fd = new FormData();
  fd.append('params', JSON.stringify(p));
  if (S.mode === 'audio' && S.srcFile) fd.append('src_audio', S.srcFile.blob, S.srcFile.name);
  if (S.refFile) fd.append('ref_audio', S.refFile.blob, S.refFile.name);
  const btn = $('#createBtn');
  btn.disabled = true;
  try {
    const job = await api('/api/generate', { method: 'POST', form: fd });
    S.jobParams[job.id] = readForm();
    const keys = Object.keys(S.jobParams); if (keys.length > 60) delete S.jobParams[keys[0]];
    store.set('jobParams', S.jobParams);
    toast(S.st?.engine?.state === 'running' ? 'Añadida a la cola' : 'Añadida a la cola · arrancando el motor…', 'ok', null, 2500);
    await refreshJobs();
  } catch (e) {
    toast(esc(e.message), 'err');
  } finally {
    setTimeout(() => (btn.disabled = false), 400);
  }
}

// ------------------------------------------------------------ letra con IA
async function lyricsAI(mode) {
  const f = readForm();
  if (!f.caption.trim()) { toast('Escribe primero el estilo o una idea en la descripción.', 'warn'); return; }
  if (mode === 'format' && !f.lyrics.trim()) { toast('Escribe una letra para darle formato.', 'warn'); return; }
  const body = { caption: f.caption, lm_mode: mode, lm_model: f.lm_model, vocal_language: effectiveLang(f),
    lm_temperature: f.lm_temperature, lm_top_p: f.lm_top_p, lm_top_k: f.lm_top_k, duration: f.duration, bpm: f.bpm,
    keyscale: f.keyscale, timesignature: f.timesignature, title: mode === 'inspire' ? 'Letra con IA' : 'Formato de letra' };
  if (mode === 'format') body.lyrics = f.lyrics;
  const btn = $(mode === 'inspire' ? '#lyricsAiBtn' : '#lyricsFormatBtn');
  const label = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = `${icon('refresh')}Pensando…`;
  try {
    let job = await api('/api/lyrics', { method: 'POST', body });
    refreshJobs();
    while (['queued', 'running'].includes(job.status)) { await sleep(700); job = await api(`/api/jobs/${job.id}`); }
    if (job.status !== 'done') throw new Error(job.error || 'cancelado');
    const r = job.result || {};
    const patch = {};
    if (r.lyrics) patch.lyrics = r.lyrics;
    if (r.bpm) patch.bpm = r.bpm;
    if (r.keyscale) patch.keyscale = r.keyscale;
    if (r.timesignature) patch.timesignature = r.timesignature;
    if (r.duration) patch.duration = Math.round(r.duration);
    if (r.vocal_language && !f.vocal_language) patch.vocal_language = r.vocal_language;
    writeForm(patch);
    if (S.mode === 'simple') setMode('custom');
    saveFormSoon();
    toast(mode === 'inspire' ? 'Letra y metadatos generados ✨' : 'Letra formateada', 'ok');
  } catch (e) {
    toast(`El LM no pudo escribir la letra: ${esc(e.message)}`, 'err');
  } finally {
    btn.innerHTML = label; btn.disabled = $('#instrumental').checked;
  }
}

// ------------------------------------------------------------ presets
async function loadPresets() {
  S.presets = (await api('/api/presets')).presets || {};
  const sel = $('#presetSelect'), cur = sel.value;
  sel.innerHTML = '<option value="">Configuraciones guardadas…</option>' +
    Object.keys(S.presets).sort().map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
  if (S.presets[cur]) sel.value = cur;
  $('#presetDelete').disabled = !sel.value;
}
async function savePreset() {
  const name = await promptText('Guardar configuración', 'Nombre de la configuración', $('#presetSelect').value || '');
  if (!name) return;
  const params = readForm();
  delete params.src_song_id; delete params.ref_song_id;
  await api('/api/presets', { method: 'POST', body: { name, params } });
  await loadPresets();
  $('#presetSelect').value = name; $('#presetDelete').disabled = false;
  toast(`Configuración «${esc(name)}» guardada`, 'ok');
}
async function deletePreset() {
  const name = $('#presetSelect').value; if (!name) return;
  if (!(await confirmBox('Eliminar configuración', `¿Eliminar «${name}»?`, 'Eliminar'))) return;
  await api(`/api/presets/${encodeURIComponent(name)}`, { method: 'DELETE' });
  await loadPresets();
}

// ============================================================ cola de trabajos
async function refreshJobs() {
  try { S.jobs = (await api('/api/jobs')).jobs; } catch { return; }
  let needSongs = false;
  for (const j of S.jobs) {
    const prev = S.lastJobStatus[j.id];
    if (prev && prev !== j.status && j.status === 'done' && j.kind === 'song') {
      needSongs = true;
      const sid = j.song_ids[0];
      toast(`«${esc(j.title)}» está lista`, 'ok', sid ? { label: 'Reproducir', fn: () => playSong(sid) } : null, 7000);
    }
    if (prev && prev !== j.status && j.status === 'failed') toast(`Falló «${esc(j.title)}»: ${esc(j.error || '')}`, 'err', null, 9000);
    S.lastJobStatus[j.id] = j.status;
    if (j.status === 'done' && j.song_ids.some((id) => !S.songs.find((s) => s.id === id))) needSongs = true;
  }
  if (needSongs) await refreshSongs();
  renderJobs();
}

function jobHtml(j) {
  const pct = Math.round((j.progress || 0) * 100);
  const cls = j.status;
  const kindTag = j.kind === 'lyrics' ? '<span class="tag acc">Letra</span>' : '';
  const stTag = { done: 'ok', failed: 'err', cancelled: 'warn' }[j.status] || '';
  let body = '';
  if (j.status === 'running' || j.status === 'queued') {
    body = `<div class="job-stage"><span>${esc(j.status === 'queued' ? 'Esperando turno' : j.stage_label)}${j.detail ? ' · ' + esc(j.detail) : ''}</span><span>${j.status === 'running' ? fmtTime(j.elapsed) : ''}</span></div>
            <div class="bar"><i style="width:${Math.max(pct, 2)}%"></i></div>`;
  } else if (j.status === 'failed') {
    body = `<div class="job-err">${esc(j.error || 'Error desconocido')}</div>`;
  } else if (j.status === 'done' && j.kind === 'song') {
    const songs = j.song_ids.map((id) => S.songs.find((s) => s.id === id)).filter(Boolean);
    body = `<div class="job-songs">${songs.map(songRowHtml).join('')}</div>
            <div class="job-stage"><span>${j.timings?.total ? 'Generada en ' + fmtDur(j.timings.total) : ''}</span><span></span></div>`;
  } else if (j.status === 'done' && j.kind === 'lyrics') {
    body = `<div class="job-stage"><span>Letra aplicada al formulario</span><span>${j.timings?.lm ? fmtDur(j.timings.lm) : ''}</span></div>`;
  }
  const actions = ['queued', 'running'].includes(j.status)
    ? `<button class="icon-btn" data-job-cancel="${j.id}" aria-label="Cancelar" title="Cancelar">${icon('x')}</button>`
    : `${j.status === 'failed' && S.jobParams[j.id] ? `<button class="icon-btn" data-job-retry="${j.id}" aria-label="Reintentar" title="Cargar ajustes y reintentar">${icon('reuse')}</button>` : ''}
       <button class="icon-btn" data-job-remove="${j.id}" aria-label="Quitar de la lista" title="Quitar">${icon('trash')}</button>`;
  return `<div class="job ${cls}" data-job="${j.id}" data-status="${j.status}">
    <div class="job-top"><span class="job-title">${esc(j.title)}</span>${kindTag}<span class="tag ${stTag}">${JOB_STATUS[j.status]}</span>${actions}</div>
    ${body}</div>`;
}

function renderJobs() {
  const list = $('#jobList');
  const jobs = S.jobs.slice(0, 40);
  if (!jobs.length) { list.innerHTML = '<p class="empty">Aún no has creado nada. Tu primera canción aparecerá aquí.</p>'; return; }
  list.querySelector('.empty')?.remove();
  const seen = new Set();
  let prevEl = null;
  for (const j of jobs) {
    seen.add(j.id);
    let el = list.querySelector(`[data-job="${j.id}"]`);
    const sig = `${j.status}|${j.song_ids.length}|${S.songsSig}`;
    if (el && el.dataset.sig === sig && j.status === 'running') {
      // actualización en sitio: no rompe clics en curso
      const bar = el.querySelector('.bar > i'); if (bar) bar.style.width = `${Math.max(Math.round(j.progress * 100), 2)}%`;
      const spans = el.querySelectorAll('.job-stage span');
      if (spans[0]) spans[0].textContent = `${j.stage_label}${j.detail ? ' · ' + j.detail : ''}`;
      if (spans[1]) spans[1].textContent = fmtTime(j.elapsed);
    } else if (!el || el.dataset.sig !== sig) {
      const nel = h(jobHtml(j)); nel.dataset.sig = sig;
      if (el) el.replaceWith(nel); else if (prevEl) prevEl.after(nel); else list.prepend(nel);
      el = nel;
    }
    prevEl = el;
  }
  $$('[data-job]', list).forEach((el) => { if (!seen.has(el.dataset.job)) el.remove(); });
  markPlaying();
}

async function onJobClick(e) {
  const c = e.target.closest('[data-job-cancel]');
  if (c) { await api(`/api/jobs/${c.dataset.jobCancel}/cancel`, { method: 'POST' }); refreshJobs(); return; }
  const r = e.target.closest('[data-job-remove]');
  if (r) { await api(`/api/jobs/${r.dataset.jobRemove}`, { method: 'DELETE' }); refreshJobs(); return; }
  const rt = e.target.closest('[data-job-retry]');
  if (rt) { writeForm(S.jobParams[rt.dataset.jobRetry]); toast('Ajustes cargados: revisa y pulsa Crear'); return; }
  onSongRowClick(e);
}

// ============================================================ canciones
async function refreshSongs() {
  try { const d = await api('/api/songs'); S.songs = d.songs; S.libStats = d.stats; } catch { return; }
  S.songsSig = String(hashStr(S.songs.map((s) => s.id + s.title + s.favorite).join('|')));
  renderRecent();
  fillSongSelects();
  $('#libCount').textContent = S.songs.length || '';
  if (S.view === 'library') renderLibrary();
}

const isInstrumental = (s) => (s.request?.lyrics || s.user_params?.lyrics || '').trim().toLowerCase() === '[instrumental]';
// El LM a veces envuelve la descripción entre comillas; se muestran sin ellas.
const cleanCaption = (c) => String(c || '').trim().replace(/^['"“‘]+|['"”’]+$/g, '');

function songSub(s) {
  const r = s.request || {};
  const bits = [];
  if (r.bpm) bits.push(`${r.bpm} BPM`);
  if (r.keyscale) bits.push(r.keyscale);
  if (!bits.length) bits.push(cleanCaption(r.caption).slice(0, 60));
  return bits.join(' · ');
}

function songRowHtml(s) {
  return `<div class="song-row" data-song="${s.id}">
    <div class="mini-cover" style="background:${coverBg(s.id)}">${icon('play')}</div>
    <div class="sr-text"><div class="sr-title">${esc(s.title)}${s.variants > 1 ? ` <span class="hint">v${s.variant}</span>` : ''}</div><div class="sr-sub">${esc(songSub(s))}</div></div>
    <span class="sr-dur">${fmtTime(s.duration)}</span>
    <button class="icon-btn ${s.favorite ? 'on' : ''}" data-fav="${s.id}" aria-label="Favorita">${icon('heart')}</button>
    <button class="icon-btn" data-more="${s.id}" aria-label="Más opciones">${icon('more')}</button>
  </div>`;
}

function renderRecent() {
  const list = $('#recentList');
  const songs = S.songs.slice(0, 15);
  list.innerHTML = songs.length ? songs.map(songRowHtml).join('') : '<p class="empty">Las canciones que crees aparecerán aquí.</p>';
  markPlaying();
}

async function onSongRowClick(e) {
  const fav = e.target.closest('[data-fav]');
  if (fav) { e.stopPropagation(); toggleFav(fav.dataset.fav); return; }
  const more = e.target.closest('[data-more]');
  if (more) { e.stopPropagation(); openSongMenu(more.dataset.more, more); return; }
  const row = e.target.closest('[data-song]');
  if (row) {
    const list = [...row.parentElement.querySelectorAll('[data-song]')].map((x) => x.dataset.song);
    if (P.current === row.dataset.song) togglePlay(); else playSong(row.dataset.song, list);
  }
}

function fillSongSelects() {
  const opts = S.songs.map((s) => `<option value="${s.id}">${esc(s.title)}${s.variants > 1 ? ' (v' + s.variant + ')' : ''} · ${fmtTime(s.duration)}</option>`).join('');
  for (const [sel, first] of [['#srcSong', '— Elegir canción —'], ['#refSong', '— Ninguna —']]) {
    const el = $(sel), cur = el.value;
    el.innerHTML = `<option value="">${first}</option>${opts}`;
    if (S.songs.some((s) => s.id === cur)) el.value = cur;
  }
  const saved = store.get('form');
  if (saved?.src_song_id && !$('#srcSong').value && S.songs.some((s) => s.id === saved.src_song_id)) $('#srcSong').value = saved.src_song_id;
  if (saved?.ref_song_id && !$('#refSong').value && S.songs.some((s) => s.id === saved.ref_song_id)) $('#refSong').value = saved.ref_song_id;
}

async function toggleFav(id) {
  const s = S.songs.find((x) => x.id === id); if (!s) return;
  await api(`/api/songs/${id}`, { method: 'PATCH', body: { favorite: !s.favorite } });
  await refreshSongs(); renderJobs(); syncPlayerFav();
}

// ------------------------------------------------------------ biblioteca
function renderLibrary() {
  const q = $('#libSearch').value.trim().toLowerCase();
  let songs = S.songs;
  if (S.libFav) songs = songs.filter((s) => s.favorite);
  if (q) songs = songs.filter((s) => `${s.title} ${s.request?.caption || ''} ${s.request?.lyrics || ''}`.toLowerCase().includes(q));
  const st = S.libStats || { count: 0, duration: 0, size: 0 };
  $('#libStats').textContent = st.count ? `${st.count} canciones · ${fmtDur(st.duration)} de música · ${fmtBytes(st.size)}` : 'Todavía no hay canciones.';
  const grid = $('#songGrid');
  if (!songs.length) {
    grid.innerHTML = `<p class="empty" style="grid-column:1/-1">${S.songs.length ? 'Nada coincide con la búsqueda.' : 'Crea tu primera canción en <a href="#create">Crear</a>.'}</p>`;
    return;
  }
  grid.innerHTML = songs.map((s) => {
    const r = s.request || {};
    // En instrumentales el LM rellena igualmente un idioma: no se muestra porque no hay voz.
    const lang = !isInstrumental(s) && r.vocal_language && r.vocal_language !== 'unknown' && r.vocal_language.toUpperCase();
    const tags = [r.bpm && `${r.bpm} BPM`, r.keyscale, isInstrumental(s) ? 'Instrumental' : lang, s.task_type !== 'text2music' && s.task_type]
      .filter(Boolean).map((t) => `<span class="tag">${esc(t)}</span>`).join('');
    return `<article class="song-card" data-song="${s.id}">
      <div class="cover" data-play="${s.id}" style="background:${coverBg(s.id)}">${coverWaves(s.id)}
        ${s.variants > 1 ? `<span class="variant">v${s.variant}</span>` : ''}
        <button class="play-btn" type="button" aria-label="Reproducir">${icon('play')}</button>
        <span class="dur">${fmtTime(s.duration)}</span></div>
      <div class="sc-body">
        <div class="sc-title" data-open="${s.id}" title="${esc(s.title)}">${esc(s.title)}</div>
        <div class="sc-cap">${esc(cleanCaption(r.caption))}</div>
        <div class="sc-tags">${tags}</div>
      </div>
      <div class="sc-actions">
        <button class="icon-btn ${s.favorite ? 'on' : ''}" data-fav="${s.id}" aria-label="Favorita">${icon('heart')}</button>
        <a class="icon-btn" href="/api/songs/${s.id}/audio?download=true" aria-label="Descargar" download>${icon('download')}</a>
        <button class="icon-btn" data-reuse="${s.id}" aria-label="Reutilizar ajustes" title="Reutilizar ajustes">${icon('reuse')}</button>
        <span class="spacer"></span>
        <button class="icon-btn" data-more="${s.id}" aria-label="Más opciones">${icon('more')}</button>
      </div></article>`;
  }).join('');
  markPlaying();
}

function initLibrary() {
  $('#libSearch').addEventListener('input', debounce(renderLibrary, 150));
  $('#libFav').onclick = () => { S.libFav = !S.libFav; $('#libFav').setAttribute('aria-pressed', S.libFav); renderLibrary(); };
  $('#libFolder').onclick = () => api('/api/open-folder/library', { method: 'POST' }).catch((e) => toast(esc(e.message), 'err'));
  $('#songGrid').onclick = (e) => {
    const fav = e.target.closest('[data-fav]'); if (fav) return toggleFav(fav.dataset.fav);
    const more = e.target.closest('[data-more]'); if (more) return openSongMenu(more.dataset.more, more);
    const reuse = e.target.closest('[data-reuse]'); if (reuse) return reuseSong(reuse.dataset.reuse, false);
    const open = e.target.closest('[data-open]'); if (open) return openSong(open.dataset.open);
    const play = e.target.closest('[data-play]');
    if (play) {
      const ids = $$('#songGrid [data-song]').map((x) => x.dataset.song);
      if (P.current === play.dataset.play) togglePlay(); else playSong(play.dataset.play, ids);
    }
  };
}

// ------------------------------------------------------------ menú contextual
function openSongMenu(id, anchor) {
  const s = S.songs.find((x) => x.id === id); if (!s) return;
  const m = $('#menu');
  m.innerHTML = `
    <button data-a="open">${icon('info')}Detalles</button>
    <button data-a="reuse">${icon('reuse')}Reutilizar ajustes</button>
    <button data-a="seed">${icon('lock')}Reutilizar con la misma semilla</button>
    <button data-a="remix">${icon('wave')}Remix / cover de esta canción</button>
    <button data-a="repaint">${icon('edit')}Repintar o extender</button>
    <button data-a="ref">${icon('copy')}Usar como referencia de timbre</button>
    <hr>
    <button data-a="rename">${icon('edit')}Renombrar</button>
    <button data-a="dl">${icon('download')}Descargar</button>
    <button data-a="del" class="danger">${icon('trash')}Eliminar</button>`;
  m.hidden = false;
  const r = anchor.getBoundingClientRect();
  const mw = 240, mh = m.offsetHeight;
  m.style.left = `${Math.min(r.right - mw, innerWidth - mw - 8)}px`;
  m.style.top = `${r.bottom + mh + 8 > innerHeight ? Math.max(8, r.top - mh - 4) : r.bottom + 4}px`;
  m.onclick = (e) => {
    const a = e.target.closest('[data-a]')?.dataset.a; if (!a) return;
    closeMenu();
    ({ open: () => openSong(id), reuse: () => reuseSong(id, false), seed: () => reuseSong(id, true),
      remix: () => remixSong(id, 'cover'), repaint: () => remixSong(id, 'repaint'), ref: () => useAsRef(id),
      rename: () => renameSong(id), dl: () => (location.href = `/api/songs/${id}/audio?download=true`), del: () => deleteSong(id) })[a]();
  };
  setTimeout(() => document.addEventListener('click', closeMenu, { once: true }), 0);
}
function closeMenu() { $('#menu').hidden = true; }
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });

function reuseSong(id, keepSeed) {
  const s = S.songs.find((x) => x.id === id); if (!s) return;
  const up = s.user_params || {}, r = s.request || {};
  const p = { ...up };
  p.caption = up.caption || r.caption || '';
  p.lyrics = r.lyrics && r.lyrics !== '[Instrumental]' ? r.lyrics : (up.lyrics || '');
  p.instrumental = (r.lyrics || up.lyrics) === '[Instrumental]';
  for (const k of ['bpm', 'keyscale', 'timesignature', 'vocal_language']) if (r[k]) p[k] = r[k];
  if (r.duration) p.duration = Math.round(r.duration);
  p.seed = keepSeed && r.seed !== undefined ? r.seed : -1;
  p.lm_seed = keepSeed && r.lm_seed !== undefined ? r.lm_seed : -1;
  p.mode = s.task_type && s.task_type !== 'text2music' ? 'audio' : 'custom';
  if (p.mode === 'audio') p.src_song_id = s.source_song_id || '';
  writeForm(p);
  store.set('form', readForm());
  $('#songDialog').open && $('#songDialog').close();
  location.hash = '#create';
  toast(keepSeed ? 'Ajustes y semilla cargados: pulsa Crear para reproducirla o cambia algo' : 'Ajustes cargados en Crear', 'ok');
}

function remixSong(id, task) {
  const s = S.songs.find((x) => x.id === id); if (!s) return;
  clearAudio('src');
  writeForm({ mode: 'audio', task_type: task, src_song_id: id, caption: s.request?.caption || '', lyrics: s.request?.lyrics === '[Instrumental]' ? '' : (s.request?.lyrics || ''), instrumental: s.request?.lyrics === '[Instrumental]' });
  $('#srcSong').value = id;
  $('#songDialog').open && $('#songDialog').close();
  location.hash = '#create';
  toast(task === 'cover' ? 'Describe el nuevo estilo y pulsa Crear' : 'Ajusta la región a repintar y pulsa Crear', 'ok');
}

function useAsRef(id) {
  clearAudio('ref');
  $('#refSong').value = id; saveFormSoon();
  $('#advanced').open = true; setAdvTab('models');
  toast('Se usará como referencia de timbre (Ajustes avanzados → Modelos)', 'ok');
}

async function renameSong(id) {
  const s = S.songs.find((x) => x.id === id); if (!s) return;
  const t = await promptText('Renombrar canción', 'Título', s.title);
  if (!t) return;
  await api(`/api/songs/${id}`, { method: 'PATCH', body: { title: t } });
  await refreshSongs(); renderJobs();
  if ($('#songDialog').open) openSong(id);
  if (P.current === id) updatePlayerMeta();
}

async function deleteSong(id) {
  const s = S.songs.find((x) => x.id === id); if (!s) return;
  if (!(await confirmBox('Eliminar canción', `¿Eliminar «${s.title}» de la biblioteca? Se borrará el archivo de audio.`, 'Eliminar'))) return;
  if (P.current === id) stopPlayer();
  await api(`/api/songs/${id}`, { method: 'DELETE' });
  $('#songDialog').open && $('#songDialog').close();
  await refreshSongs(); renderJobs();
  toast('Canción eliminada');
}

// ------------------------------------------------------------ detalle
function openSong(id) {
  const s = S.songs.find((x) => x.id === id); if (!s) return;
  const r = s.request || {};
  const d = $('#songDialog');
  d.dataset.id = id;
  $('#dCover').style.background = coverBg(id);
  $('#dCover').querySelector('.cover-art')?.remove();
  $('#dCover').insertAdjacentHTML('afterbegin', coverWaves(id));
  $('#dTitle').textContent = s.title;
  $('#dDate').textContent = `${fmtDate(s.created_at)}${s.variants > 1 ? ` · variación ${s.variant} de ${s.variants}` : ''}`;
  const ts = { 2: '2/4', 3: '3/4', 4: '4/4', 6: '6/8' }[r.timesignature] || r.timesignature;
  const lang = isInstrumental(s) ? 'Instrumental' : LANGS.find(([c]) => c === r.vocal_language)?.[1];
  $('#dBadges').innerHTML = [fmtTime(s.duration), r.bpm && `${r.bpm} BPM`, r.keyscale, ts, lang, s.format?.toUpperCase(), s.task_type !== 'text2music' && s.task_type]
    .filter(Boolean).map((b) => `<span class="tag">${esc(b)}</span>`).join('') + (s.favorite ? '<span class="tag acc">♥ Favorita</span>' : '');
  $('#dCaption').textContent = cleanCaption(r.caption) || '—';
  $('#dLyrics').textContent = !r.lyrics || r.lyrics === '[Instrumental]' ? 'Instrumental' : r.lyrics;
  const t = s.timings || {};
  const up = s.user_params || {};
  const rows = [
    ['Semilla DiT', r.seed], ['Semilla LM', r.lm_seed], ['Pasos', up.inference_steps || 'auto'], ['Guidance', up.guidance_scale || 'auto'],
    ['Shift', up.shift || 'auto'], ['Solver', r.solver || up.solver], ['Modelo LM', s.used_lm ? (up.lm_model || 'predeterminado') : 'sin LM'],
    ['Modelo DiT', up.synth_model || 'predeterminado'], ['LoRA', up.adapter ? `${up.adapter} ×${up.adapter_scale || 1}` : null],
    ['Dispositivo', s.backend || s.device], ['Tiempo', [t.lm && `LM ${fmtDur(t.lm)}`, t.synth && `síntesis ${fmtDur(t.synth)}`, t.total && `total ${fmtDur(t.total)}`].filter(Boolean).join(' · ')],
    ['Archivo', `${s.file} · ${fmtBytes(s.size)}`],
  ].filter(([, v]) => v !== undefined && v !== null && v !== '');
  $('#dParams').innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  $('#dDownload').href = `/api/songs/${id}/audio?download=true`;
  syncDialogPlay();
  if (!d.open) d.showModal();
}

function initDialog() {
  const d = $('#songDialog');
  d.addEventListener('click', (e) => { if (e.target === d || e.target.closest('[data-close]')) d.close(); });
  $('#dPlay').onclick = () => { const id = d.dataset.id; if (P.current === id) togglePlay(); else playSong(id); };
  $('#dReuse').onclick = () => reuseSong(d.dataset.id, false);
  $('#dReuseSeed').onclick = () => reuseSong(d.dataset.id, true);
  $('#dRemix').onclick = () => remixSong(d.dataset.id, 'cover');
  $('#dRename').onclick = () => renameSong(d.dataset.id);
  $('#dDelete').onclick = () => deleteSong(d.dataset.id);
}
function syncDialogPlay() {
  const d = $('#songDialog');
  const playing = P.current === d.dataset.id && !P.audio.paused;
  $('#dPlay use').setAttribute('href', playing ? '#i-pause' : '#i-play');
}

function promptText(title, label, value = '') {
  return new Promise((resolve) => {
    const d = $('#promptDialog');
    $('#pdTitle').textContent = title;
    $('#pdLabel').textContent = label;
    const inp = $('#pdInput'); inp.parentElement.hidden = false; inp.required = true; inp.value = value;
    $('#pdOk').textContent = 'Aceptar'; $('#pdOk').className = 'btn primary sm';
    d.returnValue = '';
    inp.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); if (inp.value.trim()) d.close('ok'); } };
    d.onclose = () => resolve(d.returnValue === 'ok' ? inp.value.trim() : null);
    d.showModal(); inp.select();
  });
}
function confirmBox(title, text, ok = 'Aceptar') {
  return new Promise((resolve) => {
    const d = $('#promptDialog');
    $('#pdTitle').textContent = title;
    $('#pdLabel').textContent = text;
    const inp = $('#pdInput'); inp.required = false; inp.parentElement.querySelector('input').hidden = true;
    $('#pdOk').textContent = ok; $('#pdOk').className = 'btn danger sm';
    d.returnValue = '';
    d.onclose = () => { inp.hidden = false; resolve(d.returnValue === 'ok'); };
    d.showModal();
  });
}

// ============================================================ reproductor
const P = { audio: $('#audio'), list: [], current: null, ctx: null, analyser: null, raf: 0, seeking: false };

function initPlayer() {
  const a = P.audio;
  a.volume = store.get('vol', 0.9);
  $('#pVol').value = a.volume; setRangeFill($('#pVol'));
  $('#pPlay').onclick = togglePlay;
  $('#pNext').onclick = () => step(1);
  $('#pPrev').onclick = () => (a.currentTime > 3 ? (a.currentTime = 0) : step(-1));
  $('#pFav').onclick = () => P.current && toggleFav(P.current);
  $('#pMute').onclick = () => { a.muted = !a.muted; $('#pMute use').setAttribute('href', a.muted ? '#i-mute' : '#i-volume'); };
  $('#pVol').oninput = (e) => { a.volume = +e.target.value; setRangeFill(e.target); store.set('vol', a.volume); };
  const seek = $('#pSeek');
  seek.oninput = () => { P.seeking = true; setRangeFill(seek); $('#pTime').textContent = fmtTime((seek.value / 1000) * (a.duration || 0)); };
  seek.onchange = () => { if (a.duration) a.currentTime = (seek.value / 1000) * a.duration; P.seeking = false; };
  a.addEventListener('timeupdate', () => {
    if (P.seeking) return;
    const d = a.duration || 0;
    seek.value = d ? (a.currentTime / d) * 1000 : 0; setRangeFill(seek);
    $('#pTime').textContent = fmtTime(a.currentTime);
  });
  a.addEventListener('loadedmetadata', () => ($('#pDur').textContent = fmtTime(a.duration)));
  a.addEventListener('play', () => { syncPlayState(); startViz(); });
  a.addEventListener('pause', () => { syncPlayState(); stopViz(); });
  a.addEventListener('ended', () => step(1, true));
  document.addEventListener('keydown', (e) => {
    if (e.code !== 'Space' || e.target.closest('input, textarea, select, button, [contenteditable], dialog')) return;
    if (!P.current) return;
    e.preventDefault(); togglePlay();
  });
  if ('mediaSession' in navigator) {
    navigator.mediaSession.setActionHandler('play', () => a.play());
    navigator.mediaSession.setActionHandler('pause', () => a.pause());
    navigator.mediaSession.setActionHandler('nexttrack', () => step(1));
    navigator.mediaSession.setActionHandler('previoustrack', () => step(-1));
  }
  seek.value = 0; setRangeFill(seek);
}

function playSong(id, list) {
  const s = S.songs.find((x) => x.id === id);
  if (!s) return;
  if (list?.length) P.list = list;
  else if (!P.list.includes(id)) P.list = S.songs.map((x) => x.id);
  P.current = id;
  P.audio.src = `/api/songs/${id}/audio`;
  P.audio.play().catch(() => {});
  updatePlayerMeta();
}
function updatePlayerMeta() {
  const s = S.songs.find((x) => x.id === P.current);
  if (!s) return;
  $('#pTitle').textContent = s.title;
  $('#pSub').textContent = songSub(s);
  $('#pCover').style.background = coverBg(s.id);
  $('#pDownload').href = `/api/songs/${s.id}/audio?download=true`;
  syncPlayerFav(); markPlaying();
  if ('mediaSession' in navigator) navigator.mediaSession.metadata = new MediaMetadata({ title: s.title, artist: 'Open Suno', album: songSub(s) });
}
function syncPlayerFav() {
  const s = S.songs.find((x) => x.id === P.current);
  $('#pFav').classList.toggle('on', !!s?.favorite);
}
function togglePlay() {
  if (!P.current) { if (S.songs[0]) playSong(S.songs[0].id); return; }
  if (P.audio.paused) P.audio.play().catch(() => {}); else P.audio.pause();
}
function step(dir, auto = false) {
  if (!P.list.length || !P.current) return;
  const i = P.list.indexOf(P.current) + dir;
  if (i < 0 || i >= P.list.length) { if (auto) syncPlayState(); return; }
  playSong(P.list[i]);
}
function stopPlayer() {
  P.audio.pause(); P.audio.removeAttribute('src'); P.audio.load(); P.current = null;
  $('#pTitle').textContent = 'Nada sonando'; $('#pSub').textContent = '—';
  markPlaying(); syncPlayState();
}
function syncPlayState() {
  const playing = !P.audio.paused;
  $('#pPlay use').setAttribute('href', playing ? '#i-pause' : '#i-play');
  $('#pPlay').setAttribute('aria-label', playing ? 'Pausar' : 'Reproducir');
  markPlaying(); syncDialogPlay();
}
function markPlaying() {
  const playing = !P.audio.paused;
  $$('[data-song]').forEach((el) => {
    const on = el.dataset.song === P.current;
    el.classList.toggle('playing', on);
    const u = el.querySelector('.play-btn use, .mini-cover use');
    if (u) u.setAttribute('href', on && playing ? '#i-pause' : '#i-play');
  });
}

function startViz() {
  try {
    if (!P.ctx) {
      P.ctx = new (window.AudioContext || window.webkitAudioContext)();
      const src = P.ctx.createMediaElementSource(P.audio);
      P.analyser = P.ctx.createAnalyser(); P.analyser.fftSize = 64;
      src.connect(P.analyser); P.analyser.connect(P.ctx.destination);
    }
    P.ctx.resume();
  } catch { return; }
  const cv = $('#pViz'), g = cv.getContext('2d'), data = new Uint8Array(P.analyser.frequencyBinCount);
  cancelAnimationFrame(P.raf);
  const draw = () => {
    P.analyser.getByteFrequencyData(data);
    g.clearRect(0, 0, cv.width, cv.height);
    const n = 9, w = cv.width / n;
    for (let i = 0; i < n; i++) {
      const v = data[i * 2 + 1] / 255, bh = Math.max(4, v * cv.height * 0.8);
      g.fillStyle = 'rgba(255,255,255,.85)';
      g.beginPath(); g.roundRect(i * w + w * 0.25, cv.height - bh - 8, w * 0.5, bh, 4); g.fill();
    }
    P.raf = requestAnimationFrame(draw);
  };
  draw();
}
function stopViz() { cancelAnimationFrame(P.raf); const cv = $('#pViz'); cv.getContext('2d').clearRect(0, 0, cv.width, cv.height); }

// ============================================================ MOTOR
function deviceLabel(id, short = false) {
  if (!id || id === 'auto') return short ? 'Auto' : 'Automático';
  const d = S.devices?.devices?.find((x) => x.id === id);
  if (id === 'CPU') return short ? 'CPU' : `CPU · ${d?.name || ''}`;
  const kind = id.startsWith('CUDA') ? 'CUDA' : id.startsWith('Vulkan') ? 'Vulkan' : id;
  const name = (d?.name || id).replace(/^NVIDIA\s+|^AMD\s+/, '').replace(/\s*GeForce\s*/, ' ').replace(/\s+(GPU|Graphics)$/, '').trim();
  return short ? `GPU ${name} (${kind})` : `GPU · ${d?.name || id} (${kind})`;
}

async function loadSettings() {
  const d = await api('/api/settings');
  S.settings = d.settings; S.profiles = d.profiles;
  fillEngForm();
  renderProfiles(); renderDevices();
  updateCreateInfo();
}

function fillEngForm() {
  const e = S.settings.engine;
  $$('[data-eng]').forEach((el) => {
    const v = e[el.dataset.eng];
    if (el.type === 'checkbox') el.checked = !!v; else el.value = v ?? '';
  });
  S.engDirty = false; syncEngDirty();
}
function readEngForm() {
  const o = {};
  $$('[data-eng]').forEach((el) => (o[el.dataset.eng] = el.type === 'checkbox' ? el.checked : (el.type === 'number' ? +el.value : el.value)));
  return o;
}
function syncEngDirty() {
  const e = S.settings?.engine || {};
  const cur = readEngForm();
  S.engDirty = Object.entries(cur).some(([k, v]) => String(v) !== String(e[k]));
  $('#engDirty').textContent = S.engDirty ? 'Cambios sin guardar.' : 'Sin cambios.';
  $('#engDirty').classList.toggle('dirty', S.engDirty);
}

async function saveEngine(restart) {
  const patch = { engine: { ...readEngForm(), profile: 'custom' } };
  try {
    const d = await api('/api/settings', { method: 'PUT', body: patch });
    S.settings = d.settings; fillEngForm(); renderProfiles();
    if (restart) await engineAction('restart');
    else toast(d.needs_restart ? 'Guardado. Reinicia el motor para aplicarlo.' : 'Guardado', 'ok');
    tick();
  } catch (e) { toast(esc(e.message), 'err'); }
}

async function setDevice(id) {
  try {
    const d = await api('/api/settings', { method: 'PUT', body: { engine: { device: id, profile: 'custom' } } });
    S.settings = d.settings; fillEngForm(); renderDevices(); renderProfiles(); updateCreateInfo();
    if (S.st?.engine?.state === 'running') toast(`Dispositivo: ${esc(deviceLabel(id))}. Reinicia el motor para aplicarlo.`, 'ok', { label: 'Reiniciar', fn: () => engineAction('restart') });
    else toast(`Dispositivo: ${esc(deviceLabel(id))}`, 'ok');
    tick();
  } catch (e) { toast(esc(e.message), 'err'); }
}

async function applyProfile(name) {
  try {
    const d = await api(`/api/settings/profile/${name}`, { method: 'POST' });
    S.settings = d.settings; fillEngForm(); renderDevices(); renderProfiles(); updateCreateInfo();
    const e = d.settings.engine;
    const msg = `Perfil «${esc(S.profiles[name].label)}»: ${esc(deviceLabel(e.device, true))}, VAE chunk ${e.vae_chunk}`;
    if (S.st?.engine?.state === 'running') toast(msg + '. Reinicia para aplicarlo.', 'ok', { label: 'Reiniciar', fn: () => engineAction('restart') }, 8000);
    else toast(msg, 'ok');
    tick();
  } catch (e) { toast(esc(e.message), 'err'); }
}

async function engineAction(action) {
  if (action !== 'start' && S.st?.jobs_active) {
    const verb = action === 'stop' ? 'Detener' : 'Reiniciar';
    const ok = await confirmBox(`${verb} el motor`, `Hay ${S.st.jobs_active} generación(es) en curso o en cola; se interrumpirá la que se esté generando. ¿Continuar?`, verb);
    if (!ok) return;
  }
  const labels = { start: 'Iniciando el motor…', restart: 'Reiniciando el motor…', stop: 'Deteniendo el motor…' };
  toast(labels[action], '', null, 2500);
  $$('#engStart, #engStop, #engRestart, #restartNow').forEach((b) => (b.disabled = true));
  const p = api(`/api/engine/${action}`, { method: 'POST' });
  setTimeout(tick, 300);
  try {
    const st = await p;
    if (st.state === 'error') toast(`El motor falló: ${esc(st.error || '')}`, 'err', null, 10000);
    else if (st.state === 'running') toast('Motor en marcha', 'ok', null, 2500);
  } catch (e) { toast(esc(e.message), 'err'); }
  tick();
}

function renderEngineStatus() {
  const e = S.st?.engine; if (!e) return;
  $('#statusOrb').className = `status-orb ${e.state}`;
  $('#engState').textContent = STATE_LABEL[e.state] || e.state;
  const running = e.state === 'running';
  $('#engSub').textContent = e.state === 'error' ? (e.error || '') : running ? deviceLabel(e.device) : (e.binary_exists ? 'Pulsa Iniciar para arrancar ace-server.' : 'Motor no instalado: ve a Modelos.');
  const kv = [
    ['Dispositivo', running ? deviceLabel(e.device) : deviceLabel(S.settings?.engine.device)],
    ['Backend activo', e.backend || (running ? 'se asigna al cargar el primer modelo' : '—')],
    ['Hilos CPU', e.cpu_threads],
    ['Versión', e.version ? `acestep.cpp ${e.version}` : '—'],
    ['PID', e.pid], ['RAM del proceso', e.ram_mb ? `${e.ram_mb} MB` : null],
    ['Tiempo activo', e.uptime ? fmtDur(e.uptime) : null], ['URL', e.url],
  ].filter(([, v]) => v !== null && v !== undefined);
  $('#engKv').innerHTML = kv.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  const busy = ['starting', 'stopping'].includes(e.state);
  $('#engStart').disabled = running || busy || !e.binary_exists;
  $('#engStop').disabled = !running || busy;
  $('#engRestart').disabled = busy || !e.binary_exists;
  $('#restartNow').disabled = busy;
  $('#engWebui').href = e.url; $('#engWebui').hidden = !running;
  $('#restartBanner').hidden = !e.needs_restart;
}

function renderPill() {
  const e = S.st?.engine; if (!e) return;
  const busyJobs = S.st.jobs_active > 0 && e.state === 'running';
  $('#enginePill .dot').className = `dot ${busyJobs ? 'busy' : e.state}`;
  $('#pillState').textContent = busyJobs ? 'Generando…' : (STATE_LABEL[e.state] || e.state);
  const dev = e.state === 'running' ? e.device : S.settings?.engine.device;
  $('#pillDevice').textContent = deviceLabel(dev, true);
}

async function loadDevices(refresh = false) {
  const btn = $('#devRefresh');
  if (refresh) { btn.disabled = true; btn.innerHTML = `${icon('refresh')}Detectando…`; }
  try { S.devices = await api(`/api/devices${refresh ? '?refresh=true' : ''}`); }
  catch (e) { toast(esc(e.message), 'err'); }
  finally { btn.disabled = false; btn.innerHTML = `${icon('refresh')}Detectar`; }
  renderDevices(); renderPill(); updateCreateInfo();
}

function renderDevices() {
  const list = $('#deviceList');
  const d = S.devices, cur = S.settings?.engine.device || 'auto';
  if (!d) { list.innerHTML = '<p class="empty">Detectando dispositivos…</p>'; return; }
  const items = [{ id: 'auto', kind: 'auto', name: 'Automático', sub: `GGML elige la mejor GPU${d.best_gpu ? ` (probablemente ${deviceLabel(d.best_gpu, true)})` : ''}` }];
  for (const x of d.devices || []) {
    let sub;
    if (x.kind === 'cpu') sub = `Procesador${d.cpu_variant ? ` · optimizado ${d.cpu_variant}` : ''} · sin GPU`;
    else sub = `${x.kind === 'cuda' ? 'NVIDIA CUDA' : 'Vulkan'}${x.integrated ? ' · integrada (memoria compartida)' : ' · dedicada'}${x.vram_mb ? ` · ${(x.vram_mb / 1024).toFixed(0)} GB VRAM` : ''}`;
    items.push({ ...x, sub });
  }
  list.innerHTML = items.map((x) => `
    <button type="button" class="device ${x.id === cur ? 'active' : ''}" data-dev="${x.id}" role="radio" aria-checked="${x.id === cur}">
      <span class="dev-ico">${icon(x.kind === 'cpu' ? 'cpu' : x.kind === 'auto' ? 'sparkles' : 'zap')}</span>
      <span class="dev-txt"><b>${esc(x.kind === 'auto' ? x.name : x.kind === 'cpu' ? `CPU · ${x.name}` : x.name)} <span class="hint">${x.kind === 'auto' ? '' : esc(x.id)}</span></b><small>${esc(x.sub)}</small></span>
      <span class="radio"></span>
    </button>`).join('') + (d.ok ? '' : `<div class="warn err">${icon('alert')}<span>${esc(d.error || 'No se pudieron detectar dispositivos.')}</span></div>`);
  $('#cudaHint').hidden = !(d.cuda_hint && !d.cuda_runtime);
}

function renderProfiles() {
  const cur = S.settings?.engine.profile;
  $('#profileGrid').innerHTML = Object.entries(S.profiles).map(([k, p]) => `
    <button type="button" class="profile ${k === cur ? 'active' : ''}" data-profile="${k}">
      <b>${icon(k === 'cpu' ? 'cpu' : 'zap')}${esc(p.label)}</b><small>${esc(p.description)}</small>
    </button>`).join('');
}

async function renderHardware() {
  let hw; try { hw = await api('/api/hardware'); } catch { return; }
  $('#hwOs').textContent = hw.os;
  const cards = [];
  cards.push(`<div class="hw"><b>${esc(hw.cpu.name)}</b><small>${hw.cpu.cores} núcleos · ${hw.cpu.threads} hilos · uso ${Math.round(hw.cpu.usage)}%</small>
    <div class="meter"><i style="width:${hw.cpu.usage}%"></i></div></div>`);
  const ramP = (hw.ram.used_gb / hw.ram.total_gb) * 100;
  cards.push(`<div class="hw"><b>Memoria RAM</b><small>${hw.ram.used_gb} / ${hw.ram.total_gb} GB</small><div class="meter"><i style="width:${ramP}%"></i></div></div>`);
  for (const g of hw.nvidia) {
    const p = g.memory_total_mb ? (g.memory_used_mb / g.memory_total_mb) * 100 : 0;
    cards.push(`<div class="hw"><b>${esc(g.name)}</b><small>VRAM ${(g.memory_used_mb / 1024).toFixed(1)} / ${(g.memory_total_mb / 1024).toFixed(1)} GB · uso ${g.utilization ?? '—'}% · ${g.temperature ?? '—'} °C · driver ${esc(g.driver)}</small>
      <div class="meter"><i style="width:${p}%"></i></div></div>`);
  }
  $('#hwGrid').innerHTML = cards.join('');
}

// registro del motor (SSE)
function logClass(l) {
  if (l.startsWith('[Open Suno]')) return 'l-os';
  if (/FATAL|ERROR|failed|OutOfDeviceMemory/i.test(l)) return 'l-err';
  if (/WARN/i.test(l)) return 'l-warn';
  if (/\] Step|Total:|Decode:|done/.test(l)) return 'l-step';
  if (/^\[(Load|Store|GGUF|WeightCtx)\]/.test(l)) return 'l-load';
  return '';
}
function appendLogs(lines) {
  if (!lines.length) return;
  S.logs.push(...lines.map((x) => x.line));
  if (S.logs.length > 5000) S.logs.splice(0, S.logs.length - 5000);
  const filter = $('#logFilter').value.trim().toLowerCase();
  const con = $('#console');
  const frag = document.createDocumentFragment();
  for (const { line } of lines) {
    if (filter && !line.toLowerCase().includes(filter)) continue;
    const span = document.createElement('span');
    span.className = logClass(line);
    span.textContent = line + '\n';
    frag.append(span);
  }
  con.append(frag);
  while (con.childNodes.length > 3000) con.firstChild.remove();
  if ($('#logFollow').checked) con.scrollTop = con.scrollHeight;
}
function rerenderLogs() {
  $('#console').innerHTML = '';
  const all = S.logs.map((line) => ({ line })); S.logs = [];
  appendLogs(all);
}
async function enterEngine() {
  // Los ajustes pueden haber cambiado fuera de esta pestaña (otra ventana, el instalador…).
  if (!S.engDirty) await loadSettings().catch(() => {});
  renderEngineStatus(); renderHardware();
  if (!S.devices) loadDevices();
  if (!S.es) {
    if (!S.logSeq) {
      const d = await api('/api/engine/logs?since=0&limit=1500');
      S.logSeq = d.seq; $('#console').innerHTML = ''; S.logs = []; appendLogs(d.lines);
    }
    S.es = new EventSource(`/api/engine/logs/stream?since=${S.logSeq}`);
    S.es.onmessage = (ev) => { const lines = JSON.parse(ev.data); if (lines.length) S.logSeq = lines[lines.length - 1].seq; appendLogs(lines); };
    S.es.onerror = () => { S.es?.close(); S.es = null; setTimeout(() => S.view === 'engine' && enterEngine(), 2000); };
  }
  clearInterval(S.hwTimer); S.hwTimer = setInterval(renderHardware, 3000);
}
function leaveEngine() {
  S.es?.close(); S.es = null;
  clearInterval(S.hwTimer);
}

function initEngine() {
  $('#engStart').onclick = () => engineAction('start');
  $('#engStop').onclick = () => engineAction('stop');
  $('#engRestart').onclick = () => engineAction('restart');
  $('#restartNow').onclick = () => engineAction('restart');
  $('#enginePill').onclick = () => (location.hash = '#engine');
  $('#devRefresh').onclick = () => loadDevices(true);
  $('#deviceList').onclick = (e) => { const b = e.target.closest('[data-dev]'); if (b && b.dataset.dev !== S.settings.engine.device) setDevice(b.dataset.dev); };
  $('#profileGrid').onclick = (e) => { const b = e.target.closest('[data-profile]'); if (b) applyProfile(b.dataset.profile); };
  $$('#engForm, #engForm2').forEach((f) => { f.addEventListener('input', syncEngDirty); f.addEventListener('change', syncEngDirty); f.onsubmit = (e) => e.preventDefault(); });
  $('#engSave').onclick = () => saveEngine(false);
  $('#engSaveRestart').onclick = () => saveEngine(true);
  $('#logClear').onclick = () => { S.logs = []; $('#console').innerHTML = ''; };
  $('#logFilter').addEventListener('input', debounce(rerenderLogs, 200));
}

// ============================================================ MODELOS
async function enterModels() {
  await loadCatalog();
  refreshDownloads();
}
async function loadCatalog(refresh = false) {
  try { S.catalog = await api(`/api/downloads/catalog${refresh ? '?refresh=true' : ''}`); } catch (e) { toast(esc(e.message), 'err'); return; }
  renderModels();
}

function renderModels() {
  const c = S.catalog, st = S.st?.setup; if (!c) return;
  const inst = (role) => c.models.filter((m) => m.role === role && m.installed).map((m) => m.name);
  const items = [
    ['Motor acestep.cpp', c.engine.installed, c.engine.installed ? 'Instalado' : 'Falta'],
    ['Codificador de texto', inst('embedding').length, inst('embedding').join(', ') || 'Falta'],
    ['LM (compositor)', inst('lm').length, inst('lm').join(', ') || 'Falta'],
    ['DiT (síntesis)', inst('dit').length, inst('dit').join(', ') || 'Falta'],
    ['VAE', inst('vae').length, inst('vae').join(', ') || 'Falta'],
  ];
  $('#checklist').innerHTML = items.map(([n, ok, d]) => `<li class="${ok ? 'ok' : ''}"><span class="ck">${icon(ok ? 'check' : 'x')}</span>${esc(n)}<small>${esc(d)}</small></li>`).join('') +
    (st?.nvidia_gpu ? `<li class="${c.cuda.installed ? 'ok' : 'opt'}"><span class="ck">${icon(c.cuda.installed ? 'check' : 'info')}</span>Runtime CUDA<small>${c.cuda.installed ? 'Instalado' : 'Opcional (NVIDIA)'}</small></li>` : '');
  const missingRec = c.models.filter((m) => m.recommended && !m.installed);
  $('#installMissing').disabled = c.engine.installed && !missingRec.length;
  $('#installMissing').innerHTML = `${icon('download')}${c.engine.installed && !missingRec.length ? 'Todo instalado' : 'Descargar lo que falta'}`;

  const ver = S.st?.engine?.version;
  $('#engBinInfo').innerHTML = !c.engine.platform_supported
    ? 'En Linux/macOS compila acestep.cpp (<code>./buildcuda.sh</code>, <code>./buildvulkan.sh</code> o <code>./buildcpu.sh</code>) y apunta «Carpeta de binarios» en Motor a <code>build/</code>.'
    : c.engine.installed
      ? `Instalado${ver ? ` · <b>${esc(ver)}</b>` : ''} · backends: CPU${c.engine.has_cuda_backend ? ', CUDA' : ''}${c.engine.has_vulkan_backend ? ', Vulkan' : ''}.<br>Fuente: binarios oficiales de Windows enlazados en el README de acestep.cpp.`
      : 'No instalado. Se descargan los binarios precompilados de Windows (≈170 MB) con CPU, CUDA y Vulkan.';
  $('#engDownload').disabled = !c.engine.platform_supported;

  const cudaNeed = c.cuda.required_major;
  $('#cudaInfo').innerHTML = !cudaNeed
    ? 'Instala primero el motor.'
    : c.cuda.installed
      ? `Instalado (cuBLAS ${cudaNeed}.x) en <code>${esc(c.cuda.dir)}</code>. Detecta dispositivos en Motor para ver la GPU CUDA.`
      : `El backend CUDA de este motor necesita cuBLAS ${cudaNeed}.x. Se descarga de PyPI (paquetes oficiales de NVIDIA, ≈430 MB). <b>No es imprescindible</b>: tu GPU también funciona por Vulkan.`;
  $('#cudaDownload').disabled = !cudaNeed || c.cuda.installed;

  // pestañas por rol
  const roles = ['dit', 'lm', 'embedding', 'vae'];
  $('#roleTabs').innerHTML = roles.map((r) => `<button type="button" data-role="${r}" class="${r === S.roleTab ? 'active' : ''}">${esc(c.roles[r].label)} <span class="hint">${c.models.filter((m) => m.role === r && m.installed).length}/${c.models.filter((m) => m.role === r).length}</span></button>`).join('');
  $('#roleHelp').textContent = c.roles[S.roleTab]?.help || '';
  const dlActive = new Map(S.downloads.filter((t) => ['queued', 'running'].includes(t.status)).map((t) => [t.name, t]));
  const rows = c.models.filter((m) => m.role === S.roleTab)
    .sort((a, b) => (b.recommended - a.recommended) || (b.installed - a.installed) || a.name.localeCompare(b.name));
  $('#modelTable').innerHTML = `<div class="mrow head"><span>Archivo</span><span class="mnote">Descripción</span><span class="mquant">Cuant.</span><span>Tamaño</span><span></span></div>` +
    rows.map((m) => {
      const dl = dlActive.get(m.name);
      const badges = `${m.recommended ? '<span class="tag acc">Recomendado</span>' : ''}${m.installed ? '<span class="tag ok">Instalado</span>' : ''}${m.partial && !m.installed ? '<span class="tag warn">Parcial</span>' : ''}`;
      const act = m.installed
        ? `<button class="btn ghost xs" data-mdel="${esc(m.name)}" title="Eliminar del disco">${icon('trash')}</button>`
        : dl ? `<span class="hint">${dl.status === 'queued' ? 'En cola' : Math.round((dl.done / (dl.total || 1)) * 100) + '%'}</span>`
          : `<button class="btn ghost xs" data-mdl="${esc(m.name)}">${icon('download')}Descargar</button>`;
      return `<div class="mrow ${m.installed ? 'installed' : ''}"><span class="mname" title="${esc(m.name)}">${esc(m.name)}</span><span class="mnote">${esc(m.note)} ${badges}</span><span class="mquant msize">${esc(m.quant)}</span><span class="msize">${fmtBytes(m.size)}</span><span class="mact">${act}</span></div>`;
    }).join('');
}

async function refreshDownloads() {
  let d; try { d = await api('/api/downloads'); } catch { return; }
  const before = S.downloads.filter((t) => t.status === 'running').map((t) => t.id).join();
  S.downloads = d.tasks;
  const card = $('#dlCard');
  card.hidden = !S.downloads.length;
  $('#dlList').innerHTML = S.downloads.map((t) => {
    const pct = t.total ? (t.done / t.total) * 100 : 0;
    const info = t.status === 'running'
      ? `${fmtBytes(t.done)} / ${fmtBytes(t.total)} · ${fmtBytes(t.speed)}/s${t.speed && t.total ? ` · ${fmtTime((t.total - t.done) / t.speed)} restante` : ''}`
      : t.status === 'failed' ? `Error: ${t.error}` : { queued: 'En cola', done: 'Completado', cancelled: 'Cancelado' }[t.status];
    return `<div class="dl"><span class="dl-name">${esc(t.label)}${t.current_file && t.status === 'running' && t.kind !== 'model' ? ` · <span class="hint">${esc(t.current_file)}</span>` : ''}</span>
      <span class="dl-info">${esc(info)}</span>
      ${['running', 'queued'].includes(t.status) ? `<div class="bar"><i style="width:${Math.max(pct, 1)}%"></i></div>` : ''}</div>`;
  }).join('');
  const nowRunning = S.downloads.filter((t) => t.status === 'running').map((t) => t.id).join();
  if (before !== nowRunning && S.view === 'models') { await loadCatalog(); loadModelsList(); }
  else if (S.view === 'models') renderModels();
}

async function startDownloads(items) {
  try {
    const d = await api('/api/downloads', { method: 'POST', body: { items } });
    if (!d.tasks.length) toast('No hay nada pendiente de descargar', 'ok');
    else toast(`${d.tasks.length} descarga(s) en cola`, 'ok', null, 2500);
    refreshDownloads();
  } catch (e) { toast(esc(e.message), 'err'); }
}

function initModels() {
  $('#catRefresh').onclick = () => loadCatalog(true);
  $('#installMissing').onclick = () => startDownloads([...(S.catalog?.engine.installed ? [] : ['engine']), 'recommended']);
  $('#engDownload').onclick = async () => {
    if (S.catalog?.engine.installed && !(await confirmBox('Actualizar motor', 'Se descargará la última compilación de acestep.cpp y el motor se reiniciará al instalarla.', 'Actualizar'))) return;
    startDownloads(['engine']);
  };
  $('#cudaDownload').onclick = async () => {
    if (!(await confirmBox('Instalar runtime CUDA', 'Se descargarán ≈430 MB de PyPI (nvidia-cublas y nvidia-cuda-runtime) y se extraerán las DLL en engine/cuda. ¿Continuar?', 'Descargar'))) return;
    startDownloads(['cuda']);
  };
  $('#dlCancelAll').onclick = async () => { await api('/api/downloads/cancel', { method: 'POST', body: {} }); refreshDownloads(); };
  $('#dlClear').onclick = async () => { await api('/api/downloads/clear', { method: 'POST' }); refreshDownloads(); };
  $('#roleTabs').onclick = (e) => { const b = e.target.closest('[data-role]'); if (!b) return; S.roleTab = b.dataset.role; store.set('roleTab', S.roleTab); renderModels(); };
  $('#modelTable').onclick = async (e) => {
    const dl = e.target.closest('[data-mdl]');
    if (dl) return startDownloads([`model:${dl.dataset.mdl}`]);
    const del = e.target.closest('[data-mdel]');
    if (del) {
      if (!(await confirmBox('Eliminar modelo', `¿Borrar ${del.dataset.mdel} del disco? El motor se detendrá si está en marcha.`, 'Eliminar'))) return;
      try { await api(`/api/models/${encodeURIComponent(del.dataset.mdel)}`, { method: 'DELETE' }); toast('Modelo eliminado'); } catch (err) { toast(esc(err.message), 'err'); }
      loadCatalog(); loadModelsList(); tick();
    }
  };
  $$('[data-folder]').forEach((b) => (b.onclick = () => api(`/api/open-folder/${b.dataset.folder}`, { method: 'POST' }).catch((e) => toast(esc(e.message), 'err'))));
}

async function loadModelsList() {
  try { S.models = await api('/api/models'); } catch { return; }
  const m = S.models.engine_models || S.models.models;
  const fill = (sel, arr, extra = '') => {
    const el = $(sel), cur = el.value || store.get('form')?.[el.dataset.field] || '';
    el.innerHTML = extra + (arr || []).map((n) => `<option value="${esc(n)}">${esc(n.replace('.gguf', ''))}</option>`).join('');
    if ([...el.options].some((o) => o.value === cur)) el.value = cur;
  };
  fill('#f-lm', m.lm);
  fill('#f-dit', m.dit);
  fill('#f-adapter', S.models.engine_adapters || S.models.adapters, '<option value="">Ninguno</option>');
  updateCreateInfo(); checkTaskWarn();
}

// ============================================================ ciclo de estado
let tickTimer = 0, ticking = false;
async function tick() {
  clearTimeout(tickTimer);
  if (ticking) { tickTimer = setTimeout(tick, 500); return; }
  ticking = true;
  try {
    const prevReady = S.st?.setup?.ready, prevState = S.st?.engine?.state;
    S.st = await api('/api/state');
    renderPill();
    $('#setupDot').hidden = S.st.setup.ready;
    if (S.view === 'engine') renderEngineStatus();
    if (S.st.jobs_active || S.jobs.some((j) => ['queued', 'running'].includes(j.status)) || S.view === 'create') await refreshJobs();
    if (S.st.downloads_active || (S.view === 'models' && S.downloads.some((t) => ['queued', 'running'].includes(t.status)))) await refreshDownloads();
    if (prevReady === false && S.st.setup.ready) { toast('¡Todo instalado! Ya puedes crear música.', 'ok'); loadModelsList(); }
    if (prevState !== S.st.engine.state && S.st.engine.state === 'running') loadModelsList();
    if (!S.st.setup.ready && S.view === 'create') showSetupHint(); else hideSetupHint();
  } catch { /* servidor no disponible: reintenta */ }
  ticking = false;
  const busy = S.st?.jobs_active || S.st?.downloads_active || ['starting', 'stopping'].includes(S.st?.engine?.state);
  tickTimer = setTimeout(tick, busy ? 1000 : 3000);
}

function showSetupHint() {
  if ($('#setupHint')) return;
  const el = h(`<div class="warn info" id="setupHint">${icon('info')}<span>Falta instalar el motor o algún modelo. Ve a <a href="#models">Modelos</a> y pulsa «Descargar lo que falta» (≈3,5 GB).</span></div>`);
  $('#view-create .preset-bar').before(el);
}
function hideSetupHint() { $('#setupHint')?.remove(); }

// ============================================================ tema
function initTheme() {
  let theme = store.get('theme');
  if (!theme) theme = matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  const apply = (t) => {
    document.documentElement.dataset.theme = t;
    $('#themeBtn use').setAttribute('href', t === 'dark' ? '#i-sun' : '#i-moon');
    store.set('theme', t);
  };
  apply(theme);
  $('#themeBtn').onclick = () => apply(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
}

// ============================================================ arranque
// Ningún error de la API debe perderse en silencio.
window.addEventListener('unhandledrejection', (e) => {
  const msg = e.reason?.message || String(e.reason || 'Error desconocido');
  toast(esc(msg), 'err');
});

async function init() {
  initTheme();
  initCreate();
  initLibrary();
  initPlayer();
  initDialog();
  initEngine();
  initModels();
  try {
    await Promise.all([loadSettings(), refreshSongs(), loadPresets(), loadModelsList()]);
  } catch (e) { toast(`No se pudo conectar con Open Suno: ${esc(e.message)}`, 'err'); }
  setView(location.hash.slice(1) || 'create');
  loadDevices();
}
init();
