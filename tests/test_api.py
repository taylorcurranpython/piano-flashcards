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


if __name__ == "__main__":
    unittest.main(verbosity=2)
