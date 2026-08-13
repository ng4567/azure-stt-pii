"""Determinism harness for the call-analytics Data Agent.

The failure mode this guards against is the one that sank the first executive
demo: vector search over raw transcripts returned a different count for the same
question on consecutive runs (7 mentions, then 8). Structured NL2SQL is supposed
to be deterministic. This harness proves it instead of asserting it.

For each question we compute a ground-truth answer with hand-written SQL against
the same table the agent queries, then ask the agent the same question in natural
language N times and compare. A passing run means every repetition produced the
ground-truth value.

Ground truth can be computed two ways:
  * ``--source warehouse`` runs the baseline SQL against the Fabric Warehouse.
  * ``--source corpus`` evaluates the same aggregations locally over the
    generated JSONL corpus, so the expected values can be reviewed without a
    Fabric connection.

The agent runner is pluggable. ``--agent none`` (default) computes and prints
ground truth only, which is useful when validating the question set itself.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CORPUS = Path(__file__).parent / "synthetic-call-corpus" / "call_analytics.jsonl"


@dataclass(frozen=True)
class Question:
    """A question with a deterministic, checkable answer."""

    id: str
    prompt: str
    baseline_sql: str
    local: Callable[[Sequence[dict[str, Any]]], Any]
    note: str = ""


def _competitor_mentions(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in rows:
        competitor = row.get("competitor")
        if competitor:
            totals[competitor] = totals.get(competitor, 0) + int(row["competitor_mentions"])
    return dict(sorted(totals.items()))


def _flag_count(rows: Sequence[dict[str, Any]], flag: str) -> int:
    return sum(1 for row in rows if row.get(flag))


QUESTIONS: tuple[Question, ...] = (
    Question(
        id="total-calls",
        prompt="How many calls are in the call analytics table?",
        baseline_sql="SELECT COUNT(*) AS total_calls FROM dbo.call_analytics;",
        local=len,
    ),
    Question(
        id="competitor-mentions",
        prompt=(
            "Across all calls, how many normalized competitor mentions are there? "
            "Break the total down by competitor."
        ),
        baseline_sql=(
            "SELECT competitor, SUM(competitor_mentions) AS mentions "
            "FROM dbo.call_analytics WHERE competitor IS NOT NULL "
            "GROUP BY competitor ORDER BY competitor;"
        ),
        local=_competitor_mentions,
        note="The 7-versus-8 regression from the original vector-search demo.",
    ),
    Question(
        id="cancellation-calls",
        prompt="How many calls are flagged for cancellation?",
        baseline_sql=(
            "SELECT COUNT(*) AS cancellations FROM dbo.call_analytics WHERE cancellation_flag = 1;"
        ),
        local=lambda rows: _flag_count(rows, "cancellation_flag"),
    ),
    Question(
        id="escalation-calls",
        prompt="How many calls are flagged for escalation?",
        baseline_sql=(
            "SELECT COUNT(*) AS escalations FROM dbo.call_analytics WHERE escalation_flag = 1;"
        ),
        local=lambda rows: _flag_count(rows, "escalation_flag"),
    ),
    Question(
        id="competitor-cancellation-overlap",
        prompt=(
            "How many calls mention a competitor and are also flagged for cancellation?"
        ),
        baseline_sql=(
            "SELECT COUNT(*) AS at_risk FROM dbo.call_analytics "
            "WHERE competitor IS NOT NULL AND cancellation_flag = 1;"
        ),
        local=lambda rows: sum(
            1 for row in rows if row.get("competitor") and row.get("cancellation_flag")
        ),
        note="Churn-risk intersection; the metric an executive actually acts on.",
    ),
    Question(
        id="revenue-at-risk",
        prompt=(
            "What is the total monthly recurring revenue on calls that mentioned a "
            "competitor and were flagged for cancellation?"
        ),
        baseline_sql=(
            "SELECT SUM(monthly_revenue_usd) AS revenue_at_risk FROM dbo.call_analytics "
            "WHERE competitor IS NOT NULL AND cancellation_flag = 1;"
        ),
        local=lambda rows: sum(
            int(row["monthly_revenue_usd"])
            for row in rows
            if row.get("competitor") and row.get("cancellation_flag")
        ),
        note=(
            "Requires joining transcript signals to billing data. A packaged "
            "third-party SaaS that only sees transcripts cannot answer this."
        ),
    ),
    Question(
        id="avg-handle-time-by-intent",
        prompt="What is the average handle time in seconds for each customer intent?",
        baseline_sql=(
            "SELECT customer_intent, CAST(AVG(CAST(handle_time_seconds AS FLOAT)) AS DECIMAL(10,2)) "
            "AS avg_handle_seconds FROM dbo.call_analytics "
            "GROUP BY customer_intent ORDER BY customer_intent;"
        ),
        local=lambda rows: {
            intent: round(
                statistics.fmean(
                    [
                        float(row["handle_time_seconds"])
                        for row in rows
                        if row["customer_intent"] == intent
                    ]
                ),
                2,
            )
            for intent in sorted({row["customer_intent"] for row in rows})
        },
    ),
    Question(
        id="calls-by-queue",
        prompt="How many calls are there per queue?",
        baseline_sql=(
            "SELECT queue_name, COUNT(*) AS calls FROM dbo.call_analytics "
            "GROUP BY queue_name ORDER BY queue_name;"
        ),
        local=lambda rows: dict(
            sorted(
                (
                    (queue, sum(1 for row in rows if row["queue_name"] == queue))
                    for queue in {row["queue_name"] for row in rows}
                )
            )
        ),
    ),
    Question(
        id="escalation-rate-by-region",
        prompt="What is the escalation rate per region, as a percentage?",
        baseline_sql=(
            "SELECT region, "
            "CAST(100.0 * SUM(CAST(escalation_flag AS INT)) / COUNT(*) AS DECIMAL(5,2)) AS escalation_rate_pct "
            "FROM dbo.call_analytics GROUP BY region ORDER BY region;"
        ),
        local=lambda rows: {
            region: round(
                100.0
                * sum(1 for row in rows if row["region"] == region and row["escalation_flag"])
                / sum(1 for row in rows if row["region"] == region),
                2,
            )
            for region in sorted({row["region"] for row in rows})
        },
    ),
)


@dataclass
class QuestionResult:
    question_id: str
    expected: Any
    observed: list[Any] = field(default_factory=list)

    @property
    def deterministic(self) -> bool:
        """True when every repetition produced the same answer."""
        if not self.observed:
            return False
        first = json.dumps(self.observed[0], sort_keys=True, default=str)
        return all(
            json.dumps(value, sort_keys=True, default=str) == first for value in self.observed
        )

    @property
    def correct(self) -> bool:
        """True when every repetition matched the SQL baseline."""
        if not self.observed:
            return False
        expected = json.dumps(self.expected, sort_keys=True, default=str)
        return all(
            json.dumps(value, sort_keys=True, default=str) == expected for value in self.observed
        )


def load_corpus(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(
            f"Corpus not found at {path}. Run generate_synthetic_call_corpus.py first."
        )
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def ground_truth_from_corpus(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {question.id: question.local(rows) for question in QUESTIONS}


def ground_truth_from_warehouse(connection_string: str) -> dict[str, Any]:
    """Run the baseline SQL against the Fabric Warehouse.

    Requires ``pyodbc`` and an Entra token or ActiveDirectoryInteractive
    connection string. Kept separate from the local path so the question set can
    be reviewed without database access.
    """
    import pyodbc  # imported lazily; only needed for --source warehouse

    results: dict[str, Any] = {}
    with pyodbc.connect(connection_string) as connection:
        for question in QUESTIONS:
            cursor = connection.cursor()
            cursor.execute(question.baseline_sql)
            rows = cursor.fetchall()
            if len(rows) == 1 and len(rows[0]) == 1:
                results[question.id] = rows[0][0]
            else:
                results[question.id] = {row[0]: row[1] for row in rows}
    return results


def evaluate(
    expected: dict[str, Any],
    ask: Callable[[str], Any] | None,
    repetitions: int,
) -> list[QuestionResult]:
    results: list[QuestionResult] = []
    for question in QUESTIONS:
        result = QuestionResult(question_id=question.id, expected=expected[question.id])
        if ask is not None:
            for _ in range(repetitions):
                result.observed.append(ask(question.prompt))
        results.append(result)
    return results


def render(results: Iterable[QuestionResult], repetitions: int, scored: bool) -> str:
    lines: list[str] = []
    results = list(results)
    for result in results:
        expected = json.dumps(result.expected, sort_keys=True, default=str)
        if not scored:
            lines.append(f"  {result.question_id}: expected {expected}")
            continue
        status = "PASS" if result.correct else "FAIL"
        detail = "" if result.correct else f" observed={json.dumps(result.observed, default=str)}"
        drift = "" if result.deterministic else " NON-DETERMINISTIC"
        lines.append(f"  [{status}]{drift} {result.question_id}: expected {expected}{detail}")

    if scored:
        passed = sum(1 for result in results if result.correct)
        stable = sum(1 for result in results if result.deterministic)
        lines.append("")
        lines.append(f"  accuracy      {passed}/{len(results)}")
        lines.append(f"  determinism   {stable}/{len(results)} stable across {repetitions} runs")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("corpus", "warehouse"), default="corpus")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--connection-string", default="")
    parser.add_argument("--agent", choices=("none", "fabric"), default="none")
    parser.add_argument("--data-agent-url", default="")
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()

    if args.source == "warehouse":
        if not args.connection_string:
            parser.error("--source warehouse requires --connection-string")
        expected = ground_truth_from_warehouse(args.connection_string)
    else:
        expected = ground_truth_from_corpus(load_corpus(args.corpus))

    ask: Callable[[str], Any] | None = None
    if args.agent == "fabric":
        if not args.data_agent_url:
            parser.error("--agent fabric requires --data-agent-url")
        ask = _fabric_agent_runner(args.data_agent_url)

    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")

    results = evaluate(expected, ask, args.repetitions)
    scored = ask is not None
    heading = "Data Agent determinism report" if scored else "SQL baseline (ground truth)"
    print(heading)
    print(render(results, args.repetitions, scored))

    if scored and not all(result.correct for result in results):
        return 1
    return 0


def _fabric_agent_runner(published_url: str) -> Callable[[str], Any]:
    """Build a callable that asks the published Fabric data agent a question.

    Uses the Fabric data agent SDK when available. The SDK returns prose, so the
    caller is responsible for parsing the number out of the response; we return
    the raw text and let the comparison surface mismatches rather than silently
    coercing.
    """
    from fabric.dataagent.client import FabricDataAgentAPI  # type: ignore[import-not-found]

    client = FabricDataAgentAPI(published_url)

    def ask(prompt: str) -> Any:
        return client.ask(prompt)

    return ask


if __name__ == "__main__":
    raise SystemExit(main())
