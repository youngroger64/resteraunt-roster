import json
import os
import re
import urllib.request
from collections import defaultdict

from django.db import transaction

from apps.employees.models import Employee


CLOCKING_EMPLOYEES_URL = (
    "http://127.0.0.1:8000/api/v1/employees/"
)

# Local/test employee IDs which should never become roster staff.
EXCLUDED_EXTERNAL_IDS = set()


def _normalise_name(value):
    value = (value or "").strip().lower()
    value = value.replace("’", "'")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _split_name(full_name):
    parts = (full_name or "").strip().split(maxsplit=1)

    if not parts:
        return "", ""

    if len(parts) == 1:
        return parts[0], ""

    return parts[0], parts[1]


def fetch_clocking_employees():
    token = (
        os.environ.get("ROSTER_INTEGRATION_TOKEN") or ""
    ).strip()

    if not token:
        raise RuntimeError(
            "ROSTER_INTEGRATION_TOKEN is not configured."
        )

    request = urllib.request.Request(
        CLOCKING_EMPLOYEES_URL,
        headers={
            "X-Integration-Token": token,
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=10,
    ) as response:
        payload = json.load(response)

    if not payload.get("ok"):
        raise RuntimeError(
            "Clocking employee API returned an error."
        )

    return payload.get("employees", [])


def sync_clocking_employees(dry_run=True):
    """
    Synchronise the roster employee directory with clocking.

    Clocking owns:
      - employee number
      - employee name
      - active/inactive status

    Roster continues to own:
      - department
      - scheduling eligibility
      - notes
      - roster history
      - learned scheduling information

    Conservative rules:
      - existing employees are matched ONLY by external_id
      - test IDs are skipped
      - duplicate names under multiple clocking IDs are skipped
      - missing employees are reported, not automatically created
      - roster-only employees are never deleted
    """

    clocking = fetch_clocking_employees()

    name_ids = defaultdict(list)

    for row in clocking:
        # Only active identities participate in duplicate-name protection.
        # Historical/inactive duplicate IDs must not block the live employee.
        if row.get("active"):
            name_ids[
                _normalise_name(row.get("name"))
            ].append(
                str(row.get("employee_number"))
            )

    result = {
        "matched": 0,
        "updated": 0,
        "unchanged": 0,
        "missing": [],
        "excluded": [],
        "duplicates": [],
    }

    with transaction.atomic():

        for row in clocking:
            external_id = str(
                row.get("employee_number") or ""
            ).strip()

            name = (
                row.get("name") or ""
            ).strip()

            active = bool(row.get("active"))

            if external_id in EXCLUDED_EXTERNAL_IDS:
                result["excluded"].append({
                    "external_id": external_id,
                    "name": name,
                })
                continue

            duplicate_ids = name_ids[
                _normalise_name(name)
            ]

            if len(duplicate_ids) > 1:
                result["duplicates"].append({
                    "external_id": external_id,
                    "name": name,
                    "ids": duplicate_ids,
                })
                continue

            employee = Employee.objects.filter(
                external_id=external_id
            ).first()

            if employee is None:
                result["missing"].append({
                    "external_id": external_id,
                    "name": name,
                    "active": active,
                })
                continue

            result["matched"] += 1

            first_name, last_name = _split_name(name)

            changed = (
                employee.first_name != first_name
                or employee.last_name != last_name
                or employee.is_active != active
            )

            if not changed:
                result["unchanged"] += 1
                continue

            result["updated"] += 1

            if not dry_run:
                employee.first_name = first_name
                employee.last_name = last_name
                employee.is_active = active

                employee.save(
                    update_fields=[
                        "first_name",
                        "last_name",
                        "is_active",
                        "updated_at",
                    ]
                )

        # Absolute safety: dry run can never persist anything.
        if dry_run:
            transaction.set_rollback(True)

    return result
