'use strict';
// Internal pulse drill. One bar of clicks to listen to, then clap along with the click (~20 s), then the
// click stops and the mic judges each clap against where the beat would have been. OFF_RUN beats in a row
// that are missed or more than OFF_FRAC of a beat off bring the click back; holding the whole silent stretch
// makes the next one STEP_S longer. Alternates until the session time is up. The server keeps rounds per tempo.
//
// Claps and the click share the AudioContext clock: clicks are scheduled on it and the mic worklet stamps claps
// with it. The silent-stretch grid is where the claps landed during the click stretch before it (so speaker and
// mic latency cancel out); without enough claps there, where the mic heard the count-in click.
const PV = {cfg: null, tempos: [], bpm: null, picked: false};
const PS = {active: false};
const pq = k => +params.get(k) || 0;  // dev/test overrides: ?pulse_click=6&pulse_silent=8&pulse_minutes=1
const pfmt = s => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const median = xs => { const a = [...xs].sort((x, y) => x - y), m = a.length >> 1; return a.length ? (a[m] + a[(a.length - 1) >> 1]) / 2 : null; };
const mean = xs => xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;

function tendency(ms, bpm) {
  if (ms == null) return '—';
  if (Math.abs(ms) < 0.03 * 60000 / bpm) return 'steady';
  return `${ms < 0 ? 'rushes' : 'drags'} ${Math.abs(Math.round(ms))}ms`;
}
const levelClass = t => !t.rounds ? '' : t.level >= 0.66 ? 'Easy' : t.level >= 0.33 ? 'Good' : t.level > 0 ? 'Hard' : 'Again';
function ago(days) {
  if (days == null) return 'never';
  return days < 1 / 24 ? 'just now' : days < 1 ? `${Math.round(days * 24)}h ago` : `${Math.round(days)}d ago`;
}

// ---- home: tempo picker, session length, per-tempo history
async function pulseHome() {
  setMode('pulse'); show('pulseHome');
  let st;
  try { st = await api('pulse/state'); } catch (e) { if (e.message !== 'unauthorized') $('pulseMsg').textContent = 'Could not load: ' + e.message; return; }
  PV.cfg = st; PV.tempos = st.tempos;
  if (!PV.picked || !PV.tempos.some(t => t.bpm === PV.bpm)) { PV.bpm = st.suggested; PV.picked = false; }
  const min = +localStorage.getItem('pf_pulse_min') || 4;
  $('pMinutes').innerHTML = st.minutes.map(m => `<option value="${m}"${m === min ? ' selected' : ''}>${m} min</option>`).join('');
  renderPulseHome();
}
function renderPulseHome() {
  const st = PV.cfg;
  $('pTempos').innerHTML = '';
  PV.tempos.forEach(t => {
    const b = document.createElement('button');
    b.className = 'scope tempo ' + levelClass(t) + (t.bpm === PV.bpm ? ' sel' : '');
    b.dataset.bpm = t.bpm;
    b.innerHTML = `<b>${t.bpm}${t.bpm === st.suggested ? ' ★' : ''}</b><span>${t.rounds ? `held ${Math.round(t.recent_s)}s` : 'new'}</span>`;
    b.onclick = () => { PV.bpm = t.bpm; PV.picked = true; renderPulseHome(); };
    $('pTempos').appendChild(b);
  });
  const s = PV.tempos.find(t => t.bpm === st.suggested);
  $('pSuggest').textContent = `★ Suggested: ${s.bpm} bpm — ${!s.rounds ? 'not tried yet' : `held ${Math.round(s.recent_s)}s lately, last practiced ${ago(s.days_since)}`}.`;
  const cur = PV.tempos.find(t => t.bpm === PV.bpm);
  $('pStart').textContent = `Start · ${PV.bpm} bpm · ${$('pMinutes').value} min`;
  $('pFirst').textContent = `First silent stretch: ${pq('pulse_silent') || cur.start_s}s.`;
  const played = PV.tempos.filter(t => t.rounds);
  $('pTable').innerHTML = played.length
    ? '<tr><th>bpm</th><th>Held</th><th>Best</th><th>Tendency</th><th>Last</th></tr>' + played.map(t =>
      `<tr><td>${t.bpm}</td><td>${Math.round(t.recent_s)}s</td><td>${Math.round(t.best_s)}s</td><td>${tendency(t.offset_ms, t.bpm)}</td><td>${ago(t.days_since)}</td></tr>`).join('')
    : '<tr><td class="muted">No pulse practice yet.</td></tr>';
}
$('pMinutes').onchange = () => { localStorage.setItem('pf_pulse_min', $('pMinutes').value); renderPulseHome(); };

