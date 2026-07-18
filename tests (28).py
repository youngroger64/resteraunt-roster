from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("employees", "0001_initial"), ("roster", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="rosterweek",
            name="purpose",
            field=models.CharField(
                choices=[("base","Base roster"),("historic","Historic roster"),("weekly","Weekly roster")],
                default="weekly", max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="EmployeePattern",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("weeks_seen", models.PositiveSmallIntegerField(default=0)),
                ("normal_department", models.CharField(blank=True, choices=[("restaurant","Restaurant"),("bar","Bar")], max_length=20)),
                ("average_weekly_hours", models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ("average_days_worked", models.DecimalField(decimal_places=2, default=0, max_digits=4)),
                ("consistency", models.PositiveSmallIntegerField(default=0)),
                ("day_probabilities", models.JSONField(blank=True, default=dict)),
                ("typical_shifts", models.JSONField(blank=True, default=dict)),
                ("employee", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="learned_pattern", to="employees.employee")),
            ],
            options={"ordering":["employee__first_name","employee__last_name"]},
        ),
    ]
