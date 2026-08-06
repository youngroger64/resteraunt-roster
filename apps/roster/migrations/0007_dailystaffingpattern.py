from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("roster", "0006_employeepattern_department_targets"),
    ]

    operations = [
        migrations.CreateModel(
            name="DailyStaffingPattern",
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
                ("weekday", models.PositiveSmallIntegerField()),
                (
                    "department",
                    models.CharField(
                        choices=[
                            ("restaurant", "Restaurant"),
                            ("bar", "Bar"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "typical_headcount",
                    models.PositiveSmallIntegerField(default=0),
                ),
                (
                    "minimum_headcount",
                    models.PositiveSmallIntegerField(default=0),
                ),
                (
                    "band_counts",
                    models.JSONField(blank=True, default=dict),
                ),
                (
                    "weeks_seen",
                    models.PositiveSmallIntegerField(default=0),
                ),
                (
                    "confidence",
                    models.PositiveSmallIntegerField(default=0),
                ),
            ],
            options={"ordering": ["weekday", "department"]},
        ),
        migrations.AddConstraint(
            model_name="dailystaffingpattern",
            constraint=models.UniqueConstraint(
                fields=("weekday", "department"),
                name="unique_daily_staffing_pattern",
            ),
        ),
    ]
