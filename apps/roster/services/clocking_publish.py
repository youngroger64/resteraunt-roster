import json
import os
import urllib.request


CLOCKING_VALIDATE_URL = (
    "http://127.0.0.1:8000/api/v1/roster/validate/"
)


def build_clocking_roster_payload(roster):
    shifts = []
    roster_only = []

    for shift in (
        roster.shifts
        .select_related("employee")
        .order_by("date", "start_time", "employee__first_name")
    ):
        external_id = (shift.employee.external_id or "").strip()

        # Employees without a clocking external_id are deliberately
        # roster-only. They remain on the roster but are not sent
        # to the clocking/payroll application.
        if not external_id:
            roster_only.append({
                "employee": shift.employee.full_name,
                "employee_id": shift.employee_id,
                "date": shift.date.isoformat(),
                "start_time": shift.start_time.strftime("%H:%M"),
                "end_time": shift.end_time.strftime("%H:%M"),
            })
            continue

        shifts.append({
            "employee_number": external_id,
            "date": shift.date.isoformat(),
            "start_time": shift.start_time.strftime("%H:%M"),
            "end_time": shift.end_time.strftime("%H:%M"),
        })

    return {
        "week_start": roster.week_start.isoformat(),
        "version": roster.version,
        "shifts": shifts,
        "roster_only": roster_only,
    }


def validate_roster_with_clocking(roster):
    token = (
        os.environ.get("ROSTER_INTEGRATION_TOKEN") or ""
    ).strip()

    if not token:
        raise RuntimeError(
            "ROSTER_INTEGRATION_TOKEN is not configured."
        )

    payload = build_clocking_roster_payload(roster)

    request = urllib.request.Request(
        CLOCKING_VALIDATE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Integration-Token": token,
        },
        method="POST",
    )

    with urllib.request.urlopen(
        request,
        timeout=15,
    ) as response:
        result = json.load(response)

    return {
        "payload": payload,
        "result": result,
        "roster_only": payload.get("roster_only", []),
    }
