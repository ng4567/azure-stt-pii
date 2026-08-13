"""Generate PII-safe call-center fixtures for analytics demonstrations."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

COMPETITORS = ("AT&T", "Verizon", "Cox", "Comcast", "T-Mobile", None)
SCENARIOS = (
    ("retention", "cancel_service", "retained", "negative_to_neutral", True, False),
    ("technical_support", "report_outage", "escalated", "negative", False, True),
    ("billing", "dispute_charge", "resolved", "negative_to_positive", False, False),
    ("sales", "new_service", "completed_order", "positive", False, False),
    ("technical_support", "equipment_issue", "resolved", "negative_to_positive", False, False),
)
REGIONS = ("east", "central", "west")


def transcript(call_id: str, queue: str, intent: str, disposition: str, competitor: str | None) -> str:
    competitor_line = (
        f"CUSTOMER: I am comparing options, and {competitor} came up in my research.\n\n"
        if competitor
        else ""
    )
    return (
        f"Mock call transcript {call_id} - synthetic PII-safe test data.\n\n"
        f"REP: Thank you for calling Northstar Telecom {queue.replace('_', ' ')}. How can I help?\n\n"
        f"CUSTOMER: I need help with {intent.replace('_', ' ')}.\n\n"
        f"{competitor_line}"
        "REP: I can review the available options and resolve the issue without collecting personal information.\n\n"
        f"CUSTOMER: Please proceed with the recommended next step.\n\n"
        f"REP: The call is now marked as {disposition.replace('_', ' ')}.\n\n"
        "CUSTOMER: Thank you for your help."
    )


def build_records(count: int) -> list[dict[str, object]]:
    start = datetime(2026, 8, 1, 8, tzinfo=UTC)
    records: list[dict[str, object]] = []
    for index in range(count):
        queue, intent, disposition, sentiment, cancellation, escalation = SCENARIOS[index % len(SCENARIOS)]
        competitor = COMPETITORS[index % len(COMPETITORS)]
        mentions = 0 if competitor is None else 1 + (index % 3)
        call_id = f"synthetic-call-{index + 1:03d}"
        call_start = start + timedelta(hours=index * 3)
        summary = (
            f"{intent.replace('_', ' ').capitalize()} call was {disposition.replace('_', ' ')}."
            + (f" Customer referenced {competitor}." if competitor else "")
        )
        records.append(
            {
                "call_id": call_id,
                "call_start_utc": call_start.isoformat().replace("+00:00", "Z"),
                "queue_name": queue,
                "region": REGIONS[index % len(REGIONS)],
                "customer_intent": intent,
                "disposition": disposition,
                "pii_safe_summary": summary,
                "sentiment": sentiment,
                "escalation_flag": escalation,
                "cancellation_flag": cancellation,
                "competitor": competitor,
                "competitor_mentions": mentions,
                "ingestion_utc": "2026-08-13T18:00:00Z",
                "transcript": transcript(call_id, queue, intent, disposition, competitor),
            }
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "synthetic-call-corpus")
    args = parser.parse_args()
    if args.count < 1:
        raise ValueError("--count must be positive")

    records = build_records(args.count)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    transcripts = args.output_dir / "transcripts.jsonl"
    analytics = args.output_dir / "call_analytics.jsonl"
    with transcripts.open("w", encoding="utf-8") as transcript_file, analytics.open("w", encoding="utf-8") as analytics_file:
        for record in records:
            transcript_file.write(json.dumps({"call_id": record["call_id"], "transcript": record["transcript"]}) + "\n")
            analytics_file.write(json.dumps({key: value for key, value in record.items() if key != "transcript"}) + "\n")

    summary = {
        "record_count": len(records),
        "queues": Counter(record["queue_name"] for record in records),
        "competitors": Counter(record["competitor"] for record in records if record["competitor"]),
        "cancellations": sum(bool(record["cancellation_flag"]) for record in records),
        "escalations": sum(bool(record["escalation_flag"]) for record in records),
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
