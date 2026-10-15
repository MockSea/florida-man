import unittest

from fm import towns


class TownTests(unittest.TestCase):
    def test_canonical(self):
        self.assertEqual(towns.canonical("tampa"), "Tampa")
        self.assertEqual(towns.canonical("  Saint Petersburg "), "St. Petersburg")
        self.assertEqual(towns.canonical("Port St Lucie, FL"), "Port St. Lucie")
        self.assertEqual(towns.canonical("opa locka"), "Opa-locka")
        self.assertEqual(towns.canonical("Lake Worth"), "Lake Worth Beach")
        self.assertEqual(towns.canonical("Port Charlotte"), "Port Charlotte")

    def test_rejects(self):
        for bad in ("Atlanta", "", "   ", None, "Tampa<script>", "x" * 100, "Florida", 7):
            self.assertIsNone(towns.canonical(bad), bad)

    def test_find_in_text(self):
        self.assertEqual(towns.find_in_text("Florida Man in Miami Beach steals bus"), "Miami Beach")
        self.assertIsNone(towns.find_in_text("Florida Man steals golf cart at sunrise"))
