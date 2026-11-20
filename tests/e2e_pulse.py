"""Browser end-to-end test of the internal pulse drill, no human needed.

getUserMedia is replaced by a WebAudio stream the test controls. A player schedules a short noise burst
(a clap) at each beat the policy asks for, so the real path runs: AudioWorklet clap detection, latency
calibration from the clapped-along stretch, judging silent beats, the click returning, rounds saved per tempo.
Short stretches via ?pulse_click=4&pulse_silent=6&pulse_minutes=0.5.
Three runs at 80 bpm:
  1. steady: clap every beat on time -> every silence held, each next one 5 s longer
  2. stop: stop clapping when the click stops -> the click is back after 4 beats, 0 s held
  3. drag: in silence each clap lands 12 ms later than the last -> held a few seconds, then lost, "drags"
Needs: pip install playwright && playwright install chromium (or CHROME=/path/to/chrome).
Run:  python -m tests.e2e_pulse
"""

import json
import os
import tempfile
import urllib.request

from playwright.sync_api import sync_playwright

from tests.e2e_voice import ROOT, TOKEN, free_port, serve

CLAPPER = """
(() => {
  let ctx, dest;
  navigator.mediaDevices.getUserMedia = async () => {
    if (!ctx) { ctx = new AudioContext(); dest = ctx.createMediaStreamDestination(); }
    await ctx.resume(); return dest.stream;
  };
  const clap = when => {
    const n = Math.round(ctx.sampleRate * 0.03), buf = ctx.createBuffer(1, n, ctx.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * 0.8 * Math.exp(-i / (n / 5));
    const src = ctx.createBufferSource(); src.buffer = buf; src.connect(dest); src.start(when);
  };
  // policy(k, silentIndex) -> seconds off the beat, or null for no clap; silentIndex is null outside silence.
  window.__clapAlong = policy => {
    let done = -1, id = null;
    setInterval(() => {
      if (typeof PS === 'undefined' || !PS.active || !ctx) { done = -1; return; }
      if (PS.id !== id) { id = PS.id; done = -1; }
      const pc = pulseAudio.ctx;
      for (let k = done + 1; PS.t0 + k * PS.period - pc.currentTime < 0.25; k++) {
        done = k;
        if (k < 4) continue;  // listen to the count-in
        const s = PS.seg, silent = (s.type === 'silent' && k >= s.start && k < s.start + s.beats) || (s.type === 'click' && k >= s.end);
        const start = s.type === 'silent' ? s.start : s.end;
        const off = policy(k, silent ? k - start : null);
        if (off == null) continue;
        clap(ctx.currentTime + (PS.t0 + k * PS.period + off - pc.currentTime));
      }
    }, 20);
  };
})();
"""

POLICIES = {
    "steady": "(k, i) => 0",
    "stop": "(k, i) => i == null ? 0 : null",
    "drag": "(k, i) => i == null ? 0 : 0.012 * (i + 1)",
}


def run(browser, base, name):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.add_init_script(CLAPPER)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/?pulse_click=4&pulse_silent=6&pulse_minutes=0.5")
    page.fill("#tokenInput", TOKEN)
    page.click("#tokenForm button")
    page.click("#modePulse")
    page.wait_for_selector("#pulseHome:not(.hidden) .tempo")
    page.click(".tempo[data-bpm='80']")
    page.evaluate(f"window.__clapAlong({POLICIES[name]})")
    page.click("#pStart")
    page.wait_for_selector("#pulseRun:not(.hidden)")
    page.wait_for_selector("#pulseRun[data-phase=silent]", timeout=15_000)
    page.screenshot(path=str(ROOT / "tests" / f"e2e_pulse_{name}_run.png"))
    page.wait_for_selector("#pulseDone:not(.hidden)", timeout=60_000)
    page.screenshot(path=str(ROOT / "tests" / f"e2e_pulse_{name}.png"))
    rounds = page.evaluate("PS.rounds")
    print(f"  {name}: {page.text_content('#pDoneHead')} | {page.text_content('#pDoneSub')}")
    for r in rounds:
        print(f"    silence {r['target_s']:5}s held {r['held_s']:5}s {'done' if r['completed'] else 'lost'} "
              f"claps {r['claps']:3} offset {r['mean_offset_ms']}ms abs {r['mean_abs_ms']}ms")
    assert not errors, errors
    assert rounds, "no silent stretch"
    page.close()
    return rounds


def main():
    with tempfile.TemporaryDirectory() as data:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        srv = serve(port, data)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.environ.get("CHROME") or None, args=[
                    "--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream"])
                steady = run(browser, base, "steady")
                assert all(r["completed"] for r in steady), steady
                full = [r for r in steady[:-1]]
                assert all(b["target_s"] - a["target_s"] >= 4.5 for a, b in zip(full, full[1:])), "silence did not grow"
                assert all(abs(r["mean_offset_ms"]) <= 25 for r in steady), steady
                stop = run(browser, base, "stop")
                assert all(not r["completed"] and r["held_s"] == 0 and r["claps"] == 0 for r in stop), stop
                drag = run(browser, base, "drag")
                lost = [r for r in drag if not r["completed"]]
                assert lost and all(1 <= r["held_s"] <= 6 and r["mean_offset_ms"] > 0 for r in lost), drag
                browser.close()
            req = urllib.request.Request(f"{base}/api/pulse/state", headers={"x-access-token": TOKEN})
            t80 = next(t for t in json.load(urllib.request.urlopen(req))["tempos"] if t["bpm"] == 80)
            print("  80 bpm history:", {k: t80[k] for k in ("rounds", "best_s", "recent_s", "offset_ms", "start_s")})
            assert t80["rounds"] == len(steady) + len(stop) + len(drag)
        finally:
            srv.kill()
    print("ok")


if __name__ == "__main__":
    main()
