from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0002_employee_can_work_kitchen_employee_can_work_washup_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="exclude_from_clocking",
            field=models.BooleanField(
                default=False,
                help_text="Keep this employee on the roster, but do not send their shifts to the clocking/payroll application.",
                verbose_name="Exclude from clocking integration",
            ),
        ),
    ]
