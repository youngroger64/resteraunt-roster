import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("employees", "0001_initial"),
    ]
    operations = [
        migrations.CreateModel(
            name="RosterWeek",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("week_start", models.DateField(unique=True)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("published", "Published"), ("superseded", "Superseded")], default="draft", max_length=20)),
                ("version", models.PositiveIntegerField(default=1)),
                ("notes", models.TextField(blank=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("published_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="published_rosters", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-week_start"]},
        ),
        migrations.CreateModel(
            name="Shift",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("department", models.CharField(choices=[("restaurant", "Restaurant"), ("bar", "Bar")], max_length=20)),
                ("date", models.DateField()),
                ("start_time", models.TimeField()),
                ("end_time", models.TimeField()),
                ("segment", models.PositiveSmallIntegerField(default=1)),
                ("source", models.CharField(default="manual", max_length=20)),
                ("confidence", models.PositiveSmallIntegerField(default=100)),
                ("notes", models.CharField(blank=True, max_length=250)),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="roster_shifts", to="employees.employee")),
                ("roster_week", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shifts", to="roster.rosterweek")),
            ],
            options={"ordering": ["date", "department", "start_time", "employee__first_name"]},
        ),
        migrations.AddConstraint(
            model_name="shift",
            constraint=models.UniqueConstraint(fields=("roster_week", "employee", "date", "segment"), name="unique_shift_segment_per_employee_day"),
        ),
    ]
