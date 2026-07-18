from django.test import SimpleTestCase
from .services import parse_shift_cell

class ShiftParserTests(SimpleTestCase):
    def assert_shift(self, text, start_hour, end_hour):
        parsed, error = parse_shift_cell(text)
        self.assertIsNone(error)
        self.assertEqual(parsed[0][0].hour, start_hour)
        self.assertEqual(parsed[0][1].hour, end_hour)

    def test_standard(self):
        self.assert_shift("8.30-5pm", 8, 17)

    def test_split(self):
        parsed, error = parse_shift_cell("12.30-3.30 & 6-10.30")
        self.assertIsNone(error)
        self.assertEqual(len(parsed), 2)

    def test_dot_separator_typo(self):
        self.assert_shift("8.30.4.30", 8, 16)

    def test_hyphen_minutes_typo(self):
        self.assert_shift("6-10-30", 18, 22)

    def test_close(self):
        self.assert_shift("8pm-close", 20, 1)

    def test_midnight(self):
        self.assert_shift("4-12mn", 16, 0)

    def test_single_time_needs_choice(self):
        parsed, error = parse_shift_cell("12.30")
        self.assertEqual(parsed, [])
        self.assertIsNotNone(error)
