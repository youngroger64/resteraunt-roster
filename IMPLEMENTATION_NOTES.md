# Staff roster workflow implementation

This branch adds the first integrated employee scheduling workflow to the roster sandbox.

## Added

- Manager-approved **Normal week** rules (target hours, target days, preferred area).
- Dated employee availability exceptions; these are hard constraints for candidate selection.
- Employee-facing `/rosters/staff/` page modelled on the clocking app layout.
- Upcoming published roster view (five-week horizon).
- Shift confirmation and **Can't work** response.
- Manager replacement suggestions for Can't work requests; shifts remain assigned until approved.
- Open-shift requests with manager approval/decline.
- Session-only clock UI preview for testing the combined Clock / Roster experience. It deliberately writes no attendance/payroll records.
- Multiple weeks can remain published so upcoming rosters remain visible.

## Deploy to the roster test VM

```bash
cd <roster-project>
source <venv>/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py test apps.roster
python manage.py collectstatic --noinput  # production only
```

Restart the existing gunicorn/systemd service after migration.

## Test staff access

Employees identify with `Employee.external_id` on `/rosters/staff/` in this sandbox. This is intentionally a temporary bridge to the employee number used by the clocking app. Do not treat it as production authentication; use the clocking app's stronger employee identity/PIN flow when the projects are integrated.

## Recommended next integration step

Do not duplicate attendance models into this project. Move or expose the live clocking service behind the staff page so the Clock tab calls the real clock service, while roster data remains owned by the roster module. Employee identity should be shared by a stable external employee ID.

## Simplification pass (manager + staff UX)

- Routine generated gaps now auto-assign the highest-ranked eligible employee. Normal target hours/days affect ranking but no longer force a manager choice. An open shift remains only when no safe eligible/available employee exists.
- Manager-approved `preferred_department` is now authoritative for automatic generation. Cross-trained employees can still be moved manually.
- Learning now corrects a stale employee primary department when at least 70% of historic shift segments consistently belong to another area. Re-run **Learning** after deploying this build to refresh cases such as regular Bar staff imported earlier as Restaurant.
- The manager roster header is reduced to **Regenerate**, **Publish roster**, and **More**. Print/Excel/Delete are secondary. “Needs a choice” is now “Needs attention” and should only appear for genuinely unfillable shifts.
- The staff sandbox keeps Clock as the primary screen and adds one large **View my roster** button. Shift confirmation was removed; staff only act when something is wrong. Availability is tucked inside the roster area as **Tell us when you can't work**, with time fields shown only for partial-day availability.
- Roster target display now uses manager-approved target hours when present and labels payroll history explicitly as historic context.
