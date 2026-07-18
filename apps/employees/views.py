from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from .forms import EmployeeForm, EmployeeMergeForm
from .models import Employee
from .services import delete_employee_and_history, merge_employees


@login_required
def employee_list(request):
    query = request.GET.get("q", "").strip()
    employees = Employee.objects.annotate(
        shift_count=Count("roster_shifts")
    )
    if query:
        employees = employees.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(external_id__icontains=query)
        )
    return render(
        request,
        "employees/list.html",
        {"employees": employees, "query": query},
    )


@login_required
def employee_create(request):
    form = EmployeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        employee = form.save()
        messages.success(request, f"{employee.full_name} added.")
        return redirect("employees:list")
    return render(
        request,
        "employees/form.html",
        {"form": form, "title": "Add employee"},
    )


@login_required
def employee_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    form = EmployeeForm(request.POST or None, instance=employee)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Employee updated.")
        return redirect("employees:list")
    return render(
        request,
        "employees/form.html",
        {"form": form, "title": f"Edit {employee.full_name}"},
    )


@login_required
def employee_delete(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    shift_count = employee.roster_shifts.count()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "deactivate":
            employee.is_active = False
            employee.save(update_fields=["is_active", "updated_at"])
            messages.success(
                request,
                f"{employee.full_name} was deactivated. Their history was kept.",
            )
            return redirect("employees:list")

        if action == "delete":
            deleted_shifts = delete_employee_and_history(employee)
            messages.success(
                request,
                f"Employee deleted with {deleted_shifts} shift records.",
            )
            return redirect("employees:list")

    return render(
        request,
        "employees/delete.html",
        {"employee": employee, "shift_count": shift_count},
    )


@login_required
def employee_merge(request, pk):
    source = get_object_or_404(Employee, pk=pk)
    form = EmployeeMergeForm(
        request.POST or None,
        source_employee=source,
    )

    if request.method == "POST" and form.is_valid():
        target = form.cleaned_data["target_employee"]
        result = merge_employees(
            source=source,
            target=target,
            conflict_choice=form.cleaned_data["conflict_choice"],
        )
        messages.success(
            request,
            f"Merged into {target.full_name}. "
            f"Moved {result['moved']} shifts; "
            f"resolved {result['conflicts']} conflicts.",
        )
        messages.info(
            request,
            "Run Learning again so the employee pattern is recalculated.",
        )
        return redirect("employees:list")

    return render(
        request,
        "employees/merge.html",
        {
            "source": source,
            "form": form,
            "shift_count": source.roster_shifts.count(),
        },
    )
