"""Utility functions for the email game system"""

from typing import Dict, List
import json
import os
from pathlib import Path

from .config import PROJECT_ROOT

# ---------------------------------------------------------------------------
# Queue management helper - now handled in-memory by email server
# ---------------------------------------------------------------------------


def load_agent_pool() -> List[Dict[str, str]]:
    """Load the pool of available agents from JSON file"""
    agents_file = PROJECT_ROOT / "data" / "sample_agents.json"
    with open(agents_file, 'r') as f:
        data = json.load(f)
    return data["agents"]


# ---------------------------------------------------------------------------
# Queue-based agent selection (replaces *select_random_agents*)
# ---------------------------------------------------------------------------


def select_queued_agents(num_agents: int, pop: bool = True) -> List[Dict[str, str]]:
    """Legacy function for backwards compatibility.
    
    Queue management is now handled in-memory by the email server.
    This function returns an empty list since the auto-start mechanism
    in email_server.py handles the queue directly.
    
    Args:
        num_agents: Number of agents to dequeue (ignored).
        pop: If *True* the selected IDs are atomically removed (ignored).
    """
    # Queue is now managed in-memory by email_server.py
    # This function exists for backwards compatibility but returns empty
    # since the real queue management happens in the email server auto-start
    return []


# ---------------------------------------------------------------------------
# Message-alias pool utilities (Step 2 of message_alias_pool_plan)
# ---------------------------------------------------------------------------


def load_message_alias_pool() -> List[Dict[str, str]]:
    """Load the shared pool of {id, message, alias} objects.

    The path is overridable via MESSAGE_ALIAS_POOL_PATH so a competition host can
    point the server at a PRIVATE pool that contestants never receive (keeping
    the fuzzy round from being solved by a shipped alias->message lookup). The
    in-repo file is just a sample for local development. Returns an empty list if
    no pool file exists.
    """
    import os
    override = os.getenv("MESSAGE_ALIAS_POOL_PATH", "").strip()
    pool_file = Path(override) if override else (PROJECT_ROOT / "data" / "message_alias_pool.json")
    if not pool_file.exists():
        return []
    with pool_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Two shapes in the wild: the in-repo sample is {"pairs": [...]}, while the
    # generated competition/practice pools are a bare [...]. Accepting both is
    # the difference between a working event and every game dying in round 1 on
    # `'list' object has no attribute 'get'` - which is exactly what a
    # generated pool did until it was caught in testing.
    if isinstance(data, list):
        return data
    return data.get("pairs", [])