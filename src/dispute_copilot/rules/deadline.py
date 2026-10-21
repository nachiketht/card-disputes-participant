"""Deadline arithmetic from a policy day count.

Report day is day zero: the deadline is the reported date plus the number of
calendar days printed in the policy. Weekends do not move it. This function
does not read the wall clock and does not use today's date.
"""

from datetime import date, timedelta


def add_calendar_days(reported: date, days: int) -> date:
    if days < 0:
        raise ValueError("a policy day count cannot be negative")
    return reported + timedelta(days=days)
