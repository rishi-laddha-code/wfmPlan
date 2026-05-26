# wfmplan

**Workforce Management Planning Library** — an analyst-friendly Python toolkit for staffing optimisation based on Erlang-C queueing theory.

Given a forecast of contact volumes and your operational targets (ASA or SLA), wfmplan calculates how many agents you need for every planning interval, builds a shift schedule, and exports everything as a polished Excel report — all in a few lines of Python.

---

## What's inside

| Module | What it does |
|---|---|
| `Optimizer` | Single-interval Erlang-C engine with sensitivity analysis |
| `BatchOptimizer` | Process a full forecast DataFrame or CSV file |
| `WeeklyScheduler` | Build a weekly shift roster from staffing requirements |
| `DailyScheduler` | Single-day shift scheduler (called by WeeklyScheduler) |
| `ShiftTemplate` | Define custom shift patterns |
| `ReportExporter` | Generate a polished multi-sheet Excel workbook |

---

## Installation

```sh
pip install wfmplan
```

Dependencies: `numpy`, `pandas`, `scipy`, `openpyxl`

---

## The maths (Erlang-C queueing theory)

wfmplan models your operation as an **M/M/c queue** — the standard queueing model for call centres, chat teams, and help desks.

```
Arrival process : Poisson  (random, memoryless arrivals)
Service times   : Exponential  (random service durations)
Servers         : c identical agents

Traffic intensity  ρ = (Volume / Interval) × AHT        [Erlangs]
Erlang-C  C(c,ρ)  = P(a contact must wait)
ASA  =  C(c,ρ) × AHT / (c – ρ)                          [seconds]
SLA  =  1 – C(c,ρ) × exp(–(c–ρ) × ST / AHT)             [fraction]
```

The optimiser finds the **minimum integer c** such that ASA ≤ target **or** SLA ≥ target, then applies occupancy caps and shrinkage on top.

---

## Quick start

### Single interval

```python
from wfmplan import Optimizer

opt = Optimizer(
    exp_vol=500,        # expected contacts in the interval
    aht=240,            # average handling time in seconds (4 min)
    interval=3600,      # interval length in seconds (1 hour)
    method='asa',       # optimise for Average Speed of Answer
    asa=20,             # target: answer within 20 seconds
    shrink=0.15,        # 15% shrinkage
    max_occupancy=0.85, # cap agents at 85% occupancy
)

print(opt.staffing_summary())
# Expected volume  : 500 contacts over 1.0h interval
# Avg handling time: 240s (4.0 min)
# Traffic intensity: 33.33 Erlangs
# Optimal staffing : 40 agents  (+shrinkage 15% -> 47 scheduled)
# Predicted ASA    : 14.2s
# Occupancy        : 83.3%
# P(waiting)       : 24.1%

result = opt.predict()   # returns a dict with all metrics
```

### What-if sensitivity table

```python
for row in opt.sensitivity_analysis(agent_range=4):
    print(row)
# {'agents': 36, 'is_optimal': False, 'occupancy_pct': 92.6, 'pred_asa_sec': 86.1, ...}
# {'agents': 40, 'is_optimal': True,  'occupancy_pct': 83.3, 'pred_asa_sec': 14.2, ...}
# ...
```

### Batch processing from a CSV

```python
from wfmplan import BatchOptimizer

bo = BatchOptimizer(
    'forecast.csv',   # path to CSV, or pass a DataFrame directly
    {
        'method': 'asa',
        'asa': 20,
        'shrink': 0.15,
        'max_occupancy': 0.85,
    }
)

results = bo.run_optimization()   # returns enriched DataFrame
bo.to_csv('staffing_output.csv')  # also saves to CSV

print(bo.daily_summary())
print(bo.weekly_summary())
```

### Build a weekly shift roster

```python
from wfmplan import WeeklyScheduler

scheduler = WeeklyScheduler(results)
roster = scheduler.build_roster()   # DataFrame: date, shift, agents_assigned, ...
print(scheduler.roster_summary())   # pivot: day × shift
```

### Custom shift templates

