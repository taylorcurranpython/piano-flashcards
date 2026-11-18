"""Browser end-to-end test with spoken answers, no human needed.

Spoken note names (tests/audio/*.wav, Google TTS) are played into the page as its microphone, so the real
voice path runs: speech-onset detection, offline Vosk recognition, grading, FSRS, history.
Two runs:
  1. getUserMedia is replaced by a WebAudio stream the test controls, so each answer is spoken at a known
     delay after the note appears; checks the recorded time matches that delay (not recognition time).
  2. Chrome's own fake capture device plays a looping "C" file, for a one-note C4 range.

Needs: pip install playwright && playwright install chromium (or CHROME=/path/to/chrome). The ~40 MB Vosk
model is downloaded to .cache/ on first run (VOSK_MODEL=... to use another copy).
In run 2 the file loops, so speech is already under way when the note appears and the time reads ~0.
Run:  python -m tests.e2e_voice
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
AUDIO = ROOT / "tests" / "audio"
MODEL_URL = "https://ccoreilly.github.io/vosk-browser/models/vosk-model-small-en-us-0.15.tar.gz"
MODEL = Path(os.environ.get("VOSK_MODEL", ROOT / ".cache/vosk-model-small-en-us-0.15.tar.gz"))
LEAD_MS = {"A": 90, "B": 90, "C": 120, "D": 90, "E": 90, "F": 80, "G": 100}  # silence before speech in each clip
TOLERANCE_MS = 150
TOKEN = "e2e-token"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(port, data_dir):
    env = {**os.environ, "DATA_DIR": str(data_dir), "ACCESS_TOKEN": TOKEN}
    env.pop("DATABASE_URL", None)
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--port", str(port)], cwd=ROOT, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health")
            return p
        except OSError:
            time.sleep(0.1)
    p.kill()
    raise RuntimeError("server did not start")


FAKE_MIC = """
(() => {
  let ctx, dest;
  const ensure = () => { if (!ctx) { ctx = new AudioContext(); dest = ctx.createMediaStreamDestination(); } return ctx; };
  navigator.mediaDevices.getUserMedia = async () => { ensure(); await ctx.resume(); return dest.stream; };
  const cache = {};
  // A burst of white noise (a cough, a click, a chair) that is not an answer.
  window.__noise = (delayMs, t0, durMs) => {
    ensure();
    const buf = ctx.createBuffer(1, Math.round(ctx.sampleRate * durMs / 1000), ctx.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * 0.3;
    const src = ctx.createBufferSource(); src.buffer = buf; src.connect(dest);
    src.start(ctx.currentTime + Math.max(0, t0 + delayMs - performance.now()) / 1000);
  };
  window.__say = async (letter, delayMs, t0) => {
    ensure();
    cache[letter] = cache[letter] || await fetch('/__audio/' + letter + '.wav').then(r => r.arrayBuffer()).then(b => ctx.decodeAudioData(b));
    const wait = t0 + delayMs - performance.now();
    const src = ctx.createBufferSource(); src.buffer = cache[letter]; src.connect(dest);
    src.start(ctx.currentTime + Math.max(0, wait) / 1000);
  };
})();
"""


def route_assets(page):
    page.route(re.compile(r"^[^?]*/__audio/[A-Z_a-z]+\.wav$"), lambda r: r.fulfill(path=str(AUDIO / r.request.url.rsplit("/", 1)[1])))
    page.route(re.compile(r"^[^?]*/__vosk/model\.tar\.gz$"), lambda r: r.fulfill(path=str(MODEL), headers={"Access-Control-Allow-Origin": "*"}))


def open_drill(page, base, extra=""):
    page.goto(f"{base}/?model={base}/__vosk/model.tar.gz{extra}")
    page.fill("#tokenInput", TOKEN)
    page.click("#tokenForm button")
    page.wait_for_selector("#homeView:not(.hidden) .scope")
    page.click("#micBtn")
    page.wait_for_selector("#micBtn.on", timeout=120_000)


def run_controlled(browser, base):
    page = browser.new_page()
    page.add_init_script(FAKE_MIC)
    route_assets(page)
    open_drill(page, base)
    page.click("text=Treble · staff")
    page.click("#startBtn")
    # (delay before speaking, answer right?) -> expected grade
    # A fourth value is when a noise burst plays before the answer; it must not count as the answer's start.
    plan = [(400, True, "Easy"), (1300, True, "Good"), (2400, True, "Hard"), (500, False, "Again")] * 3
    plan += [(2400, True, "Hard", 300), (1300, True, "Good", 200)]
    results, seen, wrong_card = [], [], None
    for delay, right, grade, *noise in plan:
        page.wait_for_function("() => document.querySelector('#staff svg') && !document.querySelector('#fb').textContent")
        page.wait_for_timeout(50)
        card = page.get_attribute("#staff svg", "data-card")
        letter = card.split(":")[1][0]
        say = letter if right else "ABCDEFG"[("ABCDEFG".index(letter) + 3) % 7]
        if not right:
            wrong_card = card
        page.evaluate("([l, d, n]) => { const t = performance.now(); if (n != null) window.__noise(n, t, 120); window.__say(l, d, t); }",
                      [say, delay, noise[0] if noise else None])
        page.wait_for_function("() => document.querySelector('#fb').dataset.rating && document.querySelector('#fb').textContent", timeout=15_000)
        rating = page.get_attribute("#fb", "data-rating")
        shown = page.text_content("#timer")
        ms = page.evaluate("S.answered[S.answered.length-1].ms")
        heard = page.text_content("#heard")
        results.append((card, say, delay, ms, rating, grade))
        seen.append(card)
        late = ms - delay - LEAD_MS[say]
        print(f"  {card:10} {'noise at ' + str(noise[0]) + 'ms, ' if noise else ''}said {say}, speech at {delay + LEAD_MS[say]:4}ms -> recorded {ms:4}ms ({late:+4}) {shown} {rating:5} expected {grade:5} {heard}")
        page.evaluate("document.querySelector('#fb').dataset.rating = ''")
    page.screenshot(path=str(ROOT / "tests" / "e2e_voice.png"))
    bad = [r for r in results if r[4] != r[5] or not (-30 <= r[3] - (r[2] + LEAD_MS[r[1]]) <= TOLERANCE_MS)]
    assert not bad, f"timing/grade mismatches: {bad}"
    # The missed card came back later in the same session.
    assert wrong_card and page.evaluate(f"S.queue.includes('{wrong_card}') || S.answered.filter(a => a.id === '{wrong_card}').length > 1")
    page.click("#endBtn")
    page.wait_for_selector("#doneView:not(.hidden)")
    print("  done:", page.text_content("#doneHead"), "|", page.text_content("#doneSub"))
    page.click("#statsBtn")
    page.wait_for_selector("#statsView:not(.hidden) .cell")
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(ROOT / "tests" / "e2e_stats_phone.png"), full_page=True)
    page.close()
    return results


def run_fake_device(pw, chrome, base):
    b = pw.chromium.launch(executable_path=chrome, args=[
        "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-audio-capture={AUDIO / 'C_loop.wav'}", "--autoplay-policy=no-user-gesture-required"])
    page = b.new_page()
    route_assets(page)
    open_drill(page, base)
    page.click("text=Custom range")
    page.select_option("#cClef", "treble")
    c4 = page.evaluate("cards.find(c => c.id === 'treble:C4').pos")
    page.select_option("#cLow", str(c4))
    page.select_option("#cHigh", str(c4))
    page.click("#cUse")
    page.wait_for_function("() => S.answered.length >= 1", timeout=20_000)
    a = page.evaluate("S.answered[0]")
    print(f"  fake capture device: card treble:C4, heard {a['letter']}, {a['ms']}ms, {a['rating']}")
    assert a["letter"] == "C"
    b.close()


def main():
    if not MODEL.exists():
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        print("downloading Vosk model...")
        urllib.request.urlretrieve(MODEL_URL, MODEL)
    port, data_dir = free_port(), Path(tempfile.mkdtemp())
    server = serve(port, data_dir)
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as pw:
            chrome = os.environ.get("CHROME") or None
            browser = pw.chromium.launch(executable_path=chrome, args=["--autoplay-policy=no-user-gesture-required",
                                                                     "--use-fake-ui-for-media-stream"])
            print("controlled mic:")
            run_controlled(browser, base)
            browser.close()
            print("chrome fake capture device:")
            run_fake_device(pw, chrome, base)
        print("OK")
    finally:
        server.kill()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
