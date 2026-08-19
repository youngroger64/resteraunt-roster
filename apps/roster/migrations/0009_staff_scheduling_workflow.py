from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("roster", "0008_shifttemplatepattern"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EmployeeScheduleProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("target_hours", models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ("target_days", models.PositiveSmallIntegerField(default=0)),
                ("preferred_department", models.CharField(blank=True, choices=[("restaurant", "Restaurant"), ("bar", "Bar")], max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("employee", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="schedule_profile", to="employees.employee")),
            ],
            options={"ordering": ["employee__first_name", "employee__last_name"]},
        ),
        migrations.CreateModel(
            name="AvailabilityException",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("date", models.DateField()),
                ("unavailable", models.BooleanField(default=False)),
                ("available_from", models.TimeField(blank=True, null=True)),
                ("available_until", models.TimeField(blank=True, null=True)),
                ("note", models.CharField(blank=True, max_length=250)),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="availability_exceptions", to="employees.employee")),
            ],
            options={"ordering": ["date", "employee__first_name", "employee__last_name"]},
        ),
        migrations.CreateModel(
            name="ShiftResponse",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("seen", "Seen"), ("confirmed", "Confirmed"), ("cannot_work", "Cannot work"), ("resolved", "Resolved")], default="seen", max_length=20)),
                ("reason", models.CharField(blank=True, max_length=250)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shift_responses", to="employees.employee")),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="resolved_shift_responses", to=settings.AUTH_USER_MODEL)),
                ("shift", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="responses", to="roster.shift")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="OpenShiftRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("requested", "Requested"), ("approved", "Approved"), ("declined", "Declined")], default="requested", max_length=20)),
                ("note", models.CharField(blank=True, max_length=250)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="decided_open_shift_requests", to=settings.AUTH_USER_MODEL)),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="open_shift_requests", to="employees.employee")),
                ("open_shift", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="requests", to="roster.openshift")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(model_name="availabilityexception", constraint=models.UniqueConstraint(fields=("employee", "date"), name="unique_employee_availability_exception_date")),
        migrations.AddConstraint(model_name="shiftresponse", constraint=models.UniqueConstraint(fields=("shift", "employee"), name="unique_employee_shift_response")),
        migrations.AddConstraint(model_name="openshiftrequest", constraint=models.UniqueConstraint(fields=("open_shift", "employee"), name="unique_employee_open_shift_request")),
    ]
