"""Browser checks for voice failure and fallback paths.

  1. The voice model fails to download: Start returns home with a message, and every mic stream and Vosk
     worker it started is stopped, including on a retry.
  2. Browser speech engine (?engine=browser) with speech too quiet for the energy detector: the recognizer's
     own speech-start event times the answer and the answer is graded.
Run:  python -m tests.e2e_voice_edges   (CHROME=/path/to/chrome optional)
"""

import os
import re
import tempfile

from playwright.sync_api import sync_playwright

from tests.e2e_voice import TOKEN, free_port, serve

SILENT_MIC = """
(() => {
  window.__streams = [];
  navigator.mediaDevices.getUserMedia = async () => {
    const ctx = new AudioContext(), dest = ctx.createMediaStreamDestination();
    window.__streams.push(dest.stream); return dest.stream;
  };
})();
"""
FAKE_SR = """
(() => {
  window.SpeechRecognition = class { constructor() { window.__sr = this; } start() {} stop() {} };
})();
"""


def login(page, base, query):
    page.goto(f"{base}/{query}")
    page.fill("#tokenInput", TOKEN)
    page.click("#tokenForm button")
    page.wait_for_selector("#homeView:not(.hidden) .scope")


def model_fails(browser, base):
    page = browser.new_page()
    page.add_init_script(SILENT_MIC)
    page.route(re.compile(r"^[^?]*/__missing/model\.tar\.gz$"), lambda r: r.fulfill(status=404, body="no"))
    login(page, base, f"?model={base}/__missing/model.tar.gz")
    for attempt in (1, 2):
        page.click("#startBtn")
        page.wait_for_selector("#homeView:not(.hidden)", timeout=30_000)
        page.wait_for_function("document.getElementById('homeMsg').textContent.includes('Voice is needed')")
        live = page.evaluate("window.__streams.flatMap(s => s.getTracks()).filter(t => t.readyState === 'live').length")
        opened = page.evaluate("window.__streams.length")
        page.wait_for_timeout(300)
        workers = len(page.workers)
        print(f"  attempt {attempt}: {opened} streams opened, {live} live tracks, {workers} live workers; "
              f"message: {page.text_content('#homeMsg')}")
        assert opened == attempt and live == 0 and workers == 0
    assert not page.query_selector("#staff svg") or page.is_hidden("#drillView")
    page.close()


def quiet_browser_answer(browser, base):
    page = browser.new_page()
    page.add_init_script(SILENT_MIC)
    page.add_init_script(FAKE_SR)
    login(page, base, "?engine=browser")
    page.click("#startBtn")
    page.wait_for_selector("#staff svg")
    page.wait_for_function("document.getElementById('timer').textContent !== '0.0s'")
    letter = page.get_attribute("#staff svg", "data-card").split(":")[1][0]
    page.evaluate("window.__sr.onspeechstart()")
    page.wait_for_timeout(400)
    page.evaluate("""l => { const res = [{transcript: l}]; res.isFinal = true; window.__sr.onresult({results: [res]}); }""", letter)
    page.wait_for_selector("#fb[data-rating]", timeout=5_000)
    fb, rating = page.text_content("#fb"), page.get_attribute("#fb", "data-rating")
    print(f"  said {letter} quietly: feedback “{fb}”, {rating}")
    assert rating == "Easy" and fb.startswith(letter)
    page.close()


def main():
    port = free_port()
    with tempfile.TemporaryDirectory() as d:
        server = serve(port, d)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.environ.get("CHROME") or None)
                base = f"http://127.0.0.1:{port}"
                print("model download fails:"); model_fails(browser, base)
                print("browser engine, quiet answer:"); quiet_browser_answer(browser, base)
                browser.close()
        finally:
            server.kill()
    print("OK")


if __name__ == "__main__":
    main()
