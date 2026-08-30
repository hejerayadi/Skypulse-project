"""
The trained RF fault detector, plus the one thing it does not do by itself.

What the model is: a RandomForestClassifier over a flattened 20-step window of
seven RF features, with a StandardScaler in front and a LabelEncoder behind. It
is multiclass -- `NORMAL`, `SDR_OVERHEATING`, `ANTENNA_WIND` -- so both the
verdict and the cause come straight out of `predict_proba`. The window is
labelled by its **last** row, which is what makes this a nowcast of the current
state rather than a summary of the last twenty readings.

The one thing added here is *when*. The model scores the present; an operator
wants a warning before the link is lost. forecast() fits a least-squares trend
across the window and extrapolates it to the point where the impairment
actually bites, so the number degrades honestly -- when nothing is trending,
there is no ETA, and the UI says so rather than inventing one.

drivers() is explanation, not decision: the raw readings behind the model's
call, with their trends, so an alert can be checked rather than trusted.

Everything is guarded so a backend without scikit-learn installed still boots:
`available` goes false and the endpoints say why, in the same spirit as the
CouchDB handling in app/database.py.
"""

from __future__ import annotations

import os
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from app.rf_fault.simulator import FEATURES, YAGI_BEAMWIDTH

MODEL_DIR = Path(__file__).resolve().parents[1] / "time series model"

MODEL_PATH = Path(os.getenv("RF_MODEL_PATH") or MODEL_DIR / "cubesat_multiclass_model.pkl")
SCALER_PATH = Path(os.getenv("RF_SCALER_PATH") or MODEL_DIR / "cubesat_multiclass_scaler.pkl")
ENCODER_PATH = Path(os.getenv("RF_ENCODER_PATH") or MODEL_DIR / "cubesat_label_encoder.pkl")

# Must match training. The scaler carries 140 = 20 x 7 features, so getting this
# wrong is caught at load time rather than producing quiet nonsense.
WINDOW_SIZE = int(os.getenv("RF_WINDOW_SIZE", "20"))

# How much of the model's belief has to sit outside NORMAL before the dashboard
# calls it a fault. Swap in the threshold from the notebook if you tuned one.
THRESHOLD = float(os.getenv("RF_THRESHOLD", "0.5"))

# Below the fault threshold but worth showing: the link is drifting, and the
# whole point of a forecast is to act before the classifier commits.
WATCH_PROBABILITY = float(os.getenv("RF_WATCH_PROBABILITY", "0.2"))

# ---------------------------------------------------------------------------
# The encoder's class names, mapped to what the dashboard reacts to.
#
# Keyed by the strings in cubesat_label_encoder.pkl. A retrained model that adds
# a class still loads — the unknown class lands on "degraded", which the UI
# renders as a generic alert rather than dropping it silently.
# ---------------------------------------------------------------------------

CLASS_LABELS = {
    "NORMAL": "normal",
    "SDR_OVERHEATING": "overheating",
    "ANTENNA_WIND": "wind",
}
FALLBACK_LABEL = "degraded"
HEALTHY_LABEL = "normal"

# ---------------------------------------------------------------------------
# Where each impairment stops being noise and starts being a problem.
#
# `nominal` is the top of the quiet band — in the training set NORMAL never
# exceeds 34 °C, 50 Hz of offset, 3.2 m/s or 0.45° of pointing error.
# `critical` is where the link is actually damaged, and is what an ETA counts
# down to. These come from the link budget, not from the model, and are only
# used for the forecast and the driver read-out — never to pick the label.
# ---------------------------------------------------------------------------

THERMAL_NOMINAL_C = 34.0
THERMAL_CRITICAL_C = 60.0

# Past 1500 Hz the demodulator is losing lock.
FREQ_NOMINAL_HZ = 150.0
FREQ_CRITICAL_HZ = 1500.0

WIND_NOMINAL_MPS = 4.0
WIND_CRITICAL_MPS = 12.0

# Half the -3 dB beamwidth. Past it the dish is pointing the satellite into its
# own sidelobe.
POINTING_NOMINAL_DEG = 2.0
POINTING_CRITICAL_DEG = YAGI_BEAMWIDTH / 2

# An ETA further out than this is extrapolation theatre — a trend measured over
# one window cannot credibly forecast a quarter of an hour.
MAX_ETA_SECONDS = 900.0

# Slopes below this are indistinguishable from sensor noise, and dividing by
# them produces enormous meaningless ETAs.
MIN_TREND = 1e-4


