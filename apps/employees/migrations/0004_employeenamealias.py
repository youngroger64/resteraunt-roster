from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0003_employee_exclude_from_clocking"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmployeeNameAlias",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("normalised_name", models.CharField(max_length=160, unique=True)),
                (
                    "employee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="name_aliases",
                        to="employees.employee",
                    ),
                ),
            ],
            options={
                "verbose_name": "employee name alias",
                "verbose_name_plural": "employee name aliases",
                "ordering": ["normalised_name"],
            },
        ),
    ]
