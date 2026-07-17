from collections import Counter
from apps.roster.models import Shift

def employee_summary(employee):
    shifts = Shift.objects.filter(employee=employee)
    departments = Counter(shifts.values_list("department", flat=True))
    return {
        "shift_count": shifts.count(),
        "department_counts": dict(departments),
    }
