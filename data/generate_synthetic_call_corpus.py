"""Generate a deterministic, PII-safe call-center corpus for analytics demos.

Two design constraints drive this file:

1. **PII-safe.** The repository's original ``mock-call-transcript.txt`` fixture
   deliberately contains realistic-looking names, addresses, card numbers and
   government identifiers so the redaction benchmark has something to detect.
   None of that belongs in an analytics store, so nothing here emits a personal
   name, address, phone number, email, card number or identifier.

2. **Deterministic.** Every value is a pure function of the record index, so a
   reviewer can regenerate the corpus and get byte-identical output, and the
   evaluation harness can assert exact expected answers.

The business-context fields (tenure, monthly revenue, handle time, repeat
contact) exist so the demo can answer revenue-weighted questions such as "how
much recurring revenue is on calls that mentioned a competitor" rather than a
bare mention count. Those are synthetic account attributes, not real customer
data; in production they would come from the customer's own billing system
joined in OneLake.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Six entries including "no competitor mentioned". The length interacts with the
# mention-count cycle below; see _mention_count for why that matters.
COMPETITORS: tuple[str | None, ...] = ("AT&T", "Verizon", "Cox", "Comcast", "T-Mobile", None)

# (queue, intent, disposition, sentiment, cancellation_flag, escalation_flag)
SCENARIOS: tuple[tuple[str, str, str, str, bool, bool], ...] = (
    ("retention", "cancel_service", "retained", "negative_to_neutral", True, False),
    ("technical_support", "report_outage", "escalated", "negative", False, True),
    ("billing", "dispute_charge", "resolved", "negative_to_positive", False, False),
    ("sales", "new_service", "completed_order", "positive", False, False),
    ("technical_support", "equipment_issue", "resolved", "negative_to_positive", False, False),
)

REGIONS: tuple[str, ...] = ("east", "central", "west")


def _mention_count(index: int) -> int:
    """Vary mentions 1-3 independently of which competitor was selected.

    Dividing by the competitor cycle length before taking the modulus means a
    given competitor walks through 1, 2, 3 across successive appearances. Using
    ``index % 3`` directly would be degenerate: ``len(COMPETITORS)`` is 6, so
    every competitor would be pinned to a single mention count forever.
    """
    return 1 + (index // len(COMPETITORS)) % 3


def _transcript(
    call_id: str, queue: str, intent: str, disposition: str, competitor: str | None
) -> str:
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
        "REP: I can review the available options and resolve this without collecting "
        "any personal information on this call.\n\n"
        "CUSTOMER: Please proceed with the recommended next step.\n\n"
        f"REP: The call is now marked as {disposition.replace('_', ' ')}.\n\n"
        "CUSTOMER: Thank you for your help."
    )


def build_records(count: int) -> list[dict[str, Any]]:
    start = datetime(2026, 8, 1, 8, tzinfo=UTC)
    records: list[dict[str, Any]] = []

    for index in range(count):
        queue, intent, disposition, sentiment, cancellation, escalation = SCENARIOS[
            index % len(SCENARIOS)
        ]
        # Let some cancellation attempts actually churn. If every retention call
        # ended "retained" the save rate would be a flat 100%, which is the kind
        # of number that makes a contact-centre audience stop believing the rest
        # of the demo. Cycling 7 of every 10 gives a defensible ~70%.
        if cancellation and (index // len(SCENARIOS)) % 10 >= 7:
            disposition = "cancelled"
            sentiment = "negative"
        competitor = COMPETITORS[index % len(COMPETITORS)]
        mentions = 0 if competitor is None else _mention_count(index)
        call_id = f"synthetic-call-{index + 1:03d}"

        summary = (
            f"{intent.replace('_', ' ').capitalize()} call was "
            f"{disposition.replace('_', ' ')}."
        )
        if competitor:
            summary += f" Customer referenced {competitor}."

        records.append(
            {
                "call_id": call_id,
                "call_start_utc": (start + timedelta(hours=index * 3))
                .isoformat()
                .replace("+00:00", "Z"),
                "queue_name": queue,
                "region": REGIONS[index % len(REGIONS)],
                "customer_intent": intent,
                "disposition": disposition,
                "pii_safe_summary": summary,
                "sentiment": sentiment,
                # Emitted as 0/1 rather than JSON booleans so the corpus loads
                # directly into the Oracle NUMBER(1) columns and Warehouse BIT
                # columns without a type-mapping step. A JSON `true` against
                # NUMBER(1) raises ORA-01722: invalid number.
                "escalation_flag": int(escalation),
                "cancellation_flag": int(cancellation),
                "competitor": competitor,
                "competitor_mentions": mentions,
                # Synthetic account context. In production these columns are
                # joined from the customer's billing and CRM systems in OneLake.
                "handle_time_seconds": 240 + (index % 17) * 30,
                "repeat_contact_flag": int(index % 7 == 0),
                "account_tenure_months": 6 + (index % 11) * 6,
                "monthly_revenue_usd": 55 + (index % 9) * 15,
                "ingestion_utc": "2026-08-13T18:00:00Z",
                "transcript": _transcript(call_id, queue, intent, disposition, competitor),
            }
        )

    return records


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    at_risk = [r for r in records if r["competitor"] and r["cancellation_flag"]]
    competitors = {r["competitor"] for r in records if r["competitor"]}
    return {
        "record_count": len(records),
        "queues": dict(sorted(Counter(r["queue_name"] for r in records).items())),
        "regions": dict(sorted(Counter(r["region"] for r in records).items())),
        "dispositions": dict(sorted(Counter(r["disposition"] for r in records).items())),
        "competitor_calls": dict(
            sorted(Counter(r["competitor"] for r in records if r["competitor"]).items())
        ),
        "competitor_mentions": {
            competitor: sum(
                r["competitor_mentions"] for r in records if r["competitor"] == competitor
            )
            for competitor in sorted(competitors)
        },
        "cancellations": sum(1 for r in records if r["cancellation_flag"]),
        "saved_cancellations": sum(
            1 for r in records if r["cancellation_flag"] and r["disposition"] == "retained"
        ),
        "escalations": sum(1 for r in records if r["escalation_flag"]),
        "repeat_contacts": sum(1 for r in records if r["repeat_contact_flag"]),
        "monthly_revenue_on_competitor_cancellation_calls_usd": sum(
            r["monthly_revenue_usd"] for r in at_risk
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).parent / "synthetic-call-corpus"
    )
    args = parser.parse_args()
    if args.count < 1:
        raise SystemExit("--count must be a positive integer")

    records = build_records(args.count)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    transcripts = args.output_dir / "transcripts.jsonl"
    analytics = args.output_dir / "call_analytics.jsonl"

    with (
        transcripts.open("w", encoding="utf-8", newline="\n") as transcript_file,
        analytics.open("w", encoding="utf-8", newline="\n") as analytics_file,
    ):
        for record in records:
            transcript_file.write(
                json.dumps({"call_id": record["call_id"], "transcript": record["transcript"]})
                + "\n"
            )
            analytics_file.write(
                json.dumps({k: v for k, v in record.items() if k != "transcript"}) + "\n"
            )

    summary = _summarize(records)
    (args.output_dir / "manifest.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
