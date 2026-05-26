"""
Scheduler.py  -  Shift scheduling engine for wfmplan.

Given per-interval staffing requirements (from BatchOptimizer), this module
assigns agents to shifts that cover demand while minimising total headcount.

Approach
--------
We use a greedy cover algorithm (industry-standard for operational WFM):

1. Define a set of available shift templates (start time + duration).
2. For each shift template, count how many intervals it covers.
3. Sort intervals by their required agent count descending (hardest to cover first).
4. Greedily assign agents to the shift that gives the best marginal coverage
   until every interval requirement is met.

This does NOT solve the full integer programming optimisation (which is NP-hard
for large instances) but produces good practical schedules fast, matching the
approach used by commercial WFM tools for initial draft rosters.

For multi-day inputs the scheduler runs independently per day then
assembles a weekly roster.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time
from math import ceil
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Shift template
# ---------------------------------------------------------------------------

@dataclass
class ShiftTemplate:
    """
    Describes one type of working shift.

    Parameters
    ----------
    name          : str            - e.g. 'Early', 'Mid', 'Late', 'Overnight'
    start_time    : datetime.time  - shift start (local time)
    duration_hrs  : float          - shift length in hours (e.g. 8.0, 8.5)
    break_mins    : float          - total unpaid break in minutes; reduces
                                     productive hours (default 30)
    days_of_week  : list[int]      - 0=Mon ... 6=Sun; None means every day
    """
    name: str
    start_time: time
    duration_hrs: float
    break_mins: float = 30.0
    days_of_week: Optional[list] = None  # None = all days

    @property
    def productive_hrs(self) -> float:
        return self.duration_hrs - self.break_mins / 60

    def covers(self, interval_start: datetime, interval_end: datetime) -> bool:
        """Return True if this shift is active for the given interval."""
        date = interval_start.date()

        if self.days_of_week is not None and date.weekday() not in self.days_of_week:
            return False

        shift_start = datetime.combine(date, self.start_time)
        shift_end = shift_start + timedelta(hours=self.duration_hrs)

        # Handle overnight shifts
        if shift_end.date() > date:
            # Interval must overlap [shift_start, shift_end)
            return interval_start < shift_end and interval_end > shift_start

        return interval_start >= shift_start and interval_end <= shift_end


# Default shift library – analysts can replace or extend this
DEFAULT_SHIFTS = [
    ShiftTemplate('Early',     time(6, 0),  8.0, break_mins=30),
    ShiftTemplate('Morning',   time(8, 0),  8.0, break_mins=30),
    ShiftTemplate('Mid',       time(10, 0), 8.0, break_mins=30),
    ShiftTemplate('Afternoon', time(12, 0), 8.0, break_mins=30),
    ShiftTemplate('Late',      time(14, 0), 8.0, break_mins=30),
    ShiftTemplate('Evening',   time(16, 0), 8.0, break_mins=30),
    ShiftTemplate('Night',     time(22, 0), 8.0, break_mins=30),
]


# ---------------------------------------------------------------------------
# Daily scheduler
# ---------------------------------------------------------------------------

class DailyScheduler:
    """
    Produce a shift schedule for a single day.

    Parameters
    ----------
    requirements   : pd.DataFrame   - must have columns:
                       interval_start (datetime), interval_end (datetime),
                       agent_req_shrink (int)
    shift_templates: list[ShiftTemplate] - available shifts (default: DEFAULT_SHIFTS)
    max_iterations : int             - safety cap on greedy loop (default 10 000)
    """

    def __init__(
        self,
        requirements: pd.DataFrame,
        shift_templates: list = None,
        max_iterations: int = 10_000,
    ):
        self.req = requirements.copy().reset_index(drop=True)
        self.req['interval_start'] = pd.to_datetime(self.req['interval_start'])
        self.req['interval_end'] = pd.to_datetime(self.req['interval_end'])
        self.shifts = shift_templates or DEFAULT_SHIFTS
        self.max_iterations = max_iterations

    def _build_coverage_matrix(self) -> dict:
        """For each shift, precompute which intervals it covers -> {shift_name: [bool]}"""
        coverage = {}
        for s in self.shifts:
            flags = [
                s.covers(row['interval_start'], row['interval_end'])
                for _, row in self.req.iterrows()
            ]
            if any(flags):
                coverage[s.name] = flags
        return coverage

    def schedule(self) -> pd.DataFrame:
        """
        Run greedy covering algorithm.

        Returns a DataFrame with columns:
            shift_name, start_time, end_time, agents_assigned
        """
        # Remaining demand per interval
        remaining = list(self.req['agent_req_shrink'].astype(int))
        coverage = self._build_coverage_matrix()

        assigned = {s.name: 0 for s in self.shifts}
        iterations = 0

        while any(r > 0 for r in remaining) and iterations < self.max_iterations:
            iterations += 1

            # Pick shift that covers the interval with most unmet demand
            best_shift = None
            best_score = -1

            for s_name, flags in coverage.items():
                score = sum(remaining[i] for i, f in enumerate(flags) if f and remaining[i] > 0)
                if score > best_score:
                    best_score = score
                    best_shift = s_name

            if best_shift is None or best_score <= 0:
                break

            # Assign one agent to the best shift
            assigned[best_shift] += 1
            flags = coverage[best_shift]
            for i, f in enumerate(flags):
                if f:
                    remaining[i] = max(0, remaining[i] - 1)

        if any(r > 0 for r in remaining):
            warnings.warn(
                f"Could not fully cover demand for all intervals. "
                f"Max uncovered: {max(remaining)} agent(s). "
                "Consider adding more shift templates or extending operating hours.",
                UserWarning,
            )

        # Build result DataFrame
        rows = []
        date = self.req['interval_start'].iloc[0].date()
        for s in self.shifts:
            if assigned.get(s.name, 0) > 0:
                start_dt = datetime.combine(date, s.start_time)
                end_dt = start_dt + timedelta(hours=s.duration_hrs)
                rows.append({
                    'shift_name': s.name,
                    'shift_start': start_dt,
                    'shift_end': end_dt,
                    'duration_hrs': s.duration_hrs,
                    'productive_hrs': round(s.productive_hrs, 2),
                    'agents_assigned': assigned[s.name],
                    'total_hours': round(assigned[s.name] * s.productive_hrs, 1),
                })

        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Weekly roster builder
# ---------------------------------------------------------------------------

class WeeklyScheduler:
    """
    Build a full weekly roster from per-interval staffing requirements.

    Parameters
    ----------
    requirements    : pd.DataFrame   - output from BatchOptimizer.run_optimization()
                      needs: interval_start, interval_end, agent_req_shrink
    shift_templates : list[ShiftTemplate]  - optional custom shifts
    """

    def __init__(self, requirements: pd.DataFrame, shift_templates: list = None):
        self.req = requirements.copy()
        self.req['interval_start'] = pd.to_datetime(self.req['interval_start'])
        self.req['interval_end'] = pd.to_datetime(self.req['interval_end'])
        self.shifts = shift_templates or DEFAULT_SHIFTS

    def build_roster(self) -> pd.DataFrame:
        """
        Run DailyScheduler for each day and return a combined weekly roster.

        Returns a DataFrame with columns:
            date, day_of_week, shift_name, shift_start, shift_end,
            duration_hrs, productive_hrs, agents_assigned, total_hours
        """
        self.req['date'] = self.req['interval_start'].dt.date
        all_rows = []

        for date, day_df in self.req.groupby('date'):
            scheduler = DailyScheduler(day_df, self.shifts)
            day_schedule = scheduler.schedule()
            if not day_schedule.empty:
                day_schedule.insert(0, 'date', date)
                day_schedule.insert(1, 'day_of_week', datetime.combine(date, time()).strftime('%A'))
                all_rows.append(day_schedule)

        if not all_rows:
            return pd.DataFrame()

        roster = pd.concat(all_rows, ignore_index=True)
        return roster

    def roster_summary(self) -> pd.DataFrame:
        """
        Pivot the roster to a day x shift matrix showing agents_assigned,
        useful for a wall-chart view.
        """
        roster = self.build_roster()
        if roster.empty:
            return pd.DataFrame()

        pivot = roster.pivot_table(
            index=['date', 'day_of_week'],
            columns='shift_name',
            values='agents_assigned',
            fill_value=0,
        ).reset_index()
        pivot.columns.name = None
        return pivot