// ---- audio: mic clap detector (AudioWorklet) and the click
const pulseAudio = {
  ctx: null, node: null, stream: null, loaded: false, onClap: null,
  async open() {
    if (!this.ctx) this.ctx = new (window.AudioContext || window.webkitAudioContext)({latencyHint: 'interactive'});
    const resumed = this.ctx.resume();  // inside the Start tap: iOS Safari only starts audio from a gesture
    if (navigator.audioSession) try { navigator.audioSession.type = 'play-and-record'; } catch {}
    if (!this.node) {
      // Raw mic: echo cancellation would remove the click (it is the calibration signal) and noise suppression eats claps.
      this.stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1}});
      if (!this.loaded) { await this.ctx.audioWorklet.addModule('pulse-worklet.js'); this.loaded = true; }
      this.node = new AudioWorkletNode(this.ctx, 'clap-detector');
      const mute = this.ctx.createGain(); mute.gain.value = 0;
      this.ctx.createMediaStreamSource(this.stream).connect(this.node);
      this.node.connect(mute); mute.connect(this.ctx.destination);
      this.node.port.onmessage = e => this.onClap && this.onClap(e.data);
    }
    await resumed;
  },
  close() {
    if (this.stream) this.stream.getTracks().forEach(t => t.stop());
    if (this.node) this.node.disconnect();
    this.stream = this.node = null; this.onClap = null;
  },
  click(t, accent) {
    const c = this.ctx, o = c.createOscillator(), g = c.createGain();
    o.type = 'square'; o.frequency.value = accent ? 1760 : 1320;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(accent ? 0.8 : 0.55, t + 0.002);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.05);
    o.connect(g); g.connect(c.destination); o.start(t); o.stop(t + 0.06);
  },
};

// ---- session
const beatTime = k => PS.t0 + k * PS.period;
const wrap = t => { const d = t - PS.t0, p = PS.period; return d - Math.round(d / p) * p; };
const clapsIn = (k0, k1) => PS.claps.filter(c => c.t >= beatTime(k0) - PS.period / 2 && c.t < beatTime(k1) - PS.period / 2);

async function pulseStart() {
  if (PS.active || PS.starting) return;
  const bpm = PV.bpm, minutes = +$('pMinutes').value, cfg = PV.cfg;
  localStorage.setItem('pf_pulse_min', minutes);
  PS.starting = true;
  $('pulseMsg').textContent = 'Allow the microphone…';
  try { await pulseAudio.open(); } catch (e) {
    PS.starting = false; pulseAudio.close();
    $('pulseMsg').textContent = `The microphone is needed to hear your claps (${e.name || ''} ${e.message || e}).`; return;
  }
  let r;
  try { r = await api('pulse/start', {bpm, minutes}); } catch (e) {
    PS.starting = false; pulseAudio.close(); $('pulseMsg').textContent = 'Could not start: ' + e.message; return;
  }
  const period = 60 / bpm, bars = s => Math.max(4, Math.round(s / period / 4) * 4);
  Object.assign(PS, {
    active: true, starting: false, id: r.session_id, bpm, period, cfg,
    target: pq('pulse_silent') || r.start_s, clickBeats: bars(pq('pulse_click') || cfg.click_s),
    total: (pq('pulse_minutes') || minutes) * 60, t0: pulseAudio.ctx.currentTime + 0.4, next: 0,
    rounds: [], claps: [], R: null, L: null, last: null, note: '', pending: null, seg: {type: 'count', start: 0, end: 4},
  });
  PS.startAt = beatTime(4);
  pulseAudio.node.port.postMessage({refractory: period * 0.3});
  pulseAudio.onClap = d => { if (PS.active) { PS.claps.push(d); if (PS.claps.length > 256) PS.claps.shift(); } };
  $('pulseMsg').textContent = ''; $('pMsg').textContent = ''; $('pRounds').textContent = '';
  $('pBpm').textContent = `${bpm} bpm`;
  show('pulseRun');
  wake(true);
  PS.timer = setInterval(pulseTick, 25);
}

function pulseTick() {
  if (!PS.active) return;
  const now = pulseAudio.ctx.currentTime;
  PS.timeUp = now - PS.startAt >= PS.total;
  if (PS.timeUp && PS.seg.type === 'click' && !PS.pending) return pulseFinish();
  judge(now);
  while (PS.active && beatTime(PS.next) < now + 0.12) scheduleBeat(PS.next++);
  if (PS.active) renderRun(now);
}

