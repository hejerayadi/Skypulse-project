"""
Feed the backend a live RF telemetry stream from outside the process.

The backend already runs this simulator internally, which is enough to watch the
3D view react. This script is for the other half of the question -- proving the
*ingest* path works, the one a real receiver or a Node-RED flow will use. It
generates the same readings and POSTs them one at a time to /api/rf/telemetry,
printing the prediction the backend sends back.

While it is running the backend's own simulator stays muted, so what the
dashboard shows is what this script sent.

    python simulate_rf_stream.py
    python simulate_rf_stream.py --interval 0.5 --api http://127.0.0.1:8000

Ctrl-C to stop; the backend resumes its internal simulator a few seconds later.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

from app.rf_fault.simulator import RFSimulator

SEVERITY_MARK = {"alert": "!!", "watch": " ~", "nominal": "  "}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between readings")
    parser.add_argument("--steps", type=int, default=0, help="Stop after N readings (0 = forever)")
    args = parser.parse_args()

    url = f"{args.api.rstrip('/')}/api/rf/telemetry"
    simulator = RFSimulator()

    print(f"Posting to {url} every {args.interval:g}s. Ctrl-C to stop.")
    print(
        f'{"#":>4}  {"scenario":14}  {"SNR":>5}  {"temp":>6}  {"freq":>7}  '
        f'{"wind":>5}  {"point":>6}  {"proba":>6}  {"label":13}  eta'
    )
    print("-" * 104)

    sent = 0

    # One client for the whole run: a new connection per reading would dominate
    # the timing at short intervals.
    with httpx.Client(timeout=10.0) as client:
        while args.steps == 0 or sent < args.steps:
            reading = simulator.step()

            try:
                response = client.post(url, json=reading)
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                print(f"  backend refused the reading ({e.response.status_code}): {e.response.text}")
                time.sleep(args.interval)
                continue
            except httpx.HTTPError as e:
                print(f"  backend unreachable: {e}")
                time.sleep(args.interval)
                continue

            sent += 1
            _print_row(sent, reading, response.json())
            time.sleep(args.interval)

    return 0


def _print_row(index: int, reading: dict, prediction: dict) -> None:
    probability = prediction.get("fault_probability")
    eta = prediction.get("eta_seconds")

    if not prediction.get("window_filled"):
        verdict = f'warming up {prediction["window_progress"]}/{prediction["window_size"]}'
        probability_text = "   ---"
        eta_text = ""
    else:
        mark = SEVERITY_MARK.get(prediction["severity"], "  ")
        verdict = f'{mark} {prediction["label"]}'
        probability_text = f"{probability:.4f}"
        if eta is None:
            eta_text = ""
        elif eta <= 1:
            eta_text = "happening now"
        else:
            eta_text = f'in ~{eta:.0f}s ({prediction.get("eta_basis")})'

    print(
        f'{index:>4}  {reading["scenario"]:14}  {reading["snr"]:>5.1f}  '
        f'{reading["sdr_temperature_c"]:>6.1f}  {reading["frequency_offset_hz"]:>7.1f}  '
        f'{reading["wind_speed_mps"]:>5.2f}  {reading["antenna_pointing_error_deg"]:>6.2f}  '
        f"{probability_text:>6}  {verdict:13}  {eta_text}"
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
