'use strict';
// Piano flashcards client. The server (/api/*) owns FSRS scheduling and history; this page picks the
// due cards in the chosen scope, shows them in random order, times note-on-screen -> answer start,
// and re-inserts missed/slow cards later in the same session.
const $ = id => document.getElementById(id);
const LETTERS = 'CDEFGAB';
const VIEWS = ['tokenView', 'homeView', 'drillView', 'doneView', 'statsView'];
const params = new URLSearchParams(location.search);
let token = localStorage.getItem('pf_token') || '';
let cards = [], byId = {}, cfg = {easy_ms: 1000, good_ms: 2000};
let scopes = [], scope = null;

function show(v) { VIEWS.forEach(x => $(x).classList.toggle('hidden', x !== v)); }

// ---- API
async function api(path, body) {
  const r = await fetch('api/' + path, {
    method: body ? 'POST' : 'GET', keepalive: !!body,
    headers: {'X-Access-Token': token, ...(body ? {'Content-Type': 'application/json'} : {})},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) { localStorage.removeItem('pf_token'); token = ''; show('tokenView'); throw new Error('unauthorized'); }
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}
async function loadState() {
  const st = await api('state');
  cards = st.cards; cfg = st; byId = Object.fromEntries(cards.map(c => [c.id, c]));
}

// ---- scopes
const name = c => c.letter + c.octave;
function builtinScopes() {
  const f = (label, sub, test) => ({label, sub, ids: cards.filter(test).map(c => c.id)});
  return [
    f('Treble · staff', 'D4–G5', c => c.clef === 'treble' && !c.ledger),
    f('Treble · ledger', 'G3–C4, A5–E6', c => c.clef === 'treble' && c.ledger),
    f('Treble · all', 'G3–E6', c => c.clef === 'treble'),
    f('Bass · staff', 'F2–B3', c => c.clef === 'bass' && !c.ledger),
    f('Bass · ledger', 'C2–E2, C4–E4', c => c.clef === 'bass' && c.ledger),
    f('Bass · all', 'C2–E4', c => c.clef === 'bass'),
    f('Both staffs', 'no ledger lines', c => !c.ledger),
    f('Everything', 'all 37 notes', () => true),
  ];
}
function savedScopes() { try { return JSON.parse(localStorage.getItem('pf_scopes') || '[]'); } catch { return []; } }
function rangeScope(clef, lo, hi, label) {
  const [a, b] = [Math.min(lo, hi), Math.max(lo, hi)];
  const ids = cards.filter(c => c.clef === clef && c.pos >= a && c.pos <= b).map(c => c.id);
  return {label: label || `${clef === 'treble' ? 'Treble' : 'Bass'} ${name(byId[ids[0]])}–${name(byId[ids[ids.length - 1]])}`,
          sub: 'custom', ids, custom: {clef, lo: a, hi: b}};
}
function dueIn(ids) { return ids.filter(id => byId[id] && byId[id].is_due).length; }
function renderScopes() {
  scopes = [...builtinScopes(), ...savedScopes().map(s => rangeScope(s.clef, s.lo, s.hi, s.label))];
  const last = localStorage.getItem('pf_scope');
  scope = scopes.find(s => s.label === last) || scope && scopes.find(s => s.label === scope.label) || scopes[0];
  $('scopes').innerHTML = '';
  scopes.forEach(s => {
    const b = document.createElement('button');
    b.className = 'scope' + (s === scope ? ' sel' : '');
    b.dataset.label = s.label;
    const due = dueIn(s.ids);
    b.innerHTML = `<b></b><span>${s.sub} · ${due ? due + ' due' : 'nothing due'}</span>`;
    b.querySelector('b').textContent = s.label;
    b.onclick = () => { scope = s; localStorage.setItem('pf_scope', s.label); renderScopes(); };
    $('scopes').appendChild(b);
  });
  const due = dueIn(scope.ids);
  $('startBtn').textContent = due ? `Start · ${due} due` : 'Practice weakest';
  $('homeMsg').textContent = due ? '' : nextDueMsg(scope.ids);
}
function nextDueMsg(ids) {
  const next = ids.map(id => new Date(byId[id].due)).sort((a, b) => a - b)[0];
  if (!next) return '';
  const h = (next - Date.now()) / 36e5;
  return `Nothing due here. Next review ${h < 1 ? 'within the hour' : h < 24 ? `in ${Math.round(h)}h` : `in ${Math.round(h / 24)}d`}.`;
}
function fillCustom() {
  const clef = $('cClef').value, opts = cards.filter(c => c.clef === clef);
  for (const sel of [$('cLow'), $('cHigh')]) sel.innerHTML = opts.map(c => `<option value="${c.pos}">${name(c)}</option>`).join('');
  $('cHigh').selectedIndex = opts.length - 1;
}
$('cClef').onchange = fillCustom;
const customFromForm = () => rangeScope($('cClef').value, +$('cLow').value, +$('cHigh').value, $('cName').value.trim() || undefined);
$('cUse').onclick = () => { scope = customFromForm(); scopes.push(scope); start(); };
$('cSave').onclick = () => {
  const s = customFromForm();
  localStorage.setItem('pf_scopes', JSON.stringify([...savedScopes().filter(x => x.label !== s.label), {label: s.label, ...s.custom}]));
  localStorage.setItem('pf_scope', s.label); renderScopes();
};

// ---- staff (VexFlow)
function drawNote(card) {
  const el = $('staff'); el.innerHTML = '';
  const VF = Vex.Flow, W = 220, H = 125;
  const r = new VF.Renderer(el, VF.Renderer.Backends.SVG);
  r.resize(W, H);
  const ctx = r.getContext();
  ctx.setFillStyle('#000'); ctx.setStrokeStyle('#000');
  const stave = new VF.Stave(10, 0, W - 20);
  stave.addClef(card.clef).setContext(ctx).draw();
  const note = new VF.StaveNote({clef: card.clef, keys: [card.letter.toLowerCase() + '/' + card.octave], duration: 'w', align_center: true});
  VF.Formatter.FormatAndDraw(ctx, stave, [note]);
  const svg = el.querySelector('svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.removeAttribute('width'); svg.removeAttribute('height');
  svg.setAttribute('data-card', card.id);
}

// ---- session
const S = {active: false, id: null, queue: [], answered: [], cur: null, state: 'idle', t0: 0, onset: null, tick: null, practice: false, misses: {}};
function shuffle(a) { for (let k = a.length - 1; k > 0; k--) { const j = Math.floor(Math.random() * (k + 1)); [a[k], a[j]] = [a[j], a[k]]; } return a; }
function weakest(ids, n) {
  const score = c => (c.new ? 0.5 : c.retrievability) - (c.median_ms || cfg.good_ms) / 10000 - (c.last.length && !c.last[c.last.length - 1].ok ? 0.5 : 0);
  return shuffle(ids.map(id => byId[id]).sort((a, b) => score(a) - score(b)).slice(0, n).map(c => c.id));
}
async function start(practice) {
  // No note and no clock until voice is listening. The mic is requested inside this tap (iOS Safari needs a gesture).
  const go = S.go = {};
  if (params.get('voice') !== '0' && !voice.on) {
    $('scopeLbl').textContent = scope.label; $('timer').textContent = ''; $('left').textContent = '';
    $('staff').innerHTML = ''; $('fb').textContent = ''; show('drillView');
    try { await voice.enable(); } catch (e) {
      const why = $('heard').textContent;
      if (S.go === go) { await home(); $('homeMsg').textContent = why + '. Voice is needed to start.'; }
      return;
    }
    if (S.go !== go) return;
  }
  await loadState().catch(() => {});
  const due = scope.ids.filter(id => byId[id].is_due);
  S.practice = practice || !due.length;
  S.queue = S.practice ? weakest(scope.ids, Math.min(10, scope.ids.length)) : shuffle(due);
  if (!S.queue.length) return;
  S.answered = []; S.misses = {}; S.cur = null; S.active = true;
  $('scopeLbl').textContent = scope.label + (S.practice ? ' · practice' : '');
  show('drillView');
  S.id = (await api('practice', {scope: scope.label + (S.practice ? ' (weakest)' : '')})).practice_id;
  next();
}
function requeue(id, rating) {
  // Missed: back in 2-4 cards. Slow: back in 5-8. Never right after itself.
  const [lo, hi] = rating === 'Again' ? [2, 4] : [5, 8];
  const at = Math.min(S.queue.length, lo + Math.floor(Math.random() * (hi - lo + 1)));
  S.queue.splice(Math.max(1, at), 0, id);
}
const fmt = ms => (ms / 1000).toFixed(1) + 's';
function next() {
  if (!S.active) return;
  if (!S.queue.length) return finish();
  let id = S.queue.shift();
  if (S.cur && id === S.cur.id && S.queue.length) { S.queue.splice(1, 0, id); id = S.queue.shift(); }
  S.cur = byId[id]; S.onset = null; S.state = 'shown';
  $('fb').textContent = ''; $('fb').className = ''; $('heard').textContent = '';
  $('left').textContent = `${S.answered.length} done · ${S.queue.length + 1} left`;
  $('timer').textContent = '0.0s'; clearInterval(S.tick);
  drawNote(S.cur);
  // Start the clock once the note has actually been painted.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (S.cur.id !== id || S.state !== 'shown') return;
    S.state = 'ask'; S.t0 = performance.now(); voice.reset();
    S.tick = setInterval(() => $('timer').textContent = fmt(performance.now() - S.t0), 100);
  }));
}
function markOnset(ts) {
  if (S.state !== 'ask' || S.onset !== null) return;
  S.onset = Math.max(ts, S.t0); clearInterval(S.tick); $('timer').textContent = fmt(S.onset - S.t0);
}
function answer(letter, engine) {
  if (S.state !== 'ask' || !S.active || !S.id) return;
  // Time voice answers from the start of the sound burst the recognized word came from, not from the first
  // noise after the note appeared (a cough or click would otherwise freeze the clock early).
  const s0 = engine === 'key' ? null : voice.speechStart(S.t0);
  if (s0 != null) { S.onset = null; markOnset(s0); }
  else if (S.onset === null) markOnset(performance.now());
  S.state = 'graded';
  const card = S.cur, ms = Math.round(S.onset - S.t0), correct = letter === card.letter;
  const a = {id: card.id, letter, ms, correct, rating: rateOf(correct, ms)};
  S.answered.push(a);
  showGrade(card, a);
  if (S.pending) commit(S.pending);
  // Vosk's final result says when the word itself began; until it arrives (or 1.5 s pass) the grade on screen
  // stands but isn't saved, so a later noise burst picked by speechStart can still be corrected.
  const p = {a, card, engine, practice: S.id, t0: S.t0, bursts: voice.vad.bursts};
  if (engine === 'vosk') { S.pending = p; p.timer = setTimeout(() => commit(p), 1500); } else commit(p);
  setTimeout(next, correct ? 650 : 1800);
}
const rateOf = (correct, ms) => !correct ? 'Again' : ms > cfg.good_ms ? 'Hard' : ms > cfg.easy_ms ? 'Good' : 'Easy';
function showGrade(card, a) {
  $('fb').className = a.rating;
  $('fb').textContent = a.correct ? `${card.letter} · ${fmt(a.ms)}` : `${a.letter} ✗ — it's ${card.letter}${card.octave}`;
  $('fb').dataset.rating = a.rating;
}
function commit(p) {
  if (p.saved) return p.saved;
  clearTimeout(p.timer); if (S.pending === p) S.pending = null;
  const a = p.a;
  if (a.rating === 'Again' || a.rating === 'Hard') requeue(a.id, a.rating);
  return p.saved = api('answer', {practice_id: p.practice, card_id: a.id, answer: a.letter, ms: a.ms, engine: p.engine})
    .catch(e => { $('heard').textContent = 'not saved: ' + e.message; });
}
// Word start from Vosk (performance.now() time): re-time the pending answer from the sound burst that word began in.
function correctOnset(letter, wordStart) {
  const p = S.pending;
  if (!p || p.a.letter !== letter || wordStart < p.t0 - 30) return;
  const b = p.bursts.filter(t => t >= p.t0 - 30 && t <= wordStart + 100).pop();
  const ms = Math.round(Math.max(b ?? wordStart, p.t0) - p.t0);
  if (ms !== p.a.ms) {
    p.a.ms = ms; p.a.rating = rateOf(p.a.correct, ms);
    if (S.cur === p.card && S.state === 'graded') { showGrade(p.card, p.a); $('timer').textContent = fmt(ms); }
  }
  commit(p);
}
function stop() { S.active = false; S.state = 'idle'; S.go = null; clearInterval(S.tick); }
async function finish() {
  stop();
  if (!S.id) return home();
  const sid = S.id; S.id = null;
  if (S.pending) await commit(S.pending);
  if (!S.answered.length) { api('finish', {practice_id: sid}).catch(() => {}); return home(); }
  let res = null;
  try { res = await api('finish', {practice_id: sid}); } catch (e) { $('doneSub').textContent = 'Could not save the session: ' + e.message; }
  await loadState().catch(() => {});
  // Summary uses each card's first answer. Every answer, repeats included, is an FSRS review: FSRS uses
  // same-day reviews as learning steps, so a miss then a fast retry is scheduled sooner than a clean pass.
  const first = {};
  S.answered.forEach(a => { if (!first[a.id]) first[a.id] = a; });
  const firsts = Object.values(first), ok = firsts.filter(a => a.correct);
  const times = ok.map(a => a.ms).sort((a, b) => a - b), mid = Math.floor(times.length / 2);
  const med = times.length ? Math.round((times[mid] + times[Math.floor((times.length - 1) / 2)]) / 2) : null;
  $('doneHead').textContent = `${ok.length}/${firsts.length} right first time${med ? ` · median ${fmt(med)}` : ''}`;
  if (res) $('doneSub').textContent = `${S.answered.length} answers including repeats.`;
  $('doneGrid').innerHTML = '';
  firsts.sort((a, b) => byId[a.id].clef.localeCompare(byId[b.id].clef) || byId[a.id].pos - byId[b.id].pos)
    .forEach(a => $('doneGrid').appendChild(cell(byId[a.id], a.rating, a.correct ? fmt(a.ms) : '✗ ' + a.letter)));
  show('doneView');
}
function cell(c, cls, sub) {
  const d = document.createElement('div');
  d.className = 'cell ' + (cls || ''); d.innerHTML = `<b>${name(c)}</b>${sub || ''}`; d.title = c.id; return d;
}
$('startBtn').onclick = () => start(false);
$('practiceBtn').onclick = () => start(true);
$('homeBtn').onclick = home;
$('endBtn').onclick = finish;
window.addEventListener('pagehide', () => { if (S.id && S.answered.length) api('finish', {practice_id: S.id}).catch(() => {}); });
document.addEventListener('keydown', e => {
  if (S.state === 'ask' && /^[a-g]$/i.test(e.key) && !e.metaKey && !e.ctrlKey) { markOnset(performance.now()); answer(e.key.toUpperCase(), 'key'); }
});

