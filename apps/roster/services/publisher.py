from django.db import transaction
from django.utils import timezone
from apps.roster.models import RosterStatus, RosterWeek

@transaction.atomic
def publish_roster(roster: RosterWeek, user) -> RosterWeek:
    # Different weeks may all remain published so staff can see upcoming rosters.
    # Superseding belongs to versioning of the same week, not to publishing next week.
    roster.status = RosterStatus.PUBLISHED
    roster.published_at = timezone.now()
    roster.published_by = user
    roster.save(update_fields=["status", "published_at", "published_by", "updated_at"])
    return roster
