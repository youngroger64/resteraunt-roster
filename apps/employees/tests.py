from django.test import TestCase
from .models import Employee

class EmployeeTests(TestCase):
    def test_full_name(self):
        employee = Employee(first_name="Cori", last_name="Test")
        self.assertEqual(employee.full_name, "Cori Test")