function scheduleBeat(k) {
  let seg = PS.seg;
  if (seg.type === 'count' && k >= seg.end) {
    // Where the mic heard the count-in click: the fallback grid when there are too few claps to align to.
    const offs = clapsIn(0, 4).map(c => wrap(c.t));
    PS.L = offs.length >= 2 ? median(offs) : null;
    seg = PS.seg = {type: 'click', start: k, end: k + PS.clickBeats};
  } else if (seg.type === 'click' && k >= seg.end) {
    if (!startSilent(k)) return;
  } else if (seg.type === 'silent' && k >= seg.start + seg.beats) {
    // The click comes back on time; the silence is scored once judge() has seen its last beats (PS.pending).
    PS.pending = seg; PS.seg = {type: 'click', start: k, end: k + PS.clickBeats};
  }
  if (PS.seg.type !== 'silent') pulseAudio.click(beatTime(k), k % 4 === 0);
}

function startSilent(k) {
  const remaining = PS.total - (beatTime(k) - PS.startAt);
  if (remaining < 8) { pulseFinish(); return false; }
  const offs = clapsIn(PS.seg.start, k).slice(-8).map(c => wrap(c.t));
  const ctx = pulseAudio.ctx;
  PS.R = offs.length >= 4 ? median(offs) : PS.L ?? PS.R ?? (ctx.outputLatency || 0) + (ctx.baseLatency || 0);
  const want = Math.min(PS.target, remaining), beats = Math.max(4, Math.round(want / PS.period));
  PS.seg = {type: 'silent', start: k, beats, full: want === PS.target, judged: k, offRun: 0, firstOff: null, offs: [], claps: 0};
  PS.last = null; PS.note = '';
  return true;
}

// Judge each silent beat once its window (half a beat either side of where the beat falls) has passed.
function judge(now) {
  const seg = PS.pending || PS.seg, p = PS.period, off = PS.cfg.off_frac * p;
  if (seg.type !== 'silent') return;
  while (true) {
    if (seg.judged >= seg.start + seg.beats) { if (seg === PS.pending) endSilent(true); return; }
    const k = seg.judged, exp = beatTime(k) + PS.R;
    if (now < exp + p / 2 + 0.03) return;
    const best = PS.claps.filter(c => c.t >= exp - p / 2 && c.t < exp + p / 2)
      .sort((a, b) => Math.abs(a.t - exp) - Math.abs(b.t - exp))[0];
    const d = best ? best.t - exp : null, bad = d == null || Math.abs(d) > off;
    if (best) { seg.claps++; seg.offs.push(d); }
    PS.last = {k, d, bad};
    if (bad) { seg.offRun++; if (seg.firstOff == null) seg.firstOff = k; } else { seg.offRun = 0; seg.firstOff = null; }
    seg.judged++;
    if (seg.offRun >= PS.cfg.off_run) { endSilent(false); return; }
  }
}

// Score a silence: held to the end, or lost (the click comes back at the next beat unless it already has).
function endSilent(completed) {
  const seg = PS.pending || PS.seg, p = PS.period, offs = seg.offs.map(x => x * 1000);
  const round = {
    target_s: +(seg.beats * p).toFixed(1), held_s: +((completed ? seg.beats : seg.firstOff - seg.start) * p).toFixed(1),
    completed, claps: seg.claps, mean_offset_ms: offs.length ? Math.round(mean(offs)) : null,
    mean_abs_ms: offs.length ? Math.round(mean(offs.map(Math.abs))) : null,
  };
  PS.rounds.push(round);
  api('pulse/round', {session_id: PS.id, ...round}).catch(e => { $('pMsg').textContent = 'Round not saved: ' + e.message; });
  if (completed && seg.full) PS.target = Math.min(PS.cfg.max_silent_s, PS.target + PS.cfg.step_s);
  PS.note = completed ? `Held all ${Math.round(round.held_s)}s — next silence ${Math.round(PS.target)}s` : `Off the beat after ${Math.round(round.held_s)}s — click is back`;
  $('pRounds').textContent = 'Silent stretches: ' + PS.rounds.map(r => `${Math.round(r.held_s)}s${r.completed ? ' ✓' : ''}`).join(' · ');
  $('pMsg').textContent = '';
  if (seg === PS.pending) PS.pending = null;
  else if (PS.timeUp) pulseFinish();
  else PS.seg = {type: 'click', start: PS.next, end: PS.next + PS.clickBeats};
}

