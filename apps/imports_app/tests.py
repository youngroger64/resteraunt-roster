from django.test import SimpleTestCase
from .services import parse_shift_cell

class ShiftParserTests(SimpleTestCase):
    def test_standard(self):
        parsed, error = parse_shift_cell("8.30-5pm")
        self.assertIsNone(error)
        self.assertEqual(parsed[0][1].hour, 17)

    def test_split(self):
        parsed, error = parse_shift_cell("12.30-3.30 & 6-10.30")
        self.assertIsNone(error)
        self.assertEqual(len(parsed), 2)

    def test_typo(self):
        parsed, error = parse_shift_cell("8.30.4.30")
        self.assertIsNone(error)
        self.assertEqual(parsed[0][1].hour, 16)