```python
from wfmplan import ShiftTemplate, WeeklyScheduler
from datetime import time

shifts = [
    ShiftTemplate('Morning',  time(7, 0),  8.5, break_mins=45),
    ShiftTemplate('Afternoon',time(13, 0), 8.5, break_mins=45),
    ShiftTemplate('Weekend',  time(9, 0),  6.0, break_mins=30, days_of_week=[5, 6]),
]

roster = WeeklyScheduler(results, shift_templates=shifts).build_roster()
```

### Generate the Excel report

```python
from wfmplan import ReportExporter

report = ReportExporter(
    results,
    roster=roster,
    sensitivity_data=opt.sensitivity_analysis(agent_range=5),
    title='Contact Centre — Week 23 Staffing Plan',
)
report.export('staffing_report.xlsx')
```

---

## SLA method

```python
opt = Optimizer(
    exp_vol=300, aht=180, interval=3600,
    method='sla',
    sla=0.80,   # 80% of calls answered...
    st=20,      # ...within 20 seconds
    shrink=0.12,
)
print(opt.predict())
```

---

## Input CSV format

See `input_template.csv` for a ready-to-use template. The file uses comma-separated values with these columns:

| Column | Required | Description |
|---|---|---|
| `exp_vol` | ✅ | Expected contact volume for the interval |
| `exp_aht` | ✅ | Average Handling Time in **seconds** |
| `interval_start` | ✅ | Start of interval — ISO datetime `YYYY-MM-DD HH:MM:SS` |
| `interval_end` | ✅ | End of interval — ISO datetime |
| `max_occupancy` | optional | Per-row occupancy cap (e.g. `0.85`) — overrides global target |
| `shrink` | optional | Per-row shrinkage (e.g. `0.15`) — overrides global target |
| `asa` | optional | Per-row ASA target in seconds |
| `sla` | optional | Per-row SLA fraction (e.g. `0.80`) |
| `st` | optional | Per-row service time threshold in seconds |
| `method` | optional | `asa` or `sla` per row |

---

## Output CSV columns

| Column | Description |
|---|---|
| *(all input columns)* | Passed through unchanged |
| `interval_secs` | Interval duration in seconds |
| `traffic_intensity` | Erlang value (ρ) |
| `agent_req` | Minimum agents to meet SLA/ASA target |
| `agent_req_shrink` | Scheduled headcount after shrinkage |
| `pred_occupancy` | Predicted occupancy (0–1) |
| `prob_waiting` | Erlang-C P(wait) — fraction of contacts that queue |
| `pred_asa` | Predicted Average Speed of Answer in seconds |
| `pred_sla_pct` | Predicted SLA percentage *(if `st` provided)* |

---

## Excel report sheets

| Sheet | Contents |
|---|---|
| **Summary** | KPI cards (peak agents, total volume, avg ASA, occupancy) + daily summary table |
| **Hourly Detail** | Full per-interval results with peak-demand highlighting |
| **Weekly Roster** | Day × shift wall-chart + detailed shift assignment table |
| **Sensitivity** | What-if table showing metrics at ±N agents from optimal |
| **How to Use** | Methodology notes, column definitions, formula reference |

---

## Typical analyst workflow

```
1. Fill in input_template.csv with your forecast data
2. Run BatchOptimizer to get per-interval staffing requirements
3. Run WeeklyScheduler to convert requirements into shift assignments
4. Run ReportExporter to produce the Excel report
5. Share staffing_report.xlsx with operations / HR
```

---

## Parameters reference

| Parameter | Type | Default | Description |
|---|---|---|---|
| `exp_vol` | float | — | Contact volume in the interval |
| `aht` | float | — | Average Handling Time (seconds) |
| `interval` | int | — | Interval length (seconds) |
| `method` | str | `'asa'` | `'asa'` or `'sla'` |
| `asa` | float | None | Target ASA in seconds *(method='asa')* |
| `sla` | float | None | Target SLA fraction 0–1 *(method='sla')* |
| `st` | float | None | Service time threshold in seconds *(method='sla')* |
| `shrink` | float | `0.0` | Shrinkage fraction in [0, 1) |
| `max_occupancy` | float | `1.0` | Max agent occupancy fraction (0, 1] |

---

## License

MIT — see LICENSE file.

---

*Interested in collaborating? This project is open source to empower WFM analysts everywhere. Contributions welcome.*
