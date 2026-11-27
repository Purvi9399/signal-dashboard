"""Tests for the CSV metrics parser. Run: pytest test_parse_csv.py -v"""

import pytest
from parse_csv import parse_metrics, find_anomalies, summarize, SessionStats


CLEAN_CSV = """session_id,tokens,tool_calls,errors,duration_s
sess-001,1200,5,0,45.2
sess-002,1500,8,1,62.0
sess-003,980,3,0,30.5
"""

# This CSV has a UTF-8 BOM (byte order mark) at the start — common when
# exported from Excel or Windows tools
BOM_CSV = "﻿" + """session_id,tokens,tool_calls,errors,duration_s
sess-010,2000,10,0,50.0
sess-011,1800,7,2,48.3
sess-012,45000,25,5,180.0
"""

# This CSV has European-locale decimal separators in duration_s
EURO_CSV = """session_id,tokens,tool_calls,errors,duration_s
sess-020,1100,4,0,"45,2"
sess-021,1300,6,1,"62,0"
sess-022,900,3,0,"30,5"
"""

# This CSV has BOTH issues: BOM + European decimals + a session_id with comma
COMBINED_CSV = "﻿" + """session_id,tokens,tool_calls,errors,duration_s
"sess-030,alpha",2200,12,0,"55,8"
sess-031,1600,5,1,"42,0"
sess-032,50000,30,8,"200,5"
"""


def test_parse_clean_csv():
    stats = parse_metrics(CLEAN_CSV)
    assert len(stats) == 3
    assert stats[0].session_id == "sess-001"
    assert stats[0].total_tokens == 1200
    assert stats[2].duration_s == 30.5


def test_parse_bom_csv():
    """CSV with UTF-8 BOM — the first column header has a hidden \\ufeff prefix."""
    stats = parse_metrics(BOM_CSV)
    assert len(stats) == 3
    assert stats[0].session_id == "sess-010"


def test_parse_euro_csv():
    """CSV with European decimal separators (comma instead of dot)."""
    stats = parse_metrics(EURO_CSV)
    assert len(stats) == 3
    assert stats[0].duration_s == 45.2


def test_parse_combined_csv():
    """CSV with BOM, European decimals, AND quoted session_id containing comma."""
    stats = parse_metrics(COMBINED_CSV)
    assert len(stats) == 3
    assert stats[0].session_id == "sess-030,alpha"
    assert stats[0].duration_s == 55.8


def test_anomaly_detection_clean():
    stats = parse_metrics(CLEAN_CSV)
    anomalies = find_anomalies(stats)
    assert anomalies == []


def test_anomaly_detection_spike():
    stats = parse_metrics(BOM_CSV)
    anomalies = find_anomalies(stats)
    assert len(anomalies) == 1
    assert anomalies[0]["session_id"] == "sess-012"


def test_summarize_clean():
    result = summarize(CLEAN_CSV)
    assert result["session_count"] == 3
    assert result["total_tokens"] == 3680
    assert result["total_errors"] == 1
    assert result["anomalies"] == []


def test_summarize_bom():
    result = summarize(BOM_CSV)
    assert result["session_count"] == 3
    assert len(result["anomalies"]) == 1


def test_summarize_combined():
    """The hardest test: everything broken at once."""
    result = summarize(COMBINED_CSV)
    assert result["session_count"] == 3
    assert result["total_tokens"] == 53800
    assert len(result["anomalies"]) == 1
    assert result["anomalies"][0]["session_id"] == "sess-032"
