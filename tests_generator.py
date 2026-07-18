import django.db.models.deletion
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("roster", "0002_rosterpurpose_employeepattern")]

    operations = [
        migrations.CreateModel(
            name="StaffingPattern",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("weekday", models.PositiveSmallIntegerField()),
                ("department", models.CharField(choices=[("restaurant","Restaurant"),("bar","Bar")], max_length=20)),
                ("shift_signature", models.CharField(max_length=120)),
                ("average_required", models.DecimalField(decimal_places=2, default=0, max_digits=4)),
                ("weeks_seen", models.PositiveSmallIntegerField(default=0)),
                ("confidence", models.PositiveSmallIntegerField(default=0)),
            ],
            options={"ordering":["weekday","department","shift_signature"]},
        ),
        migrations.AddConstraint(
            model_name="staffingpattern",
            constraint=models.UniqueConstraint(
                fields=("weekday","department","shift_signature"),
                name="unique_staffing_pattern",
            ),
        ),
        migrations.CreateModel(
            name="OpenShift",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("department", models.CharField(choices=[("restaurant","Restaurant"),("bar","Bar")], max_length=20)),
                ("date", models.DateField()),
                ("start_time", models.TimeField()),
                ("end_time", models.TimeField()),
                ("source_signature", models.CharField(blank=True, max_length=120)),
                ("confidence", models.PositiveSmallIntegerField(default=0)),
                ("notes", models.CharField(blank=True, max_length=250)),
                ("roster_week", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="open_shifts", to="roster.rosterweek")),
            ],
            options={"ordering":["date","department","start_time"]},
        ),
    ]
