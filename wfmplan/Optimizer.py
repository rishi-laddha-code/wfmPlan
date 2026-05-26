"""
Optimizer.py  -  Core Erlang-C queueing engine for wfmplan.

Theory
------
Erlang-C (M/M/c queue) models a call centre as a Poisson arrival process
feeding into c identical servers each with exponential service times.

Key quantities
  rho  = (lambda / mu) = traffic intensity (Erlangs)
  C(c,rho) = P(waiting) - Erlang-C formula
  ASA = C(c,rho) * AHT / (c - rho)           Average Speed of Answer
  SLA = 1 - C(c,rho)*exp(-(c-rho)*ST/AHT)    % calls answered within service target ST

All times are in seconds throughout the library.
"""

import warnings
from math import ceil, exp, log
from scipy.special import loggamma


class Optimizer:
    """
    Single-interval Erlang-C staffing optimizer.

    Parameters
    ----------
    exp_vol       : float  - expected call / contact volume in the interval
    aht           : float  - average handling time in seconds
    interval      : int    - interval length in seconds (e.g. 3600 for 1 hour)
    max_occupancy : float  - ceiling on agent occupancy, default 1.0 (no cap)
    shrink        : float  - shrinkage factor in [0, 1), default 0.0
    method        : str    - 'asa' (default) or 'sla'
    asa           : float  - target Average Speed of Answer in seconds  (method='asa')
    sla           : float  - target SLA as a fraction, e.g. 0.80            (method='sla')
    st            : float  - service time target in seconds, e.g. 20        (method='sla')
    """

    def __init__(
        self,
        exp_vol: float,
        aht: float,
        interval: int,
        max_occupancy: float = 1.0,
        shrink: float = 0.0,
        asa: float = None,
        sla: float = None,
        st: float = None,
        method: str = 'asa',
        **kwargs,
    ):
        self.validate_inputs(exp_vol, aht, interval, max_occupancy, shrink, asa, sla, st, method)
        if kwargs:
            warnings.warn(f"Unexpected keyword arguments ignored: {list(kwargs.keys())}", UserWarning)

        self.exp_vol = exp_vol
        self.aht = aht
        self.asa = asa
        self.sla = sla
        self.st = st
        self.interval = interval
        self.shrink = shrink
        self.max_occupancy = max_occupancy
        self.method = method
        self.intensity = None  # computed in predict()

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def validate_inputs(self, exp_vol, aht, interval, max_occupancy, shrink, asa, sla, st, method):
        if method not in ('asa', 'sla'):
            raise ValueError("method must be 'asa' or 'sla'")
        if method == 'asa' and asa is None:
            raise ValueError("Provide 'asa' target (seconds) when method='asa'")
        if method == 'sla' and (sla is None or st is None):
            raise ValueError("Provide both 'sla' (fraction) and 'st' (seconds) when method='sla'")
        if exp_vol <= 0:
            raise ValueError("exp_vol must be > 0")
        if aht <= 0:
            raise ValueError("aht must be > 0")
        if asa is not None and asa <= 0:
            raise ValueError("asa must be > 0")
        if sla is not None and not (0 < sla <= 1):
            raise ValueError("sla must be in (0, 1]")
        if st is not None and st <= 0:
            raise ValueError("st must be > 0")
        if interval <= 0:
            raise ValueError("interval must be > 0")
        if not 0 <= shrink < 1:
            raise ValueError("shrink must be in [0, 1)")
        if not 0 < max_occupancy <= 1:
            raise ValueError("max_occupancy must be in (0, 1]")

    # ------------------------------------------------------------------
    # Core Erlang-C math (public so analysts can call directly)
    # ------------------------------------------------------------------

    def erlang_c(self, traffic_intensity: float, num_agents: int) -> float:
        """
        Erlang-C formula  C(c, rho) = P(call must wait).

        Parameters
        ----------
        traffic_intensity : float - rho = lambda/mu  (Erlangs)
        num_agents        : int   - c  (must be > rho for a stable queue)

        Returns
        -------
        float in [0, 1] - probability that an arriving call must wait
        """
        if num_agents <= traffic_intensity:
            return 1.0
        try:
            log_num = num_agents * log(traffic_intensity) - loggamma(num_agents + 1) + log(num_agents)
            log_den = log(num_agents - traffic_intensity)
            x = exp(log_num - log_den)
            y = sum(exp(i * log(traffic_intensity) - loggamma(i + 1)) for i in range(round(num_agents) + 1))
            return x / (y + x)
        except OverflowError:
            return 1.0

    def calc_asa(self, pw: float, num_agents: int) -> float:
        """Average Speed of Answer (seconds) given P(waiting) and agent count."""
        try:
            return (pw * self.aht) / (num_agents - self.intensity)
        except ZeroDivisionError:
            return float('inf')

    def calc_sla(self, pw: float, num_agents: int) -> float:
        """
        Fraction of calls answered within service target ST.
        SLA = 1 - C(c,rho) * exp(-(c-rho)*ST/AHT)
        """
        if not self.st:
            return None
        try:
            return 1.0 - pw * exp(-((num_agents - self.intensity) * (self.st / self.aht)))
        except OverflowError:
            return 0.0

    # ------------------------------------------------------------------
    # Minimum agents search
    # ------------------------------------------------------------------

    def _find_min_agents(self) -> int:
        n = max(ceil(self.intensity) + 1, 1)
        if n <= self.intensity:
            n = int(self.intensity) + 1

        pw = self.erlang_c(self.intensity, n)

        if self.method == 'sla':
            while self.calc_sla(pw, n) < self.sla:
                n += 1
                pw = self.erlang_c(self.intensity, n)
        else:
            while self.calc_asa(pw, n) > self.asa:
                n += 1
                pw = self.erlang_c(self.intensity, n)

        return n

    # ------------------------------------------------------------------
    # Primary output
    # ------------------------------------------------------------------

    def predict(self) -> dict:
        """
        Compute the optimal staffing requirement and key service metrics.

        Returns
        -------
        dict with keys:
            agent_req          - raw agents needed to hit SLA/ASA target
            agent_req_shrink   - agents needed after accounting for shrinkage
            traffic_intensity  - Erlangs (rho)
            pred_occupancy     - agent occupancy as a fraction
            prob_waiting       - Erlang-C P(wait)
            pred_asa           - predicted ASA in seconds  (always returned)
            pred_sla_pct       - predicted SLA as %        (when st is provided)
        """
        try:
            self.intensity = (self.exp_vol / self.interval) * self.aht
            n = self._find_min_agents()

            # Apply max-occupancy cap
            if (self.intensity / n) > self.max_occupancy:
                n = ceil(self.intensity / self.max_occupancy)

            pw = self.erlang_c(self.intensity, n)
            asa_val = self.calc_asa(pw, n)
            sla_val = self.calc_sla(pw, n)
            occupancy = self.intensity / n
            n_shrink = ceil(n / (1 - self.shrink))

            result = {
                'agent_req': int(n),
                'agent_req_shrink': int(n_shrink),
                'traffic_intensity': round(self.intensity, 3),
                'pred_occupancy': round(occupancy, 4),
                'prob_waiting': round(pw, 4),
                'pred_asa': round(asa_val, 1),
            }
            if sla_val is not None:
                result['pred_sla_pct'] = round(sla_val * 100, 2)

            return result

        except Exception as e:
            intensity = getattr(self, 'intensity', None)
            warnings.warn(f"predict() failed - exp_vol={self.exp_vol}, intensity={intensity}, error={e}")
            return {}

    # ------------------------------------------------------------------
    # Sensitivity analysis
    # ------------------------------------------------------------------

    def sensitivity_analysis(self, agent_range: int = 5) -> list:
        """
        Show how key metrics change as agent count varies +/-agent_range
        around the optimal staffing level.

        Returns a list of dicts, one per agent count, sorted ascending.
        Useful for what-if planning tables.

        Parameters
        ----------
        agent_range : int - how many steps above and below optimal to include
        """
        base = self.predict()
        if not base:
            return []

        opt_n = base['agent_req']
        self.intensity = (self.exp_vol / self.interval) * self.aht
        rows = []

        for n in range(max(int(self.intensity) + 1, opt_n - agent_range), opt_n + agent_range + 1):
            pw = self.erlang_c(self.intensity, n)
            asa_v = self.calc_asa(pw, n)
            occupancy = self.intensity / n
            row = {
                'agents': n,
                'is_optimal': (n == opt_n),
                'occupancy_pct': round(occupancy * 100, 1),
                'prob_waiting_pct': round(pw * 100, 1),
                'pred_asa_sec': round(asa_v, 1),
                'agent_req_shrink': ceil(n / (1 - self.shrink)),
            }
            if self.st:
                row['pred_sla_pct'] = round(self.calc_sla(pw, n) * 100, 2)
            rows.append(row)

        return rows

    # ------------------------------------------------------------------
    # Human-readable summary
    # ------------------------------------------------------------------

    def staffing_summary(self) -> str:
        """
        Return a plain-English summary paragraph suitable for a report or
        dashboard.  Calls predict() internally.
        """
        r = self.predict()
        if not r:
            return "Staffing calculation failed - check input values."

        lines = [
            f"Expected volume  : {self.exp_vol:,.0f} contacts over {self.interval/3600:.1f}h interval",
            f"Avg handling time: {self.aht:.0f}s ({self.aht/60:.1f} min)",
            f"Traffic intensity: {r['traffic_intensity']:.2f} Erlangs",
            f"",
            f"Optimal staffing : {r['agent_req']} agents  "
            f"(+shrinkage {self.shrink*100:.0f}% -> {r['agent_req_shrink']} scheduled)",
            f"Predicted ASA    : {r['pred_asa']}s",
            f"Occupancy        : {r['pred_occupancy']*100:.1f}%",
            f"P(waiting)       : {r['prob_waiting']*100:.1f}%",
        ]
        if 'pred_sla_pct' in r:
            lines.append(f"Predicted SLA    : {r['pred_sla_pct']}%  (target: within {self.st}s)")
        return "\n".join(lines)