// ---- stats
function tier(c) {
  if (c.new) return '';
  if (c.last.length && !c.last[c.last.length - 1].ok) return 'Again';
  if (c.median_ms == null) return 'Again';
  return c.median_ms > cfg.good_ms ? 'Hard' : c.median_ms > cfg.easy_ms ? 'Good' : 'Easy';
}
async function stats() {
  await loadState();
  for (const [clef, el] of [['treble', 'gridTreble'], ['bass', 'gridBass']]) {
    $(el).innerHTML = '';
    cards.filter(c => c.clef === clef).forEach(c =>
      $(el).appendChild(cell(c, tier(c), c.new ? 'new' : `${c.median_ms != null ? fmt(c.median_ms) : '—'}<br>${c.correct}/${c.attempts}`)));
  }
  const slow = cards.filter(c => !c.new).sort((a, b) => (b.median_ms ?? 1e9) - (a.median_ms ?? 1e9)).slice(0, 8);
  $('slowTbl').innerHTML = '<tr><th>Note</th><th>Median</th><th>Right</th><th>Next due</th></tr>' + slow.map(c =>
    `<tr><td>${c.clef} ${name(c)}</td><td>${c.median_ms != null ? fmt(c.median_ms) : '—'}</td><td>${c.correct}/${c.attempts}</td><td>${new Date(c.due).toLocaleString([], {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'})}</td></tr>`).join('');
  show('statsView');
}
$('statsBtn').onclick = () => { if (!S.active) stats(); };
$('statsBack').onclick = home;

