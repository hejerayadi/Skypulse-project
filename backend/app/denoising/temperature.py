from __future__ import annotations


def denoise_temperature_points(raw_values: list[float], kernel_size: int = 5) -> list[float]:
	if not raw_values:
		return []

	if kernel_size < 1 or kernel_size % 2 == 0:
		raise ValueError("kernel_size must be a positive odd number")

	half_window = kernel_size // 2
	filtered_values: list[float] = []

	for index in range(len(raw_values)):
		window: list[float] = []
		for offset in range(-half_window, half_window + 1):
			neighbor_index = min(max(index + offset, 0), len(raw_values) - 1)
			window.append(raw_values[neighbor_index])

		sorted_window = sorted(window)
		filtered_values.append(sorted_window[len(sorted_window) // 2])

	return filtered_values
