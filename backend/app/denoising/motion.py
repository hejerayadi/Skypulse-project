from __future__ import annotations

from math import pi


def _ema_low_pass(values: list[float], alpha: float = 0.18) -> list[float]:
	if not values:
		return []

	filtered_values = [values[0]]
	for value in values[1:]:
		filtered_values.append(filtered_values[-1] + alpha * (value - filtered_values[-1]))
	return filtered_values


def denoise_motion_points(raw_values: list[float], sample_rate_hz: float = 1.0, cutoff_hz: float = 0.15) -> list[float]:
	if not raw_values:
		return []

	try:
		from scipy.signal import butter, filtfilt
	except ImportError:
		return _ema_low_pass(raw_values)

	if sample_rate_hz <= 0:
		return _ema_low_pass(raw_values)

	if cutoff_hz <= 0 or cutoff_hz >= sample_rate_hz / 2:
		return _ema_low_pass(raw_values)

	normalized_cutoff = cutoff_hz / (sample_rate_hz / 2)
	b, a = butter(N=4, Wn=normalized_cutoff, btype="low")

	if len(raw_values) <= max(len(a), len(b)) * 3:
		return _ema_low_pass(raw_values)

	filtered = filtfilt(b, a, raw_values)
	return [float(value) for value in filtered]
