"""API: catalog, speed grading, FSRS scheduling, auth.   Run:  python -m tests.test_api"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("DATABASE_URL", None)
os.environ["ACCESS_TOKEN"] = "test-token"

from starlette.testclient import TestClient  # noqa: E402

from app import drill, server  # noqa: E402

H = {"X-Access-Token": "test-token"}


class ApiTest(unittest.TestCase):
    c = TestClient(server.app)

    def post(self, path, body):
        r = self.c.post(path, json=body, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_catalog(self):
        cat = drill.CATALOG
        self.assertEqual(len(cat), 37)
        # Same letter, different octave = different cards.
        self.assertTrue({"treble:C4", "treble:C5", "treble:C6", "bass:C2", "bass:C3", "bass:C4"} <= set(cat))
        self.assertFalse(cat["treble:E4"]["ledger"])
        self.assertTrue(cat["treble:C4"]["ledger"])
        self.assertTrue(cat["treble:A5"]["ledger"])
        self.assertFalse(cat["bass:G2"]["ledger"])
        self.assertTrue(cat["bass:E2"]["ledger"])
        self.assertEqual(sum(c["ledger"] for c in cat.values()), 15)

    def test_grading(self):
        R = drill.Rating
        self.assertEqual(drill.rating_for(False, 300), R.Again)
        self.assertEqual(drill.rating_for(True, 800), R.Easy)
        self.assertEqual(drill.rating_for(True, 1500), R.Good)
        self.assertEqual(drill.rating_for(True, 2600), R.Hard)

    def test_auth_and_page(self):
        self.assertEqual(self.c.get("/api/state").status_code, 401)
        self.assertEqual(self.c.get("/api/state", headers={"X-Access-Token": "nope"}).status_code, 401)
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(self.c.get("/drill.js").status_code, 200)

    def test_practice_flow(self):
        pid = self.post("/api/practice", {"scope": "treble staff"})["practice_id"]
        ans = lambda card, letter, ms: self.post("/api/answer", {"practice_id": pid, "card_id": card, "answer": letter, "ms": ms})  # noqa: E731

        fast, wrong, slow = ans("treble:E4", "e", 700), ans("treble:F4", "G", 900), ans("treble:G4", "G", 3200)
        self.assertEqual((fast["correct"], fast["rating"]), (True, "Easy"))
        self.assertEqual((wrong["correct"], wrong["rating"]), (False, "Again"))
        self.assertEqual(slow["rating"], "Hard")
        now = datetime.now(timezone.utc)
        due = {k: datetime.fromisoformat(v["due"]) for k, v in (("easy", fast), ("again", wrong), ("hard", slow))}
        self.assertLess(due["again"], now + timedelta(minutes=5))  # missed: back within minutes
        self.assertGreater(due["easy"], now + timedelta(days=1))  # instant: not again today
        self.assertLess(due["again"], due["hard"])
        self.assertLess(due["hard"], due["easy"])

        st = {c["id"]: c for c in self.c.get("/api/state", headers=H).json()["cards"]}
        self.assertFalse(st["treble:E4"]["is_due"])
        self.assertTrue(st["treble:A4"]["is_due"] and st["treble:A4"]["new"])
        self.assertEqual(st["treble:E4"]["median_ms"], 700)
        self.assertEqual((st["treble:F4"]["attempts"], st["treble:F4"]["correct"]), (1, 0))

        bad = self.c.post("/api/answer", headers=H, json={"practice_id": pid, "card_id": "treble:H9", "answer": "C", "ms": 1})
        self.assertEqual(bad.status_code, 400)
        done = self.post("/api/finish", {"practice_id": pid})
        self.assertEqual(done["practice"], {"answered": 3, "correct": 2, "median_ms": 1950})


class PulseTest(unittest.TestCase):
    c = TestClient(server.app)
    post = ApiTest.post

    def test_suggest_and_start_target(self):
        from app import pulse
        now = datetime.now(timezone.utc)
        mk = lambda held, target, done, days=0: pulse.PulseRound(  # noqa: E731
            bpm=80, at=(now - timedelta(days=days)).replace(tzinfo=None), target_s=target, held_s=held,
            completed=done, claps=10, mean_offset_ms=-20.0)
        empty = {t: pulse.tempo_stats([], now) for t in pulse.TEMPOS}
        self.assertEqual(pulse.suggest(empty), 80)  # nothing practiced: start mid-range
        self.assertEqual(empty[80]["start_s"], pulse.BASE_SILENT_S)
        st = {**empty, 80: pulse.tempo_stats([mk(20, 20, True), mk(25, 25, True), mk(12, 30, False)], now)}
        self.assertEqual(st[80]["start_s"], 25)  # longest silence held lately
        self.assertEqual((st[80]["best_s"], st[80]["recent_s"], st[80]["offset_ms"]), (25, 20, -20))
        self.assertIn(pulse.suggest(st), (70, 90))  # untried tempos first, nearest to the middle
        # Every tempo practiced today and held well except 50, which is weak: suggest 50.
        good = [mk(60, 60, True)] * 5
        st = {t: pulse.tempo_stats(good, now) for t in pulse.TEMPOS}
        st[50] = pulse.tempo_stats([mk(8, 20, False)] * 5, now)
        self.assertEqual(pulse.suggest(st), 50)
        # A strong tempo not practiced for two weeks outranks the same tempo practiced today.
        st[50] = pulse.tempo_stats(good, now)
        st[110] = pulse.tempo_stats([mk(60, 60, True, days=14)] * 5, now)
        self.assertEqual(pulse.suggest(st), 110)

    def test_pulse_flow(self):
        st = self.c.get("/api/pulse/state", headers=H).json()
        self.assertEqual([t["bpm"] for t in st["tempos"]], list(range(40, 121, 10)))
        self.assertEqual(self.c.post("/api/pulse/start", headers=H, json={"bpm": 200}).status_code, 400)
        sid = self.post("/api/pulse/start", {"bpm": 60, "minutes": 3})["session_id"]
        self.post("/api/pulse/round", {"session_id": sid, "target_s": 20, "held_s": 20, "completed": True,
                                       "claps": 20, "mean_offset_ms": -30, "mean_abs_ms": 35})
        lost = self.post("/api/pulse/round", {"session_id": sid, "target_s": 25, "held_s": 11.5, "completed": False,
                                              "claps": 14, "mean_offset_ms": 10})
        self.assertEqual((lost["held_s"], lost["completed"]), (11.5, False))
        done = self.post("/api/pulse/finish", {"session_id": sid})["session"]
        self.assertEqual(done, {"bpm": 60, "rounds": 2, "completed": 1, "best_s": 20.0, "offset_ms": -10})
        t60 = {t["bpm"]: t for t in self.c.get("/api/pulse/state", headers=H).json()["tempos"]}[60]
        self.assertEqual((t60["rounds"], t60["start_s"], t60["best_s"]), (2, 20, 20.0))
        self.assertEqual(self.post("/api/pulse/start", {"bpm": 60})["start_s"], 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
