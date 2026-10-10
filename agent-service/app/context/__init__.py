"""Server-side context engineering: budgeting, rendering, reporting.

The public entry point is `assemble_context`, which returns an
`AssembledContext`. Everything else in this package exists to make that decision
explicit and auditable:

======================  ====================================================
module                  responsibility
======================  ====================================================
``tokenizer.py``        how tokens are counted (and by which counter)
``model_specs.py``      per-model window/output facts, with their source
``policy.py``           per-kind weights, floors, ceilings, protection
``budget.py``           the allocation algorithm and the hard ceiling
``render.py``           the shape of the system prompt
``report.py``           what was dropped, truncated and why
``summary.py``          the rolling summary of evicted messages
======================  ====================================================
"""

from app.context.builder import AssembledContext, assemble_context, estimate_tokens
from app.context.budget import BudgetInfeasible
from app.context.policy import CONTEXT_POLICIES, policy_for
from app.context.summary import summarise_request, summarize_sync

__all__ = [
    "AssembledContext",
    "BudgetInfeasible",
    "CONTEXT_POLICIES",
    "assemble_context",
    "estimate_tokens",
    "policy_for",
    "summarise_request",
    "summarize_sync",
]
