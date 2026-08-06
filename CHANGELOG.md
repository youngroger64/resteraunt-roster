# Changelog

## 0.15.0
- Batch payroll import for CSV, Excel and Word `.docx`
- Word payroll reports parse week-ending date, ordinary and Sunday hours
- Payroll total hours override roster-derived weekly targets during Relearn
- Last ten payroll weeks form each employee's target
- High-hour regular employees receive first priority while under 50%, 75% and 90% of target
- Ranking uses percentage of target reached, not only absolute score
- Salary and blank-hour rows are retained as review issues but do not create false targets
- Duplicate employee/week payroll records update safely

## 0.14.0
- Weekly-hours balancing and target display
