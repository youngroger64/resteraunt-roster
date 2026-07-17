from django.db import models
from apps.core.models import TimeStampedModel

class Department(models.TextChoices):
    RESTAURANT = "restaurant", "Restaurant"
    BAR = "bar", "Bar"

class Employee(TimeStampedModel):
    external_id = models.CharField(max_length=64, blank=True, db_index=True)
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80, blank=True)
    department = models.CharField(max_length=20, choices=Department.choices, default=Department.RESTAURANT)
    can_work_restaurant = models.BooleanField(default=True)
    can_work_bar = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["department", "first_name", "last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["external_id"],
                condition=~models.Q(external_id=""),
                name="unique_nonblank_employee_external_id",
            )
        ]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self):
        return self.full_name
