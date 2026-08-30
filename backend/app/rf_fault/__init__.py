"""
RF link fault detection for the ground station.

Three pieces, deliberately separate:

  * detector.py  — the trained model, the sliding window it needs, and the
                   attribution that turns "fault" into "overheating" or "wind".
  * simulator.py — a scenario-driven telemetry generator, so the pipeline can be
                   exercised end to end before real SDR telemetry exists.
  * stream.py    — the live layer: one reading in, one prediction out, fanned
                   out to every connected browser.
"""
