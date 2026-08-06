# Changelog


## 0.17.0
- Automatic Bar assignments require Bar permission plus Bar history or a Bar home department
- Automatic Restaurant assignments require Restaurant permission plus Restaurant history or home department
- Payroll average is stored separately from roster-area targets
- Restaurant and Bar receive separate learned target hours
- Kitchen and other duties no longer inflate Restaurant targets
- Roster displays area target and total payroll average separately
- Added department-boundary tests



## 0.16.0
- Payroll history import now accepts text-based PDF files
- PDF, Word, Excel and CSV files can be batch-uploaded together
- Accountant-style PDF lines are parsed into employee hours
- Salary and blank-hour rows remain excluded from hourly targets
- Image-only/scanned PDFs show a clear error instead of importing bad data
- Added PDF payroll importer regression tests


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
