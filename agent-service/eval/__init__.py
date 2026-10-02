"""Retrieval evaluation utilities.

Two entry points matter:

* ``evaluate_retrieval`` — measure the golden set. `groundedness` is `None`
  unless a judge is passed; see `eval.metrics` for why that is deliberate.
* ``python -m eval.run_eval`` — the CI gate. Its exit code is the contract, so
  the gate is imported from its own module rather than re-exported here and
  pulled into every process that only wants a metric.
"""

from eval.metrics import (
    EvaluationReport,
    citation_accuracy_for_query,
    evaluate_retrieval,
    resolve_citation,
)

__all__ = [
    "EvaluationReport",
    "citation_accuracy_for_query",
    "evaluate_retrieval",
    "resolve_citation",
]
