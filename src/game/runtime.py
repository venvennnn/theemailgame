"""Runtime functions for server bootstrap and per-round orchestration"""

import asyncio
import os
import threading
from typing import Dict, List, Set
import uvicorn
import httpx
from datetime import datetime
import random

from .models import RoundResult
from .config import ROUND_DURATION_SEC, REQUESTS_PER_AGENT, PRE_GAME_GRACE_SEC
from .assignment import generate_balanced_assignment_lists, validate_balanced_assignment
from .scoring import process_submission_emails, reset_scored_submissions, clear_score_events
from .instructions import send_moderator_instructions
from .utils import load_message_alias_pool

# Global tracking for fuzzy description system
previous_round_signing_permissions: Dict[str, List[str]] = {}
previous_round_aliases: Dict[str, str] = {}
previous_round_request_sets: Dict[str, frozenset[str]] = {}
# Track last round's exact message for each agent so we can log comparisons
previous_round_messages: Dict[str, str] = {}

# Message ids already attributed to a prior round. get_all_messages returns the
# whole game's mail, so without this each round's conversations would accumulate
# every earlier round's messages (e.g. Round 3 showing the Round 1 instruction).
# Per-game module state (one process per game), reset implicitly by the process.
_recorded_message_ids: set = set()

# Track which whimsical messages each agent has already been assigned across the
# whole session so we never reuse one for that agent.
previous_messages_by_agent: Dict[str, Set[str]] = {}


def _run_server_in_thread(app, port: int, name: str) -> uvicorn.Server:
    """Run a server in a background thread"""
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(cfg)

    def _runner() -> None:  # pragma: no cover
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())

    t = threading.Thread(target=_runner, daemon=True, name=f"{name}-server")
    t.start()
    return server


async def wait_for_server_ready(port: int, name: str) -> None:
    """Wait for a server to become ready"""
    for _ in range(20):
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"http://127.0.0.1:{port}/health", timeout=2)
                if r.status_code == 200:
                    return
        except Exception:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError(f"{name} server did not become ready in time.")


