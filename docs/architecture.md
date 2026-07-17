# Architecture

This is a separate Django project and should run as its own service.

- `employees`: local roster-facing employee records
- `roster`: weeks, shifts and manager workflow
- `imports_app`: spreadsheet ingestion
- `dashboard`: manager landing page
- `core`: shared base models and utilities

Business logic belongs in `apps/roster/services/`, not in templates.
The future clocking-system integration should be implemented as a narrow,
audited publishing adapter.
