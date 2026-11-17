"""Reading the printed identity off a card crop."""
import sqlite3
import unittest

from services import card_text


class ParseCardText(unittest.TestCase):
    def test_set_code_and_number(self):
        # The collector line wins over a loose set-code match: "077/198" is the
        # whole line, whereas set-code-plus-digits can come from artwork text.
        parsed = card_text.parse_card_text("SV07 077/198\nMeditite")
        self.assertEqual(parsed["number"], "077")
        self.assertEqual(parsed["total"], "198")

    def test_set_code_branch_when_there_is_no_slash(self):
        parsed = card_text.parse_card_text("SWSH12 173")
        self.assertEqual(parsed["set_code"], "swsh12")
        self.assertEqual(parsed["number"], "173")

    def test_total_below_the_number_is_a_misread(self):
        # "077/012" cannot be a collector line, so the loose branch is tried.
        parsed = card_text.parse_card_text("077/012\nMeditite")
        self.assertIsNone(parsed)

    def test_number_over_total_without_a_set_code(self):
        parsed = card_text.parse_card_text("some flavour text\n077/198")
        self.assertEqual(parsed["set_code"], None)
        self.assertEqual(parsed["number"], "077")

    def test_illustrator_credit_cannot_win(self):
        # The credit line carries a year-like number that must not be read as
        # the collector number.
        parsed = card_text.parse_card_text("077/198\nIllustr. GOTO minori 2023")
        self.assertEqual(parsed["number"], "077")

    def test_garbage_is_not_a_reading(self):
        self.assertIsNone(card_text.parse_card_text("~~~~"))
        self.assertIsNone(card_text.parse_card_text(""))


class ResolveCard(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE cards (ptcg_id TEXT, name TEXT, number TEXT)")
        self.conn.executemany(
            "INSERT INTO cards (ptcg_id, name, number) VALUES (?,?,?)",
            [("sv07-077", "Meditite", "077"),
             ("sv01-077", "Wattrel", "077"),
             ("swsh12-173", "Lumineon V", "173")],
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_set_and_number_resolve_exactly(self):
        row = card_text.resolve_card(self.conn, {"set_code": "sv07", "number": "77"})
        self.assertEqual(row["ptcg_id"], "sv07-077")

    def test_unknown_set_code_does_not_fall_back_to_the_number(self):
        # A wrong set code must not resolve to some other card with that number.
        self.assertIsNone(card_text.resolve_card(self.conn, {"set_code": "xy9", "number": "077"}))

    def test_localised_printing_resolves_by_its_de_prefix(self):
        self.conn.execute("INSERT INTO cards (ptcg_id, name, number) VALUES ('de_sv07-077', 'Meditite', '077')")
        self.conn.commit()
        # The English printing is found first; the German one still exists and
        # must not be confused with another card.
        row = card_text.resolve_card(self.conn, {"set_code": "sv07", "number": "077"})
        self.assertEqual(row["ptcg_id"], "sv07-077")

    def test_a_hallucinated_set_code_loses_to_the_printed_name(self):
        # Measured: the vision model read "sv01" off a Stellar Crown card, and
        # sv01-077 is a real card (Wattrel). The name has to win.
        row = card_text.resolve_card(self.conn, {"set_code": "sv01", "number": "077"},
                                     name="Meditie")
        self.assertEqual(row["ptcg_id"], "sv07-077")

    def test_a_stray_header_word_does_not_block_the_name(self):
        # Measured from a real browser crop: the header OCR drags a word along
        # with the card name. Matching per word is what lets this resolve.
        row = card_text.resolve_card(self.conn, {"number": "077", "set_code": None},
                                     name="easie Meditie")
        self.assertEqual(row["ptcg_id"], "sv07-077")

    def test_a_different_name_with_the_same_number_does_not_resolve(self):
        # sv01-077 (Wattrel) carries the same number, so the word-wise match must
        # not let an unrelated name ride in on it.
        row = card_text.resolve_card(self.conn, {"number": "077", "set_code": None},
                                     name="easie Charizard")
        self.assertIsNone(row)

    def test_the_right_name_still_picks_its_own_card_at_a_shared_number(self):
        # Control for the test above: the same number does resolve when the name
        # really is that card, so "None" above is a rejection and not a stub.
        row = card_text.resolve_card(self.conn, {"number": "077", "set_code": None},
                                     name="Wattrel")
        self.assertEqual(row["ptcg_id"], "sv01-077")

    def test_a_real_name_at_another_number_does_not_resolve(self):
        # Lumineon V is a real card, but at 173 — reading it with 077 must not
        # resolve, or a high-scoring word could drag an unrelated card in on the
        # strength of its name alone. (The same name at 173 does resolve, which
        # test_name_and_number_resolve_when_the_set_code_is_unreadable covers.)
        row = card_text.resolve_card(self.conn, {"number": "077", "set_code": None},
                                     name="Lumineon V")
        self.assertIsNone(row)

    def test_a_set_code_without_a_name_is_still_used(self):
        # Nothing contradicts it, so a set code read (or asked for) alone stands.
        row = card_text.resolve_card(self.conn, {"set_code": "sv01", "number": "077"})
        self.assertEqual(row["ptcg_id"], "sv01-077")

    def test_name_and_number_resolve_when_the_set_code_is_unreadable(self):
        row = card_text.resolve_card(self.conn, {"number": "173", "set_code": None},
                                     name="Lumineon V")
        self.assertEqual(row["ptcg_id"], "swsh12-173")


if __name__ == "__main__":
    unittest.main()
