from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Employee",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("external_id", models.CharField(blank=True, db_index=True, max_length=64)),
                ("first_name", models.CharField(max_length=80)),
                ("last_name", models.CharField(blank=True, max_length=80)),
                ("department", models.CharField(choices=[("restaurant", "Restaurant"), ("bar", "Bar")], default="restaurant", max_length=20)),
                ("can_work_restaurant", models.BooleanField(default=True)),
                ("can_work_bar", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True)),
            ],
            options={"ordering": ["department", "first_name", "last_name"]},
        ),
        migrations.AddConstraint(
            model_name="employee",
            constraint=models.UniqueConstraint(
                condition=models.Q(("external_id", ""), _negated=True),
                fields=("external_id",),
                name="unique_nonblank_employee_external_id",
            ),
        ),
    ]
