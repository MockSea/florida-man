import unittest

from fm.score import score


class ScoreTests(unittest.TestCase):
    def test_deterministic(self):
        h = "Florida Man throws alligator through Wendy's drive-thru window"
        self.assertEqual(score(h), score(h))
        self.assertEqual([score(h)[0] for _ in range(20)], [score(h)[0]] * 20)

    def test_range(self):
        for h in ("", "x", "Florida Man " + "alligator naked Waffle House nugget 911 " * 20):
            s, _ = score(h)
            self.assertGreaterEqual(s, 1)
            self.assertLessEqual(s, 100)

    def test_absurd_beats_mundane(self):
        mundane, _ = score("Florida Man arrested for speeding")
        absurd, _ = score("Florida Man arrested after riding alligator naked into Waffle House")
        self.assertGreater(absurd, mundane + 30)

    def test_grim_penalized(self):
        grim, reasons = score("Florida Man charged with murder after alligator attack")
        light, _ = score("Florida Man charged after alligator ride")
        self.assertLess(grim, light)
        self.assertIn("penalty: grim story", reasons)

    def test_is_grim(self):
        from fm.score import is_grim
        self.assertTrue(is_grim("South Florida Man gets 13 years in violent sex trafficking case"))
        self.assertTrue(is_grim("Florida man charged with kidnapping"))
        self.assertFalse(is_grim("Florida man assaulted neighbor with a pool noodle"))
        self.assertFalse(is_grim("Florida man arrested for trafficking alligators"))
        self.assertFalse(is_grim(None))

    def test_food_is_not_an_animal(self):
        _, r = score("Florida Man throws chicken nuggets")
        self.assertNotIn("animal: bird", r)
        _, r = score("Florida Man chases chicken across I-95")
        self.assertIn("animal: bird", r)

    def test_weapon_that_isnt(self):
        s1, r1 = score("Florida Man attacked roommate with a pool noodle")
        s2, r2 = score("Florida Man attacked roommate with a knife")
        self.assertIn("weapon that isn't: pool noodle", r1)
        self.assertFalse(any(r.startswith("weapon that isn't") for r in r2))
        self.assertGreater(s1, s2)


if __name__ == "__main__":
    unittest.main()
