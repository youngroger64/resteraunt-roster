from apps.employees.models import Employee

def eligible_replacements(shift):
    candidates = Employee.objects.filter(is_active=True).exclude(pk=shift.employee_id)
    if shift.department == "bar":
        candidates = candidates.filter(can_work_bar=True)
    else:
        candidates = candidates.filter(can_work_restaurant=True)
    return candidates.order_by("first_name", "last_name")
