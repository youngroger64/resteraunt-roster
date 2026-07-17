# Restaurant Roster

A standalone Django application for building restaurant and bar rosters quickly.

## Current features

- Manager login
- Dashboard
- Employee management
- Restaurant and Bar departments
- Draft/published roster weeks
- Spreadsheet-style weekly roster editor
- Create a new week by copying the latest roster
- Excel employee import
- Basic Excel roster import
- Publish and supersede roster versions
- Printable weekly roster
- Service-layer placeholders for learning, confidence and replacements
- Django admin and audit-friendly timestamps

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_demo
python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

## Deployment beside the clocking app

Keep this project in a separate folder and use a separate virtual environment,
Gunicorn socket/port and systemd service. Restarting this service will not restart
the clocking application.

See `docs/deployment.md`.

## Important safety boundary

This project currently uses its own database. It does not write to the live
clocking application's payroll, attendance or clock-event tables.
A controlled publisher/integration can be added later.
