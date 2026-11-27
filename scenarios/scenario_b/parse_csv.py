"""CSV metrics parser — reads agent execution logs and extracts stats."""

import csv
import io
from dataclasses import dataclass


@dataclass
class SessionStats:
    session_id: str
    total_tokens: int
    tool_calls: int
    errors: int
    duration_s: float


def parse_metrics(csv_text: str) -> list[SessionStats]:
    """Parse a CSV string of agent session metrics into SessionStats objects.

    Expected columns: session_id, tokens, tool_calls, errors, duration_s
    """
    results = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        results.append(SessionStats(
            session_id=row["session_id"].strip(),
            total_tokens=int(row["tokens"]),
            tool_calls=int(row["tool_calls"]),
            errors=int(row["errors"]),
            duration_s=float(row["duration_s"]),
        ))
    return results


def find_anomalies(stats: list[SessionStats], token_threshold: float = 3.0) -> list[dict]:
    """Flag sessions whose token usage exceeds the median by token_threshold standard deviations."""
    if len(stats) < 2:
        return []
    tokens = sorted(s.total_tokens for s in stats)
    n = len(tokens)
    median = tokens[n // 2] if n % 2 else (tokens[n // 2 - 1] + tokens[n // 2]) / 2
    mean = sum(tokens) / n
    variance = sum((t - mean) ** 2 for t in tokens) / n
    std = variance ** 0.5
    if std == 0:
        return []
    anomalies = []
    for s in stats:
        z = (s.total_tokens - median) / std
        if z > token_threshold:
            anomalies.append({"session_id": s.session_id, "tokens": s.total_tokens,
                              "z_score": round(z, 2), "median": median})
    return anomalies


def summarize(csv_text: str) -> dict:
    """Parse CSV and return a summary with anomaly detection.

    BUG 1: The CSV has a UTF-8 BOM that csv.DictReader doesn't strip,
           so the first column name becomes '\\ufeffsession_id' instead
           of 'session_id'. This causes a KeyError on some inputs.

    BUG 2: duration_s values in the CSV use comma as decimal separator
           for some rows (European locale). float() fails on "12,5".
           The obvious fix (replace comma with dot) breaks session_ids
           that contain commas in quoted fields.
    """
    stats = parse_metrics(csv_text)
    anomalies = find_anomalies(stats)
    total_tokens = sum(s.total_tokens for s in stats)
    total_errors = sum(s.errors for s in stats)
    return {
        "session_count": len(stats),
        "total_tokens": total_tokens,
        "total_errors": total_errors,
        "anomalies": anomalies,
    }
