from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("roster", "0004_coveragepattern")]
    operations = [
        migrations.CreateModel(
            name="PayrollWeek",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("week_end", models.DateField(unique=True)),
                ("source_name", models.CharField(blank=True, max_length=255)),
            ],
            options={"ordering":["-week_end"]},
        ),
        migrations.CreateModel(
            name="PayrollRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("ordinary_hours", models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ("sunday_hours", models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ("overtime_hours", models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ("total_hours", models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ("notes", models.CharField(blank=True, max_length=500)),
                ("source_row", models.PositiveIntegerField(default=0)),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payroll_records", to="employees.employee")),
                ("payroll_week", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="records", to="roster.payrollweek")),
            ],
            options={"ordering":["payroll_week__week_end","employee__first_name"]},
        ),
        migrations.AddConstraint(
            model_name="payrollrecord",
            constraint=models.UniqueConstraint(fields=("payroll_week","employee"), name="unique_payroll_employee_week"),
        ),
    ]
