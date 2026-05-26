"""
BatchOptimizer.py  -  Run Erlang-C optimization across many intervals at once.

Accepts a pandas DataFrame or a CSV file path, applies Optimizer per row,
and returns enriched results.  Also provides CSV export helpers.
"""

import warnings
from math import ceil
from pathlib import Path

import pandas as pd

from .Optimizer import Optimizer


# Required columns in the input DataFrame / CSV
REQUIRED_COLS = {'exp_vol', 'exp_aht', 'interval_start', 'interval_end'}

# Optional columns that override operational_targets per row
OPTIONAL_OVERRIDE_COLS = {'max_occupancy', 'shrink', 'asa', 'sla', 'st', 'method'}


class BatchOptimizer:
    """
    Run Erlang-C staffing optimization for every row in a DataFrame or CSV.

    Parameters
    ----------
    data                : pd.DataFrame or str/Path - input data or CSV file path
    operational_targets : dict - default targets applied to ALL rows unless
                          overridden by per-row columns.
                          Required keys depend on method:
                            method='asa' -> asa (seconds)
                            method='sla' -> sla (fraction), st (seconds)
                          Optional: max_occupancy (default 1.0), shrink (default 0.0)

    Example
    -------
    >>> bo = BatchOptimizer('forecast.csv', {'method': 'asa', 'asa': 20, 'shrink': 0.15})
    >>> results = bo.run_optimization()
    >>> bo.to_csv('staffing_output.csv')
    """

    def __init__(self, data, operational_targets: dict):
        if isinstance(data, (str, Path)):
            self.df = pd.read_csv(data, parse_dates=['interval_start', 'interval_end'])
        elif isinstance(data, pd.DataFrame):
            self.df = data.copy()
        else:
            raise TypeError("data must be a DataFrame or a path to a CSV file")

        self._validate_dataframe(self.df)
        self.operational_targets = operational_targets
        self._results_df: pd.DataFrame = None

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_dataframe(self, df: pd.DataFrame):
        missing = REQUIRED_COLS - set(df.columns)
        if missing:
            raise ValueError(
                f"Input data is missing required column(s): {missing}\n"
                f"Required columns: {REQUIRED_COLS}"
            )
        if df.empty:
            raise ValueError("Input DataFrame is empty")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _interval_seconds(start, end) -> int:
        return int((end - start).total_seconds())

    def _row_targets(self, row) -> dict:
        """Merge global targets with any per-row overrides."""
        targets = dict(self.operational_targets)
        for col in OPTIONAL_OVERRIDE_COLS:
            if col in row.index and pd.notna(row[col]):
                targets[col] = row[col]
        return targets

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run_optimization(self) -> pd.DataFrame:
        """
        Process every row and return an enriched DataFrame.

        New columns added to the input columns:
            interval_secs, traffic_intensity,
            agent_req, agent_req_shrink,
            pred_occupancy, prob_waiting, pred_asa,
            pred_sla_pct  (if st provided)
        """
        records = []
        errors = []

        for idx, row in self.df.iterrows():
            try:
                interval = self._interval_seconds(row['interval_start'], row['interval_end'])
                targets = self._row_targets(row)

                opt = Optimizer(
                    exp_vol=float(row['exp_vol']),
                    aht=float(row['exp_aht']),
                    interval=interval,
                    **targets,
                )
                result = opt.predict()

                record = row.to_dict()
                record['interval_secs'] = interval
                record.update(result)
                records.append(record)

            except Exception as e:
                errors.append(f"Row {idx}: {e}")
                record = row.to_dict()
                record['error'] = str(e)
                records.append(record)

        if errors:
            warnings.warn(f"{len(errors)} row(s) failed:\n" + "\n".join(errors))

        results_df = pd.DataFrame(records)

        # Reorder columns: inputs first, then outputs
        input_cols = list(self.df.columns)
        output_cols = [c for c in results_df.columns if c not in input_cols]
        self._results_df = results_df[input_cols + output_cols]
        return self._results_df

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------

    def to_csv(self, path: str = 'staffing_output.csv') -> str:
        """Save results to CSV.  Runs optimization first if not yet done."""
        if self._results_df is None:
            self.run_optimization()
        self._results_df.to_csv(path, index=False)
        return path

    def daily_summary(self) -> pd.DataFrame:
        """
        Aggregate results to daily totals / averages.

        Returns a DataFrame indexed by date with columns:
            total_volume, avg_aht, peak_agents, total_agent_hours,
            avg_occupancy_pct, avg_asa
        """
        if self._results_df is None:
            self.run_optimization()

        df = self._results_df.copy()
        df['date'] = pd.to_datetime(df['interval_start']).dt.date
        df['agent_hours'] = df['agent_req_shrink'] * df['interval_secs'] / 3600

        summary = df.groupby('date').agg(
            total_volume=('exp_vol', 'sum'),
            avg_aht=('exp_aht', 'mean'),
            peak_agents=('agent_req_shrink', 'max'),
            total_agent_hours=('agent_hours', 'sum'),
            avg_occupancy_pct=('pred_occupancy', lambda x: round(x.mean() * 100, 1)),
            avg_asa=('pred_asa', 'mean'),
        ).reset_index()

        summary['avg_aht'] = summary['avg_aht'].round(0)
        summary['avg_asa'] = summary['avg_asa'].round(1)
        summary['total_agent_hours'] = summary['total_agent_hours'].round(1)
        return summary

    def weekly_summary(self) -> pd.DataFrame:
        """Aggregate to week-level (Monday-based ISO weeks)."""
        if self._results_df is None:
            self.run_optimization()

        df = self._results_df.copy()
        df['week_start'] = pd.to_datetime(df['interval_start']).dt.to_period('W').dt.start_time
        df['agent_hours'] = df['agent_req_shrink'] * df['interval_secs'] / 3600

        summary = df.groupby('week_start').agg(
            total_volume=('exp_vol', 'sum'),
            avg_aht=('exp_aht', 'mean'),
            peak_agents=('agent_req_shrink', 'max'),
            total_agent_hours=('agent_hours', 'sum'),
            avg_occupancy_pct=('pred_occupancy', lambda x: round(x.mean() * 100, 1)),
            avg_asa=('pred_asa', 'mean'),
        ).reset_index()

        summary['avg_aht'] = summary['avg_aht'].round(0)
        summary['avg_asa'] = summary['avg_asa'].round(1)
        summary['total_agent_hours'] = summary['total_agent_hours'].round(1)
        return summary