function renderRun(now) {
  const seg = PS.seg, p = PS.period, k = Math.floor((now - PS.t0) / p);
  $('pLeft').textContent = PS.timeUp ? 'last stretch' : pfmt(PS.total - Math.max(0, now - PS.startAt)) + ' left';
  const lit = seg.type !== 'silent' && k >= 0 && now - beatTime(k) < 0.12;
  $('pBeat').classList.toggle('on', lit);
  const c = PS.claps[PS.claps.length - 1];
  $('pClap').classList.toggle('on', !!c && now - c.t < 0.15 && now >= c.t);
  $('pulseRun').dataset.phase = seg.type;
  if (seg.type === 'count') {
    $('pPhase').textContent = 'Listen…';
    $('pHeld').textContent = String(Math.max(1, 4 - Math.max(0, k)));
    $('pOff').textContent = 'Clap along after these 4 clicks';
  } else if (seg.type === 'click') {
    $('pPhase').textContent = 'Clap along with the click';
    $('pHeld').textContent = `silence in ${Math.max(0, Math.ceil(beatTime(seg.end) - now))}s`;
    $('pOff').textContent = PS.note;
  } else {
    $('pPhase').textContent = 'Keep the beat — listening';
    $('pHeld').textContent = `${Math.floor(Math.max(0, now - beatTime(seg.start)))}s / ${Math.round(seg.beats * p)}s`;
    const l = PS.last;
    $('pOff').className = l ? (l.bad ? 'bad' : 'ok') : '';
    $('pOff').textContent = !l ? '' : l.d == null ? 'missed a beat'
      : !l.bad ? `on the beat (${l.d < 0 ? '' : '+'}${Math.round(l.d * 1000)}ms)` : `${l.d < 0 ? 'early' : 'late'} ${Math.abs(Math.round(l.d * 1000))}ms`;
    if (!PS.claps.some(c => c.t > beatTime(seg.start))) $('pMsg').textContent = k - seg.start >= 2 ? 'Not hearing claps — clap louder or closer to the phone.' : '';
    else $('pMsg').textContent = '';
  }
  if (seg.type !== 'silent') $('pOff').className = '';
}

async function pulseFinish() {
  if (!PS.active) return;
  if (PS.pending) judge(Infinity);  // a silence that already ran its course, still waiting on its last beats
  PS.active = false; clearInterval(PS.timer); wake(false); pulseAudio.close();
  const id = PS.id; PS.id = null;
  let res = null;
  try { res = (await api('pulse/finish', {session_id: id})).session; } catch (e) { $('pDoneSub').textContent = 'Could not save: ' + e.message; }
  const rs = PS.rounds, best = Math.max(0, ...rs.map(r => r.held_s));
  const offs = rs.map(r => r.mean_offset_ms).filter(x => x != null);
  $('pDoneHead').textContent = rs.length ? `${PS.bpm} bpm · held up to ${Math.round(best)}s` : `${PS.bpm} bpm · no silent stretch yet`;
  $('pDoneSub').textContent = rs.length
    ? `${rs.filter(r => r.completed).length}/${rs.length} silent stretches held to the end · ${tendency(offs.length ? mean(offs) : null, PS.bpm)}`
    : (res ? 'The session ended before the click first went quiet.' : $('pDoneSub').textContent);
  $('pDoneTbl').innerHTML = rs.length ? '<tr><th>#</th><th>Silence</th><th>Held</th><th>Tendency</th></tr>' + rs.map((r, i) =>
    `<tr><td>${i + 1}</td><td>${Math.round(r.target_s)}s</td><td>${Math.round(r.held_s)}s${r.completed ? ' ✓' : ''}</td><td>${tendency(r.mean_offset_ms, PS.bpm)}</td></tr>`).join('') : '';
  show('pulseDone');
}

let wakeLock = null;
async function wake(on) {
  try {
    if (on && navigator.wakeLock) wakeLock = await navigator.wakeLock.request('screen');
    else if (!on && wakeLock) { await wakeLock.release(); wakeLock = null; }
  } catch {}
}

$('pStart').onclick = pulseStart;
$('pEnd').onclick = pulseFinish;
$('pAgain').onclick = pulseStart;
$('pHomeBtn').onclick = pulseHome;
// Audio timing stops when the page is hidden, so the session ends there.
document.addEventListener('visibilitychange', () => { if (document.hidden && PS.active) pulseFinish(); });
window.addEventListener('pagehide', () => { if (PS.active && PS.id) api('pulse/finish', {session_id: PS.id}).catch(() => {}); });
