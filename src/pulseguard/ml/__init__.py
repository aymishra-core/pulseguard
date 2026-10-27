"""Optional latency scoring. The detector uses the standard library only."""

from pulseguard.ml.anomaly import detect, detect_in_process, time_modes

__all__ = ["detect", "detect_in_process", "time_modes"]