// ---- voice: offline Vosk (default) or the browser's speech recognition (?engine=browser)
const MAP = {a: 'A', ay: 'A', hey: 'A', hay: 'A', aye: 'A', eh: 'A',
  b: 'B', be: 'B', bee: 'B', bea: 'B',
  c: 'C', see: 'C', sea: 'C', si: 'C', cee: 'C',
  d: 'D', de: 'D', dee: 'D', di: 'D', the: 'D', thee: 'D', tee: 'D', tea: 'D', dea: 'D', deep: 'D',
  e: 'E', ee: 'E', eee: 'E', he: 'E', ye: 'E', yi: 'E', eve: 'E', eat: 'E', ease: 'E', eek: 'E', ie: 'E', ii: 'E',
  f: 'F', ef: 'F', eff: 'F', if: 'F',
  g: 'G', gee: 'G', jee: 'G', ji: 'G', je: 'G', gi: 'G'};
const toLetter = text => { for (const w of (text || '').toLowerCase().replace(/[^a-z ]/g, ' ').split(/\s+/)) if (MAP[w]) return MAP[w]; return null; };
// Served locally if present (the Docker image includes it), else from the vosk-browser site.
const MODEL_FILE = 'vosk-model-small-en-us-0.15.tar.gz';
const modelUrl = async () => params.get('model') ||
  (await fetch('models/' + MODEL_FILE, {method: 'HEAD'}).then(r => r.ok).catch(() => false)
    ? new URL('models/' + MODEL_FILE, location.href).href : 'https://ccoreilly.github.io/vosk-browser/models/' + MODEL_FILE);

