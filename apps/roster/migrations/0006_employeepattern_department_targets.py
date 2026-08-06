from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("roster", "0005_payrollweek_payrollrecord"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeepattern",
            name="payroll_average_hours",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5),
        ),
        migrations.AddField(
            model_name="employeepattern",
            name="restaurant_target_hours",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5),
        ),
        migrations.AddField(
            model_name="employeepattern",
            name="bar_target_hours",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5),
        ),
    ]
