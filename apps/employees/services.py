from django.db import transaction
from apps.roster.models import EmployeePattern, Shift
from .models import Employee


@transaction.atomic
def merge_employees(source: Employee, target: Employee, conflict_choice: str):
    moved = 0
    conflicts = 0

    source_shifts = list(
        Shift.objects.filter(employee=source).select_related("roster_week")
    )

    for shift in source_shifts:
        conflicting = Shift.objects.filter(
            employee=target,
            roster_week=shift.roster_week,
            date=shift.date,
            segment=shift.segment,
        ).first()

        if conflicting:
            conflicts += 1
            if conflict_choice == "use_source":
                conflicting.delete()
                shift.employee = target
                shift.save(update_fields=["employee", "updated_at"])
                moved += 1
            else:
                shift.delete()
        else:
            shift.employee = target
            shift.save(update_fields=["employee", "updated_at"])
            moved += 1

    # Learned patterns are recalculated after merges.
    EmployeePattern.objects.filter(employee=source).delete()
    EmployeePattern.objects.filter(employee=target).delete()

    source.delete()
    return {"moved": moved, "conflicts": conflicts}


@transaction.atomic
def delete_employee_and_history(employee: Employee):
    shift_count = Shift.objects.filter(employee=employee).count()
    Shift.objects.filter(employee=employee).delete()
    EmployeePattern.objects.filter(employee=employee).delete()
    employee.delete()
    return shift_count
