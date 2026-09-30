import json
import urllib.error
import urllib.request

from django.conf import settings


class ClockingPublishError(Exception):
    """Clocking did not accept a roster. The local week must stay unpublished."""

    def __init__(self, message, warnings=None):
        super().__init__(message)
        self.warnings = list(warnings or [])


def _clocking_base_url():
    return str(getattr(settings, "CLOCKING_BASE_URL", "") or "").rstrip("/")


def _integration_token():
    return str(getattr(settings, "ROSTER_INTEGRATION_TOKEN", "") or "").strip()


def _require_token():
    token = _integration_token()
    if not token:
        raise ClockingPublishError(
            "ROSTER_INTEGRATION_TOKEN is not configured."
        )
    if not _clocking_base_url():
        raise ClockingPublishError(
            "CLOCKING_BASE_URL is not configured."
        )
    return token


def format_clocking_warning(warning):
    if not isinstance(warning, dict):
        return str(warning)
    message = warning.get("message") or warning.get("code") or "Clocking warning"
    detail = " ".join(
        part for part in (
            str(warning.get("employee_number") or "").strip(),
            str(warning.get("date") or "").strip(),
        )
        if part
    )
    if detail:
        return f"{message} ({detail})"
    return message


def build_clocking_roster_payload(roster):
    shifts = []
    roster_only = []

    for shift in (
        roster.shifts
        .select_related("employee")
        .order_by("date", "start_time", "employee__first_name")
    ):
        external_id = (shift.employee.external_id or "").strip()

        # Employees without a clocking external_id cannot be sent.
        # Validation still reports them. Week publish refuses them.
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
            "external_shift_id": str(shift.pk),
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


def _error_from_http(exc):
    try:
        payload = json.loads(exc.read().decode())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        payload = None

    if isinstance(payload, dict):
        if payload.get("error"):
            return str(payload["error"])
        errors = payload.get("errors") or []
        parts = []
        for error in errors:
            if not isinstance(error, dict):
                parts.append(str(error))
                continue
            parts.append(" ".join(
                part for part in (
                    str(error.get("code") or "").strip(),
                    str(error.get("employee_number") or "").strip(),
                    str(error.get("message") or "").strip(),
                )
                if part
            ))
        if parts:
            return "Clocking rejected the roster: " + "; ".join(parts)
    return f"Clocking rejected the roster (HTTP {getattr(exc, 'code', '')}).".strip()


def _post_json(path, payload):
    token = _require_token()
    request = urllib.request.Request(
        f"{_clocking_base_url()}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Integration-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        raise ClockingPublishError(_error_from_http(exc)) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ClockingPublishError(
            f"Could not reach the clocking application: {reason}"
        ) from exc

    if status < 200 or status >= 300:
        raise ClockingPublishError(
            f"Clocking rejected the roster (HTTP {status})."
        )

    try:
        result = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise ClockingPublishError(
            "Clocking returned an invalid response."
        ) from None

    if not isinstance(result, dict):
        raise ClockingPublishError(
            "Clocking returned an invalid response."
        )
    return result


def _ensure_accepted(result):
    warnings = result.get("warnings") or []
    if not isinstance(warnings, list):
        warnings = []
    if result.get("published") is not True or result.get("fully_applied") is not True:
        detail = result.get("error")
        if not detail and warnings:
            detail = "; ".join(format_clocking_warning(warning) for warning in warnings)
        raise ClockingPublishError(
            detail or "Clocking did not accept the roster.",
            warnings=warnings,
        )
    return result


def validate_roster_with_clocking(roster):
    payload = build_clocking_roster_payload(roster)
    result = _post_json("/api/v1/roster/validate/", payload)
    return {
        "payload": payload,
        "result": result,
        "roster_only": payload.get("roster_only", []),
    }


def publish_week_to_clocking(roster):
    """
    Send one complete week to clocking.

    Does not change the local roster. The caller marks it published only
    after this returns.
    """
    payload = build_clocking_roster_payload(roster)
    roster_only = payload.get("roster_only") or []
    if roster_only:
        names = sorted({row["employee"] for row in roster_only})
        raise ClockingPublishError(
            "Cannot publish. These employees have no clocking ID: "
            + ", ".join(names)
            + "."
        )

    result = _post_json(
        "/api/v1/roster/publish/",
        {
            "week_start": payload["week_start"],
            "version": payload["version"],
            "shifts": payload["shifts"],
        },
    )
    return _ensure_accepted(result)


def publish_employee_roster_to_clocking(roster, employee, removed_shift_ids=None):
    external_id = (employee.external_id or "").strip()

    if not external_id:
        raise ClockingPublishError(
            f"{employee.full_name} has no clocking ID, so this change cannot be sent."
        )

    removed_ids = {int(pk) for pk in (removed_shift_ids or [])}
    shifts = []
    for shift in (
        roster.shifts
        .filter(employee=employee)
        .order_by("date", "start_time")
    ):
        if shift.pk in removed_ids:
            continue
        shifts.append({
            "employee_number": external_id,
            "external_shift_id": str(shift.pk),
            "date": shift.date.isoformat(),
            "start_time": shift.start_time.strftime("%H:%M"),
            "end_time": shift.end_time.strftime("%H:%M"),
        })

    payload = {
        "week_start": roster.week_start.isoformat(),
        "employee_number": external_id,
        "shifts": shifts,
    }
    if removed_ids:
        payload["removed_external_shift_ids"] = [str(pk) for pk in removed_ids]

    result = _post_json(
        "/api/v1/roster/publish-employee/",
        payload,
    )
    return _ensure_accepted(result)
