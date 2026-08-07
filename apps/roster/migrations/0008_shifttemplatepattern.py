from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("roster", "0007_dailystaffingpattern"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShiftTemplatePattern",
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
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "weekday",
                    models.PositiveSmallIntegerField(),
                ),
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
                    "shift_signature",
                    models.CharField(max_length=120),
                ),
                (
                    "typical_count",
                    models.PositiveSmallIntegerField(default=1),
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
            options={
                "ordering": [
                    "weekday",
                    "department",
                    "-confidence",
                    "shift_signature",
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="shifttemplatepattern",
            constraint=models.UniqueConstraint(
                fields=(
                    "weekday",
                    "department",
                    "shift_signature",
                ),
                name="unique_shift_template_pattern",
            ),
        ),
    ]
