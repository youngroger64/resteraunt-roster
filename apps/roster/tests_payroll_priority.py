from django.test import TestCase
from apps.employees.models import Employee
from apps.roster.models import EmployeePattern
from apps.roster.services.generator import hours_target_score

class PayrollPriorityTests(TestCase):
    def pattern(self,name,hours):
        employee=Employee.objects.create(first_name=name, department="restaurant", can_work_restaurant=True)
        return EmployeePattern.objects.create(employee=employee, average_weekly_hours=hours, average_days_worked=4)
    def test_high_hour_underallocated_employee_has_priority(self):
        high=self.pattern("High",30)
        low=self.pattern("Low",10)
        self.assertGreater(hours_target_score(high,10,5), hours_target_score(low,5,5))
    def test_half_target_gets_strong_boost(self):
        pattern=self.pattern("Regular",20)
        self.assertGreaterEqual(hours_target_score(pattern,9,5),100)
