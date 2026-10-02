"""Test bootstrap.

Unit tests exercise the in-process approval store and checkpointer. They must
state that explicitly instead of relying on a silent fallback, because the
service itself is required to fail closed when PostgreSQL is unavailable.
"""

import asyncio
import os
import sys

# Environment variables win over the values in `.env`, so this pins the whole
# test session to the in-memory backends before `app.config` is imported.
os.environ["DATABASE_URL"] = ""
os.environ["CHECKPOINT_BACKEND"] = "memory"
os.environ["ENVIRONMENT"] = "test"
os.environ["RESUME_RECONCILER_ENABLED"] = "false"

# Tests must exercise the signed caller identity, not the legacy header trust.
os.environ["AGENT_INTERNAL_JWT_SECRET"] = "test-internal-jwt-secret"
os.environ["ALLOW_STATIC_INTERNAL_TOKEN"] = "false"
os.environ["AGENT_SERVICE_TOKEN"] = ""

# `asyncio.run` honours the policy, and psycopg's async driver needs a Selector
# loop on Windows. The integration test therefore needs this before it runs.
if sys.platform == "win32":
    policy_class = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_class is not None:
        asyncio.set_event_loop_policy(policy_class())