async def run_single_round(round_number: int, selected_agents: List[Dict[str, str]], 
                          agents: List, agent_tasks: List) -> RoundResult:
    """Run a single round of the signature game and return results"""
    global previous_round_signing_permissions, previous_round_aliases, previous_round_request_sets
    
    
    # Note: Email server retains all messages across rounds for dashboard continuity
    # Agents also retain their transcript history for natural language reasoning
    
    # Choose unique message/alias pairs for this round, ensuring we never assign
    # the *same* message to the *same* agent in later rounds of the same
    # session.
    pool = load_message_alias_pool().copy()
    if len(pool) < len(selected_agents):
        raise RuntimeError("Not enough message/alias pairs in pool for the number of agents.")

    # Shuffle to keep selection random while we pick sequentially.
    random.shuffle(pool)

    agent_messages: Dict[str, str] = {}
    alias_by_agent_current: Dict[str, str] = {}

    # Clear tracking at the first round of a new session
    if round_number == 1:
        previous_messages_by_agent.clear()
        reset_scored_submissions()
        # A fresh match must not replay the previous one's point pops.
        clear_score_events([a["id"] for a in selected_agents])

    for agent_cfg in selected_agents:
        agent_id = agent_cfg["id"]
        previously_used = previous_messages_by_agent.get(agent_id, set())

        # Find the first pair in the pool whose message this agent hasn't seen
        chosen_pair = None
        for pair in pool:
            if pair["message"] not in previously_used:
                chosen_pair = pair
                break

        if chosen_pair is None:
            raise RuntimeError(f"Message pool exhausted: no new message available for agent {agent_id}.")

        # Record and remove from pool so another agent cannot get the exact same pair this round.
        pool.remove(chosen_pair)
        agent_messages[agent_id] = chosen_pair["message"]
        alias_by_agent_current[agent_id] = chosen_pair["alias"]

        # Update history
        previous_messages_by_agent.setdefault(agent_id, set()).add(chosen_pair["message"])
    
    # Generate balanced signing assignments
    agent_ids = [agent["id"] for agent in selected_agents]

    # Ensure we don't repeat the same assignment (ignoring ordering within lists)
    def to_request_set_map(rl: Dict[str, List[str]]) -> Dict[str, frozenset[str]]:
        return {aid: frozenset(lst) for aid, lst in rl.items()}

    prev_canonical = previous_round_request_sets

    max_attempts_assignment = 10
    for _ in range(max_attempts_assignment):
        request_lists, signing_permissions = generate_balanced_assignment_lists(agent_ids, requests_per_agent=REQUESTS_PER_AGENT)
        canonical = to_request_set_map(request_lists)
        if round_number == 1 or canonical != prev_canonical:
            break
    
    # Log assignment information
    print(f"\nAssignment for Round {round_number}:")
    for agent_id, request_list in request_lists.items():
        print(f"  {agent_id} must request from: {request_list}")

    # Also log signing permissions so it's visible before the round starts
    print("Signing permissions:")
    for agent_id, can_sign_for in signing_permissions.items():
        print(f"  {agent_id} can sign for: {can_sign_for}")
    
    # Log message assignments (current vs previous round)
    print("Message assignments:")
    if round_number == 1:
        for aid in agent_ids:
            print(f"  {aid}: \"{agent_messages[aid]}\"")
    else:
        for aid in agent_ids:
            prev = previous_round_messages.get(aid, "-")
            curr = agent_messages[aid]
            print(f"  {aid}: current=\"{curr}\" | previous=\"{prev}\"")
    
    # Create round result object
    round_result = RoundResult(round_number, agent_ids, request_lists, signing_permissions, agent_messages)
    round_result.start_time = datetime.now()
    
    # Validate assignment balance
    validate_balanced_assignment(request_lists, REQUESTS_PER_AGENT)
    
    # For fuzzy descriptions, provide aliases from the PREVIOUS round so that
    # an agent references the alias they observed last round rather than the
    # freshly generated alias for this round.
    aliases_for_fuzzy = previous_round_aliases if round_number > 1 else {}

    # Give agents a moment to settle after the game forms before round 1's
    # clock starts, so remote agents with higher latency aren't penalized in
    # the opening round (they were reliably scoring 0 in round 1).
    if round_number == 1 and PRE_GAME_GRACE_SEC > 0:
        print(f"[runtime] Pre-game grace: waiting {PRE_GAME_GRACE_SEC}s for agents to settle before round 1...")
        await asyncio.sleep(PRE_GAME_GRACE_SEC)

    await send_moderator_instructions(
        request_lists,
        signing_permissions,
        agent_messages,
        aliases_for_fuzzy,
        round_number,
        previous_round_signing_permissions if round_number > 1 else None,
    )
    
    # Brief delay to ensure all instructions are delivered
    await asyncio.sleep(2)

    # Fixed duration round
    print(f"[runtime] Round {round_number} running for {ROUND_DURATION_SEC}s...")
    await asyncio.sleep(ROUND_DURATION_SEC)

    # Process signature submissions and scoring
    agent_scores, agent_performance, message_mismatches, signature_events = await process_submission_emails(agent_ids, request_lists, signing_permissions, agent_messages)

    round_result.end_time = datetime.now()
    round_result.agent_scores = agent_scores
    round_result.agent_performance = agent_performance
    round_result.message_mismatches = message_mismatches
    round_result.signature_events = signature_events
    
    # Collect round statistics over HTTP (this orchestrator runs in its own
    # process), scoped to this game's participants so concurrent games don't mix.
    participants = set(agent_ids) | {"moderator"}
    messages = []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(os.getenv("EMAIL_GAME_SELF_URL", "http://127.0.0.1:8000") + "/get_all_messages", timeout=10,
                                    headers={"X-Internal-Key": os.getenv("EMAIL_GAME_INTERNAL_KEY", "")})
        if resp.status_code == 200:
            messages = [m for m in resp.json().get("messages", [])
                        if m.get("from") in participants and m.get("to") in participants]
    except Exception as e:
        print(f"[runtime] round-stats fetch failed (non-fatal): {e}")

    # Keep only messages NEW this round - get_all_messages returns the whole
    # game's mail, so filter out anything already recorded in a prior round.
    new_messages = []
    for m in messages:
        mid = m.get("message_id") or m.get("id") or (
            f"{m.get('from')}|{m.get('to')}|{m.get('timestamp')}|{m.get('subject')}")
        if mid in _recorded_message_ids:
            continue
        _recorded_message_ids.add(mid)
        new_messages.append(m)
    messages = new_messages
    round_result.total_messages = len(messages)

    # Group messages by conversation
    conversations = {}
    for msg in messages:
        key = tuple(sorted([msg['from'], msg['to']]))
        if key not in conversations:
            conversations[key] = []
        conversations[key].append(msg)
    round_result.conversations = conversations
    
    
    # Store data for the next round
    previous_round_signing_permissions = signing_permissions.copy()
    previous_round_aliases = alias_by_agent_current.copy()
    previous_round_request_sets = to_request_set_map(request_lists)
    # Store current messages for next round's comparison
    previous_round_messages.clear()
    previous_round_messages.update(agent_messages)
    
    return round_result