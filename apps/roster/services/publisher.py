from django.db import transaction
from django.utils import timezone

from apps.roster.models import RosterStatus, RosterWeek


@transaction.atomic
def publish_roster(roster: RosterWeek, user) -> RosterWeek:
    # Area-only assignments such as KITCHEN are valid roster entries
    # and do not prevent publication.

    roster.status = RosterStatus.PUBLISHED
    roster.published_at = timezone.now()
    roster.published_by = user

    roster.save(
        update_fields=[
            "status",
            "published_at",
            "published_by",
            "updated_at",
        ]
    )

    return roster
