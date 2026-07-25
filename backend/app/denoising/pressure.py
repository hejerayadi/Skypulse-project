from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KalmanConfig:
	process_noise: float = 0.001
	measurement_noise: float = 0.25 ** 2
	initial_uncertainty: float = 1.0


def denoise_pressure_points(raw_values: list[float], config: KalmanConfig | None = None) -> list[float]:
	if not raw_values:
		return []

	filter_config = config or KalmanConfig()
	filtered_values: list[float] = [raw_values[0]]
	uncertainty = filter_config.initial_uncertainty

	for value in raw_values[1:]:
		predicted_value = filtered_values[-1]
		predicted_uncertainty = uncertainty + filter_config.process_noise

		kalman_gain = predicted_uncertainty / (predicted_uncertainty + filter_config.measurement_noise)
		updated_value = predicted_value + kalman_gain * (value - predicted_value)
		uncertainty = (1 - kalman_gain) * predicted_uncertainty
		filtered_values.append(updated_value)

	return filtered_values
