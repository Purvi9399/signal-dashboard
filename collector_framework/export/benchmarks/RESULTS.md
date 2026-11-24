# Upload map benchmark

Machine-local timing for RawEvent → `source_events` row mapping + validation.
No network. Re-run: `python -m export.benchmarks.bench_upload_map`

| N | elapsed_ms | per_event_ms |
|---|---|---|
| 20 | 0.209 | 0.0105 |
| 100 | 0.654 | 0.0065 |