def _clip01(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _ramp(value: float, nominal: float, critical: float) -> float:
    """0 while the reading is unremarkable, 1 once it is critical."""

    return _clip01((value - nominal) / (critical - nominal))


def _slope(times: list[float], values: list[float]) -> float:
    """
    Least-squares slope in units per second.

    Per second rather than per sample so the forecast survives a change of tick
    rate — the training set is one reading a minute, the simulator runs at two
    seconds, and a real receiver will be neither.
    """

    n = len(values)
    if n < 3:
        return 0.0

    mean_t = sum(times) / n
    mean_v = sum(values) / n

    denominator = sum((t - mean_t) ** 2 for t in times)
    if denominator <= 0:
        return 0.0

    return sum((t - mean_t) * (v - mean_v) for t, v in zip(times, values)) / denominator


def _time_to_cross(current: float, target: float, slope: float) -> float | None:
    """Seconds until a linear trend reaches `target`, or None if it never will."""

    if current >= target:
        return 0.0
    if slope <= MIN_TREND:
        return None

    eta = (target - current) / slope
    return eta if eta <= MAX_ETA_SECONDS else None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


_NO_FORECAST = {"eta_seconds": None, "basis": None, "trend": None, "trend_unit": None}


class ModelUnavailable(RuntimeError):
    """The detector cannot score anything — missing dependency or missing file."""


class RFFaultDetector:
    """
    One sliding window, one model, one prediction per reading.

    Stateful by nature: a window-based model cannot score a reading in
    isolation, so the caller must feed it every reading in order. All public
    methods take the same lock, because the simulator thread and HTTP requests
    both land here.
    """

    def __init__(self, window_size: int = WINDOW_SIZE, threshold: float = THRESHOLD):
        self.window_size = window_size
        self.threshold = threshold

        self._lock = threading.RLock()
        self._model = None
        self._scaler = None
        self._load_error: str | None = None

        # Positional: index i of predict_proba's output is this UI label.
        self._label_order: list[str] = []

        # (epoch_seconds, reading) so trends are measured against the wall clock
        # rather than against sample index.
        self._window: deque[tuple[float, dict]] = deque(maxlen=window_size)

        self._samples_seen = 0

    # -- loading ------------------------------------------------------------

    def load(self) -> bool:
        """
        Load model, scaler and label encoder. Never raises — a backend that
        cannot score is still a backend, and /api/rf/status reports the reason.
        """

        with self._lock:
            if self._model is not None:
                return True

            try:
                import joblib
            except ImportError:
                return self._fail(
                    "scikit-learn / joblib are not installed. "
                    "Run: pip install -r backend/requirements.txt"
                )

            for path in (MODEL_PATH, SCALER_PATH, ENCODER_PATH):
                if not path.exists():
                    return self._fail(f"Model file not found: {path}")

            try:
                model = joblib.load(MODEL_PATH)
                scaler = joblib.load(SCALER_PATH)
                encoder = joblib.load(ENCODER_PATH)
            except Exception as e:
                return self._fail(f"Could not load the model: {e}")

            expected = self.window_size * len(FEATURES)
            for name, estimator in (("scaler", scaler), ("model", model)):
                actual = getattr(estimator, "n_features_in_", expected)
                if actual != expected:
                    # Caught here rather than at the first predict, where it
                    # would surface as an opaque shape error on a background
                    # thread.
                    return self._fail(
                        f"Window mismatch: the {name} expects {actual} features but "
                        f"{self.window_size} steps x {len(FEATURES)} features is {expected}. "
                        f"Set RF_WINDOW_SIZE to {actual // len(FEATURES)}."
                    )

            # predict_proba's columns are ordered by the model's own classes_,
            # which are the encoder's integer codes. Resolving the names once
            # here means no per-prediction inverse_transform, and it fails loudly
            # now rather than mislabelling every prediction later.
            try:
                names = encoder.inverse_transform(model.classes_)
            except Exception as e:
                return self._fail(f"Label encoder does not match the model's classes: {e}")

            self._label_order = [CLASS_LABELS.get(str(name), FALLBACK_LABEL) for name in names]

            unknown = [str(n) for n in names if str(n) not in CLASS_LABELS]
            if unknown:
                print(
                    f"RF detector: unrecognised class(es) {', '.join(unknown)} — "
                    f"reported as '{FALLBACK_LABEL}'. Add them to CLASS_LABELS to "
                    "give them their own treatment in the 3D view."
                )

            if HEALTHY_LABEL not in self._label_order:
                return self._fail(
                    f"No healthy class: the encoder holds {list(names)}, none of which "
                    f"maps to '{HEALTHY_LABEL}'. Nothing could ever read as nominal."
                )

            self._model = model
            self._scaler = scaler
            self._load_error = None

            print(
                f"RF fault detector loaded: {MODEL_PATH.name} "
                f"({self.window_size}-step window, classes {list(names)}, "
                f"threshold {self.threshold})"
            )
            return True

    def _fail(self, message: str) -> bool:
        self._load_error = message
        print("RF detector disabled -", message)
        return False

    @property
    def available(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def labels(self) -> list[str]:
        """The labels this model can report, for /api/rf/status."""

        return sorted(set(self._label_order))

    def reset(self) -> None:
        """Drop the window. Used when the reading source changes underneath us."""

        with self._lock:
            self._window.clear()

    # -- scoring ------------------------------------------------------------

    def observe(self, reading: dict, source: str = "live") -> dict:
        """
        Add one reading to the window and score it.

        Returns the full prediction envelope the frontend consumes — the reading
        itself, the model's class probabilities, the forecast, and enough of the
        working out to justify both.
        """

        with self._lock:
            if not self.available and not self.load():
                raise ModelUnavailable(self._load_error or "Model not loaded")

            missing = [f for f in FEATURES if reading.get(f) is None]
            if missing:
                raise ValueError(f"Reading is missing required fields: {', '.join(missing)}")

            now = datetime.now(timezone.utc)
            self._window.append((now.timestamp(), reading))
            self._samples_seen += 1

            if len(self._window) < self.window_size:
                return self._envelope(
                    reading=reading,
                    now=now,
                    source=source,
                    probabilities={},
                    label=HEALTHY_LABEL,
                    severity="nominal",
                    forecast=_NO_FORECAST,
                )

            probabilities = self._predict()

            # Everything the model does not consider healthy. Equivalent to the
            # single probability a binary detector would emit, which is what
            # makes one threshold enough to drive three classes.
            fault_probability = 1.0 - probabilities.get(HEALTHY_LABEL, 0.0)

            if fault_probability >= self.threshold:
                severity = "alert"
            elif fault_probability >= WATCH_PROBABILITY:
                severity = "watch"
            else:
                severity = "nominal"

            if severity == "nominal":
                # Naming a cause the model does not believe in would be noise,
                # and so would counting down to a fault nothing is heading for.
                label = HEALTHY_LABEL
                forecast = _NO_FORECAST
            else:
                label = self._leading_fault(probabilities)
                forecast = self._forecast(label)

            return self._envelope(
                reading=reading,
                now=now,
                source=source,
                probabilities=probabilities,
                label=label,
                severity=severity,
                forecast=forecast,
            )

    def _predict(self) -> dict[str, float]:
        """
        Class probabilities for the current window, keyed by UI label.

        Summed rather than assigned, so two encoder classes mapping to the same
        label (a retrained model splitting a fault into stages, say) add up
        instead of one silently overwriting the other.
        """

        flat = [float(reading[f]) for _, reading in self._window for f in FEATURES]

        # Imported locally so this module can be imported without numpy, matching
        # the joblib guard in load().
        import numpy as np

        scaled = self._scaler.transform(np.asarray(flat, dtype=float).reshape(1, -1))
        row = self._model.predict_proba(scaled)[0]

        probabilities: dict[str, float] = {}
        for label, probability in zip(self._label_order, row):
            probabilities[label] = probabilities.get(label, 0.0) + float(probability)

        return probabilities

    def _leading_fault(self, probabilities: dict[str, float]) -> str:
        """The most likely class that is not the healthy one."""

        faults = {k: v for k, v in probabilities.items() if k != HEALTHY_LABEL}
        if not faults:
            return FALLBACK_LABEL

        return max(faults, key=lambda label: faults[label])

    @staticmethod
    def _margin(probabilities: dict[str, float]) -> float:
        """
        How decisively the model picked, 0–1.

        The top class beating the runner-up by a hair is a genuinely ambiguous
        prediction, and the UI should be able to say so rather than presenting a
        coin toss as a verdict.
        """

        if len(probabilities) < 2:
            return 0.0

        ranked = sorted(probabilities.values(), reverse=True)
        return _clip01(ranked[0] - ranked[1])

    # -- forecasting --------------------------------------------------------

    def _series(self, field: str) -> tuple[list[float], list[float]]:
        times = [t for t, _ in self._window]
        values = [float(reading[field]) for _, reading in self._window]
        return times, values

    def _forecast(self, label: str) -> dict:
        """
        When the fault the model named reaches the point where it costs the link.

        Only the two physical faults have a threshold to count down to. Anything
        else gets no ETA, which is the truthful answer rather than a fabricated
        one.
        """

        if label == "overheating":
            return self._extrapolate(
                "sdr_temperature_c",
                THERMAL_CRITICAL_C,
                f"SDR temperature reaching {THERMAL_CRITICAL_C:.0f} °C",
                "°C/s",
            )

        if label == "wind":
            return self._extrapolate(
                "antenna_pointing_error_deg",
                POINTING_CRITICAL_DEG,
                f"pointing error reaching {POINTING_CRITICAL_DEG:.1f}° off boresight",
                "°/s",
            )

        return _NO_FORECAST

    def _extrapolate(self, field: str, target: float, basis: str, unit: str) -> dict:
        times, values = self._series(field)
        slope = _slope(times, values)
        eta = _time_to_cross(values[-1], target, slope)

        if eta is None:
            return _NO_FORECAST

        return {
            "eta_seconds": round(eta, 1),
            "basis": basis,
            "trend": round(slope, 4),
            "trend_unit": unit,
        }

    # -- presentation -------------------------------------------------------

    # key, display name, unit, nominal, critical, the fault it points at
    DRIVER_SPECS = [
        ("sdr_temperature_c", "SDR temperature", "°C", THERMAL_NOMINAL_C, THERMAL_CRITICAL_C, "overheating"),
        ("frequency_offset_hz", "Frequency offset", "Hz", FREQ_NOMINAL_HZ, FREQ_CRITICAL_HZ, "overheating"),
        ("wind_speed_mps", "Wind speed", "m/s", WIND_NOMINAL_MPS, WIND_CRITICAL_MPS, "wind"),
        ("antenna_pointing_error_deg", "Pointing error", "°", POINTING_NOMINAL_DEG, POINTING_CRITICAL_DEG, "wind"),
    ]

    def _drivers(self) -> list[dict]:
        """
        The four readings behind the model's call, with the trend that produced
        the ETA. Sorted worst first so the UI can show the top two and stop.

        Averaged over the last few samples rather than read off the newest one:
        a single noisy reading should not reorder the explanation under an alert
        that has not itself changed.
        """

        if len(self._window) < self.window_size:
            return []

        recent = list(self._window)[-5:]
        drivers = []

        for key, label, unit, nominal, critical, cause in self.DRIVER_SPECS:
            # Frequency offset drifts negative on a healthy link and positive on
            # a hot one; the distance from zero is what matters.
            value = _mean([abs(float(r[key])) if key == "frequency_offset_hz" else float(r[key])
                           for _, r in recent])
            times, series = self._series(key)

            drivers.append(
                {
                    "key": key,
                    "label": label,
                    "unit": unit,
                    "value": round(value, 2),
                    "critical": critical,
                    "severity": round(_ramp(value, nominal, critical), 3),
                    "trend": round(_slope(times, series), 4),
                    "cause": cause,
                }
            )

        drivers.sort(key=lambda d: d["severity"], reverse=True)
        return drivers

    def _envelope(
        self,
        reading: dict,
        now: datetime,
        source: str,
        probabilities: dict[str, float],
        label: str,
        severity: str,
        forecast: dict,
    ) -> dict:
        filled = len(self._window) == self.window_size

        return {
            "timestamp": now.isoformat(),
            "source": source,
            # Only the simulator knows this; live telemetry carries no ground
            # truth, and the UI must never present it as a prediction.
            "scenario": reading.get("scenario"),
            "label": label,
            "severity": severity,
            # Everything outside the healthy class — the one number a threshold
            # is applied to.
            "fault_probability": (
                None if not filled else round(1.0 - probabilities.get(HEALTHY_LABEL, 0.0), 4)
            ),
            # The model's probability for the label actually being shown, which
            # for a healthy link is its confidence in *healthy*.
            "confidence": None if not filled else round(probabilities.get(label, 0.0), 4),
            # How far the top class beat the runner-up.
            "margin": None if not filled else round(self._margin(probabilities), 4),
            "class_probabilities": {k: round(v, 4) for k, v in sorted(probabilities.items())},
            "threshold": self.threshold,
            "eta_seconds": forecast.get("eta_seconds"),
            "eta_basis": forecast.get("basis"),
            "eta_trend": forecast.get("trend"),
            "eta_trend_unit": forecast.get("trend_unit"),
            "drivers": self._drivers(),
            "window_size": self.window_size,
            "window_filled": filled,
            "window_progress": len(self._window),
            "samples_seen": self._samples_seen,
            "reading": reading,
        }


# One detector per process. The window is shared state by design: whichever
# source is feeding readings, they all have to go through the same 20-step
# history or the model sees a discontinuity.
detector = RFFaultDetector()
