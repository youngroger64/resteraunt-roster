from io import BytesIO
from unittest.mock import patch

from django.test import TestCase

from apps.imports_app.payroll import import_payroll
from apps.roster.models import PayrollRecord


class _Page:
    def __init__(self, text):
        self.text = text

    def extract_text(self):
        return self.text


class _Reader:
    def __init__(self, _file_obj):
        self.pages = [
            _Page(
                """
                Hours for week ending: 02/08/2026
                Name Ordinary Hours Sunday Hours Notes
                1 Cori Hamm 32.00 7.75
                9 Catherine Kirby 28.50 6.00
                23 Michelle Spencer Salary --
                """
            )
        ]


class _EmptyReader:
    def __init__(self, _file_obj):
        self.pages = [_Page("")]


class PayrollPdfImportTests(TestCase):
    @patch("apps.imports_app.payroll.PdfReader", _Reader)
    def test_text_pdf_payroll_import(self):
        stream = BytesIO(b"%PDF mock")
        stream.name = "wages.pdf"

        week, count, issues = import_payroll(stream)

        self.assertEqual(str(week.week_end), "2026-08-02")
        self.assertEqual(count, 2)
        self.assertEqual(
            float(
                PayrollRecord.objects.get(
                    employee__first_name="Catherine"
                ).total_hours
            ),
            34.5,
        )
        self.assertTrue(any("No hourly total" in item["message"] for item in issues))

    @patch("apps.imports_app.payroll.PdfReader", _EmptyReader)
    def test_image_only_pdf_gets_clear_error(self):
        stream = BytesIO(b"%PDF mock")
        stream.name = "scan.pdf"

        with self.assertRaisesMessage(
            ValueError,
            "contains no selectable text",
        ):
            import_payroll(stream)
