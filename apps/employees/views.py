from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from .forms import EmployeeForm
from .models import Employee

@login_required
def employee_list(request):
    query = request.GET.get("q", "").strip()
    employees = Employee.objects.all()
    if query:
        employees = employees.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(external_id__icontains=query)
        )
    return render(request, "employees/list.html", {"employees": employees, "query": query})

@login_required
def employee_create(request):
    form = EmployeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        employee = form.save()
        messages.success(request, f"{employee.full_name} added.")
        return redirect("employees:list")
    return render(request, "employees/form.html", {"form": form, "title": "Add employee"})

@login_required
def employee_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    form = EmployeeForm(request.POST or None, instance=employee)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Employee updated.")
        return redirect("employees:list")
    return render(request, "employees/form.html", {"form": form, "title": f"Edit {employee.full_name}"})
