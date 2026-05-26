"""
wfmplan  -  Workforce Management Planning Library
==================================================

Core components
---------------
Optimizer        Single-interval Erlang-C staffing calculator
BatchOptimizer   Process a full forecast DataFrame or CSV file
WeeklyScheduler  Build a weekly shift roster from staffing requirements
DailyScheduler   Single-day shift scheduler
ShiftTemplate    Define custom shift patterns
ReportExporter   Generate a polished multi-sheet Excel report

Quick start
-----------
>>> from wfmplan import Optimizer
>>> opt = Optimizer(exp_vol=500, aht=240, interval=3600, method='asa', asa=20, shrink=0.15)
>>> print(opt.staffing_summary())

>>> from wfmplan import BatchOptimizer, WeeklyScheduler, ReportExporter
>>> bo = BatchOptimizer('forecast.csv', {'method':'asa','asa':20,'shrink':0.15,'max_occupancy':0.85})
>>> results = bo.run_optimization()
>>> roster_df = WeeklyScheduler(results).build_roster()
>>> ReportExporter(results, roster=roster_df).export('my_report.xlsx')
"""

from .Optimizer import Optimizer
from .BatchOptimizer import BatchOptimizer
from .Scheduler import ShiftTemplate, DailyScheduler, WeeklyScheduler, DEFAULT_SHIFTS
from .ReportExporter import ReportExporter

__version__ = '2.0.0'
__author__ = 'Rishi Laddha'

__all__ = [
    'Optimizer',
    'BatchOptimizer',
    'ShiftTemplate',
    'DailyScheduler',
    'WeeklyScheduler',
    'DEFAULT_SHIFTS',
    'ReportExporter',
]