const voice = {
  on: false, engine: null, vad: {floor: 0.005, active: false, start: null, lastStart: null, quiet: 0, bursts: []},
  reset() { const v = this.vad; v.lastStart = null; v.bursts = []; if (!v.active) v.start = null; },
  // Start of the speech that produced the current result: the ongoing burst, else the last one since t0.
  speechStart(t0) { const v = this.vad; const s = v.active ? v.start : v.lastStart; return s != null && s >= t0 - 30 ? s : null; },
  // Energy-based speech onset. Works on ~5 ms blocks; once speech is detected it walks back over the
  // quieter lead-in (soft consonants like the "s" in "see") so the onset isn't late.
  hist: [],
  feed(d, sr) {
    const v = this.vad, B = 256, end = performance.now(), n = Math.floor(d.length / B);
    for (let b = 0; b < n; b++) {
      let sum = 0; for (let k = b * B; k < (b + 1) * B; k++) sum += d[k] * d[k];
      const rms = Math.sqrt(sum / B), t = end - (d.length - b * B) / sr * 1000, blockMs = B / sr * 1000;
      this.hist.push([t, rms]); if (this.hist.length > 200) this.hist.shift();
      if (!v.active) {
        v.floor = v.floor * 0.99 + rms * 0.01;
        if (rms > Math.max(0.01, v.floor * 3)) {
          let start = t;
          const soft = Math.max(0.003, v.floor * 1.5);
          for (let i = this.hist.length - 2; i >= 0 && this.hist[i][1] > soft && t - this.hist[i][0] < 300; i--) start = this.hist[i][0];
          v.active = true; v.start = start; v.quiet = 0; v.bursts.push(start);
        }
      } else if (rms < Math.max(0.008, v.floor * 2)) { v.quiet += blockMs; if (v.quiet > 300) { v.active = false; v.lastStart = v.start; } }
      else v.quiet = 0;
    }
  },
  // performance.now() time of a position (seconds of audio fed) in the Vosk stream.
  audioTime(sec) {
    const c = (this.chunks || []).filter(([s]) => s <= sec).pop();
    return c ? c[1] + (sec - c[0]) * 1000 : null;
  },
  heard(text, final) {
    if (S.state !== 'ask') return;
    // A result for speech that began before this note (e.g. Vosk finishing the previous answer late) is stale.
    if (this.speechStart(S.t0) == null) return;
    const L = toLetter(text);
    $('heard').textContent = text ? `heard “${text}”` : '';
    if (L) answer(L, voice.engine);
  },
  enable() {
    if (!this.pending) this.pending = this.start().catch(e => { this.pending = null; throw e; });
    return this.pending;
  },
  async start() {
    $('micBtn').textContent = 'Voice: starting…';
    const want = params.get('engine') || 'vosk';
    try {
      if (want === 'vosk' && window.Vosk) await this.vosk(); else await this.browser();
      this.on = true; localStorage.setItem('pf_voice', '1');
      $('micBtn').textContent = `Voice: on (${this.engine})`; $('micBtn').classList.add('mic', 'on');
    } catch (e) {
      $('micBtn').textContent = 'Voice: off'; $('heard').textContent = 'Voice failed: ' + (e.name || '') + ' ' + (e.message || e);
      throw e;
    }
  },
  async vosk() {
    $('heard').textContent = 'Allow the microphone to start…';
    const stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, channelCount: 1}});
    const ctx = new AudioContext();
    if (ctx.state === 'suspended') await ctx.resume();
    $('heard').textContent = 'Loading voice model (one-time ~40 MB)…';
    const model = await Vosk.createModel(await modelUrl());
    const grammar = JSON.stringify([...Object.keys(MAP), '[unk]']);
    const rec = new model.KaldiRecognizer(16000, grammar);
    rec.setWords(true);
    rec.on('result', m => {
      this.heard(m.result.text, true);
      for (const w of m.result.result || []) {
        const L = toLetter(w.word), t = this.audioTime(w.start);
        if (L && t != null) { correctOnset(L, t); break; }
      }
    });
    rec.on('partialresult', m => { if (m.result.partial) this.heard(m.result.partial, false); });
    const src = ctx.createMediaStreamSource(stream), node = ctx.createScriptProcessor(2048, 1, 1);
    let fed = 0;
    this.chunks = [];
    node.onaudioprocess = ev => {
      const buf = ev.inputBuffer;
      this.chunks.push([fed, performance.now() - buf.duration * 1000]); if (this.chunks.length > 400) this.chunks.shift();
      fed += buf.duration;
      this.feed(buf.getChannelData(0), ctx.sampleRate);
      try { rec.acceptWaveform(buf); } catch (e) { console.error(e); }
    };
    src.connect(node); node.connect(ctx.destination);
    this.engine = 'vosk'; $('heard').textContent = '';
  },
  async browser() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) throw new Error('no speech recognition in this browser');
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    const ctx = new AudioContext(), src = ctx.createMediaStreamSource(stream), node = ctx.createScriptProcessor(2048, 1, 1);
    node.onaudioprocess = ev => this.feed(ev.inputBuffer.getChannelData(0), ctx.sampleRate);
    src.connect(node); node.connect(ctx.destination);
    const r = new SR(); r.continuous = true; r.interimResults = true; r.lang = 'en-US';
    r.onspeechstart = () => { if (S.state === 'ask' && this.vad.start == null) markOnset(performance.now()); };
    r.onresult = ev => { const res = ev.results[ev.results.length - 1]; this.heard(res[0].transcript, res.isFinal); };
    r.onend = () => { if (this.on) try { r.start(); } catch {} };
    r.start(); this.engine = 'browser';
  },
};
$('micBtn').onclick = () => {
  if (voice.on) { $('heard').textContent = 'Reload the page to turn voice off.'; localStorage.setItem('pf_voice', '0'); return; }
  voice.enable().catch(() => {});
};

// ---- boot
async function home() {
  show('homeView');
  try { await loadState(); } catch (e) { if (e.message !== 'unauthorized') $('homeMsg').textContent = 'Could not load: ' + e.message; return; }
  renderScopes(); fillCustom();
}
$('tokenForm').onsubmit = async e => {
  e.preventDefault(); token = $('tokenInput').value.trim();
  try { await loadState(); localStorage.setItem('pf_token', token); $('tokenErr').textContent = ''; home(); }
  catch (err) { $('tokenErr').textContent = 'That token did not work.'; }
};
home();
