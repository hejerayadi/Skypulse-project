"""
Scenario-driven RF telemetry generator.

There is no SDR on the bench yet, so this stands in for one: it walks a plan of
scenarios -- quiet link, SDR heating up, wind gust, recovery -- and emits one
reading per tick with the same field names the real receiver will publish.

The physics is deliberately the same as the notebook the model was trained
against, because the point of this module is to prove the *serving* path works,
not to prove the model generalises. Two couplings carry the whole simulation:

  * a hot SDR drifts its local oscillator, so temperature pulls frequency
    offset, which costs SNR;
  * wind pushes the Yagi off boresight, so wind speed pulls pointing error,
    which costs RSSI and therefore SNR too.

Both end up degrading the link through SNR, so telling them apart means looking
at *which* channel moved -- which is what the multiclass model was trained on,
and why these two couplings are kept faithful to it here.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone

# Order matters: the scaler and the model were fitted on a window flattened in
# exactly this sequence, so this list is part of the model's contract.
FEATURES = [
    "snr",
    "antenna_pointing_error_deg",
    "ber",
    "signal_lock",
    "wind_speed_mps",
    "frequency_offset_hz",
    "sdr_temperature_c",
]

# Largest LO drift a fully heat-soaked SDR shows, in Hz.
MAX_FREQ_OFFSET = 2043.0
# Thermal time constant of the SDR enclosure, in ticks.
THERMAL_TAU = 9.0
# -3 dB beamwidth of the Yagi, in degrees. Pointing error is only meaningful
# relative to this.
YAGI_BEAMWIDTH = 25.0

# The plan cycles rather than ending, so a dashboard left open overnight keeps
# getting something to show instead of freezing on the last reading.
DEFAULT_PLAN: list[tuple[int, str]] = [
    (30, "normal"),
    (40, "sdr_heating"),
    (25, "sdr_cooling"),
    (20, "normal"),
    (35, "wind_gust"),
    (20, "wind_recovery"),
    (30, "normal"),
    (40, "sdr_heating"),
    (20, "normal"),
    (35, "wind_gust"),
    (20, "normal"),
]


def antenna_loss(pointing_error: float) -> float:
    """dB lost to being off boresight. Quadratic inside the beam, then capped."""

    return min(12.0 * (pointing_error / YAGI_BEAMWIDTH) ** 2, 30.0)


def snr_from_rssi(rssi: float) -> float:
    return _clip(8.0 + (rssi + 95.0) / 20.0 * 14.0, 8.0, 22.0)


def freq_penalty(offset: float) -> float:
    """
    dB of SNR lost to the receiver sitting off the carrier.

    Piecewise rather than smooth: a small offset is almost free, past half the
    loop bandwidth it starts costing real margin, and beyond that the
    demodulator is losing lock.
    """

    ratio = abs(offset) / 1500.0

    if ratio <= 0.5:
        return 0.5 * ratio * ratio * 4.0
    if ratio <= 1.0:
        return 1.0 + (ratio - 0.5) * 8.0
    return min(5.0 + (ratio - 1.0) * 10.0, 15.0)


def _clip(value: float, low: float, high: float) -> float:
    return float(max(low, min(high, value)))


class RFSimulator:
    """
    One ground station's worth of RF telemetry, one reading per `step()`.

    Holds its own state -- the receiver's temperature and the wind outside do
    not reset between readings -- so a single instance must be driven by a
    single caller. stream.py owns the only one the backend runs.
    """

    def __init__(self, plan: list[tuple[int, str]] | None = None, loop: bool = True):
        self.plan = plan or DEFAULT_PLAN
        self.loop = loop

        self.rssi = random.uniform(-88, -82)
        self.sdr_temp = 28.0
        self.wind_speed = 1.5
        self.wind_peak = 12.0

        self.step_count = 0
        self.scenario_idx = 0
        self.steps_in_scene = 0
        self.sdr_heat_step = 0

    # -- plan bookkeeping ---------------------------------------------------

    @property
    def finished(self) -> bool:
        return not self.loop and self.scenario_idx >= len(self.plan)

    def current_scenario(self) -> str:
        if self.scenario_idx >= len(self.plan):
            return "normal"
        return self.plan[self.scenario_idx][1]

    def scenario_length(self) -> int:
        if self.scenario_idx >= len(self.plan):
            return 10**9
        return self.plan[self.scenario_idx][0]

    def _advance(self) -> None:
        self.steps_in_scene += 1

        if self.steps_in_scene >= self.scenario_length():
            self.scenario_idx += 1
            self.steps_in_scene = 0
            self.sdr_heat_step = 0

            if self.loop and self.scenario_idx >= len(self.plan):
                self.scenario_idx = 0

        self.step_count += 1

    # -- generation ---------------------------------------------------------

    def step(self) -> dict:
        """Advance the simulation by one tick and return the reading it produced."""

        scenario = self.current_scenario()

        self.rssi = _clip(self.rssi + random.uniform(-1.2, 1.2), -95.0, -75.0)

        pointing_error = 0.0
        freq_offset = random.uniform(-50, 50)
        snr_penalty_db = 0.0
        rssi_loss_db = 0.0

        if scenario == "normal":
            self.sdr_temp = _clip(self.sdr_temp + random.uniform(-0.2, 0.2), 25.0, 34.0)
            self.wind_speed = max(0.0, random.gauss(1.5, 0.4))
            pointing_error = max(0.0, self.wind_speed * 0.05 + random.uniform(0, 0.2))

        elif scenario in ("sdr_heating", "sdr_cooling"):
            self.sdr_heat_step += 1

            if scenario == "sdr_heating":
                # Exponential approach to the soak temperature, which is what a
                # box with a fixed heat load and no active cooling actually does.
                frac = 1 - math.exp(-self.sdr_heat_step / (THERMAL_TAU * 2))
                self.sdr_temp = 28.0 + (66.0 - 28.0) * frac + random.uniform(-0.3, 0.3)
            else:
                cool_frac = math.exp(-self.sdr_heat_step / (THERMAL_TAU * 1.5))
                self.sdr_temp = 30.0 + (self.sdr_temp - 30.0) * cool_frac + random.uniform(-0.2, 0.2)

            self.sdr_temp = _clip(self.sdr_temp, 25.0, 72.0)

            drift_frac = min(1.0, max(0.0, self.sdr_temp - 28.0) / (66.0 - 28.0))
            freq_offset = max(0.0, drift_frac * MAX_FREQ_OFFSET + random.uniform(-30, 30))
            snr_penalty_db = freq_penalty(freq_offset)

            self.wind_speed = max(0.0, random.gauss(1.2, 0.3))
            pointing_error = max(0.0, self.wind_speed * 0.05 + random.uniform(0, 0.2))

        elif scenario == "wind_gust":
            if self.steps_in_scene == 0:
                self.wind_peak = random.uniform(10.0, 17.0)

            progress = self.steps_in_scene / self.scenario_length()

            # Ramp in over the first 40% of the scene, hold, then ease off -- a
            # gust front rather than a step change.
            if progress < 0.4:
                self.wind_speed = 2.0 + (self.wind_peak - 2.0) * (progress / 0.4)
            elif progress < 0.6:
                self.wind_speed = self.wind_peak + random.gauss(0, 0.8)
            else:
                self.wind_speed = self.wind_peak * (1 - (progress - 0.6) / 0.4) + 2.0

            self.wind_speed = max(0.0, self.wind_speed + random.gauss(0, 0.3))

            # Scaled so the peak gust walks the dish to ~85% of the mount's
            # 30 degree travel, whatever peak this particular gust drew.
            k_point = (30.0 * 0.85) / self.wind_peak
            pointing_error = _clip(k_point * self.wind_speed + random.gauss(0, 0.3), 0.0, 30.0)
            rssi_loss_db = antenna_loss(pointing_error)
            freq_offset = random.uniform(-60, 60)

            self.sdr_temp = _clip(self.sdr_temp + random.uniform(-0.2, 0.2), 25.0, 34.0)

        elif scenario == "wind_recovery":
            self.wind_speed = max(0.0, self.wind_speed * 0.85 + random.gauss(0, 0.2))
            pointing_error = max(0.0, self.wind_speed * 0.1 + random.uniform(0, 0.3))
            rssi_loss_db = antenna_loss(pointing_error)
            freq_offset = random.uniform(-60, 60)
            self.sdr_temp = _clip(self.sdr_temp + random.uniform(-0.2, 0.2), 25.0, 34.0)

        rssi_eff = round(_clip(self.rssi - rssi_loss_db, -105.0, -75.0), 1)
        snr_base = snr_from_rssi(rssi_eff) + random.uniform(-0.5, 0.5)
        snr = round(_clip(snr_base - snr_penalty_db, 0.5, 22.0), 1)
        ber = round(max(0.00005, 0.01 * math.exp(-0.25 * snr) + random.uniform(0, 0.0005)), 6)
        signal_lock = round(_clip(70.0 + snr * 1.3 + random.uniform(-1, 1), 0.0, 99.9), 1)

        reading = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scenario": scenario,
            "sdr_temperature_c": round(self.sdr_temp, 1),
            "wind_speed_mps": round(self.wind_speed, 2),
            "antenna_pointing_error_deg": round(pointing_error, 2),
            "rssi": rssi_eff,
            "snr": snr,
            "ber": ber,
            "signal_lock": signal_lock,
            "frequency_offset_hz": round(freq_offset, 1),
        }

        self._advance()
        return reading
