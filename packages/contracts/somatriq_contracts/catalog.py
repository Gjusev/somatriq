"""Metric catalog constants (spec §69 quality engine input; system.metrics seed).

M1 hardcodes the heart-rate entry; the system.metrics table is the source of
truth and ingest validation will read from it once the catalog grows (§153
golden datasets extend this list).
"""

HEART_RATE_BPM_MIN = 20.0
HEART_RATE_BPM_MAX = 250.0
HEART_RATE_UNIT = "bpm"
HEART_RATE_CADENCE_SECONDS = 1
HEART_RATE_AGGREGATIONS = ("none", "1m", "5m", "1h")
