from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0002_employee_can_work_kitchen_employee_can_work_washup_and_more"),
        ("roster", "0014_alter_coveragepattern_department_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnnualLeave",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("date", models.DateField()),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="annual_leave", to="employees.employee")),
                ("roster_week", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="annual_leave", to="roster.rosterweek")),
            ],
            options={
                "ordering": ["date", "employee__first_name"],
            },
        ),
        migrations.AddConstraint(
            model_name="annualleave",
            constraint=models.UniqueConstraint(fields=("roster_week", "employee", "date"), name="unique_annual_leave_day"),
        ),
    ]
