# Changelog

## 0.13.0
- Learns simultaneous staffing demand in 30-minute coverage slots
- Uses median historic coverage to reduce one-off noise
- Similar overlapping shifts no longer create separate staffing needs
- Generator skips shifts when current planned coverage already meets demand
- Open shifts count toward planned coverage to prevent duplicate false gaps
- Regression test covers the unnecessary Friday 10:00-17:00 case
