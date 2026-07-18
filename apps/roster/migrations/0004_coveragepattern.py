from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("roster", "0003_staffingpattern_openshift")]

    operations = [
        migrations.CreateModel(
            name="CoveragePattern",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("weekday", models.PositiveSmallIntegerField()),
                ("department", models.CharField(choices=[("restaurant","Restaurant"),("bar","Bar")], max_length=20)),
                ("slot_minute", models.PositiveSmallIntegerField()),
                ("average_required", models.DecimalField(decimal_places=2, default=0, max_digits=4)),
                ("weeks_seen", models.PositiveSmallIntegerField(default=0)),
                ("confidence", models.PositiveSmallIntegerField(default=0)),
            ],
            options={"ordering":["weekday","department","slot_minute"]},
        ),
        migrations.AddConstraint(
            model_name="coveragepattern",
            constraint=models.UniqueConstraint(
                fields=("weekday","department","slot_minute"),
                name="unique_coverage_pattern",
            ),
        ),
    ]
