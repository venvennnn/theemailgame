"""Offline unit tests for CustomAgent parsing / auth prune (no API key)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _make_agent():
    from my_agent import CustomAgent

    with patch.object(CustomAgent, "__init__", lambda self, *a, **k: None):
        agent = CustomAgent.__new__(CustomAgent)
    agent.agent_id = "me"
    agent.moderator_agent = "moderator"
    agent.driver = None
    agent._reset_game_state()
    agent.send_message = MagicMock(return_value={"success": True, "message_id": "m1"})
    agent.sign_and_respond = MagicMock(return_value={"success": True})
    agent.submit_signature = MagicMock(return_value={"success": True})
    return agent


def test_round1_broadcast_and_parse():
    agent = _make_agent()
    body = (
        'Welcome!\n\n**ROUND 1**\n\n'
        'You must get signatures for this EXACT message: '
        '"The dancing penguins have arrived at the ice cream parlor!"\n'
        "1. You must REQUEST signatures from these agents: alice, bob\n"
        "2. You are AUTHORIZED to sign messages for these agents: carol, dave\n"
    )
    agent._handle_moderator({"from": "moderator", "body": body})
    assert agent.round_no == 1
    assert "penguins" in agent.my_message
    assert agent.request_list == ["alice", "bob"]
    assert agent.auth_explicit == {"carol", "dave"}
    assert agent.auth_fuzzy == []
    # Broadcast to roster (= request + auth peers)
    assert {"alice", "bob", "carol", "dave"} <= agent.requested_this_round


def test_impersonation_block_and_sole_candidate():
    agent = _make_agent()
    # Simulate end of round 1 auth.
    agent.round_no = 1
    agent.auth_explicit = {"carol", "dave"}
    agent.signed_this_round = {"carol"}
    agent.my_message = "old"
    agent.seen_messages = {
        "carol": {1: "The dancing penguins have arrived at the ice cream parlor!"},
        "dave": {1: "My pet cactus just learned how to play the harmonica."},
    }

    body = (
        '**ROUND 2**\n'
        'You must get signatures for this EXACT message: "Quantum jellybeans taste different in parallel universes."\n'
        "1. You must REQUEST signatures from these agents: alice, bob\n"
        "2. You are AUTHORIZED to sign messages for these agents: "
        "the agent who mentioned waddling arctic birds visiting a frozen dessert establishment "
        "(from last round; their message this round may be different)\n"
    )
    agent._handle_moderator({"from": "moderator", "body": body})
    assert agent.prev_auth == {"carol", "dave"}
    assert len(agent.auth_fuzzy) == 1
    assert agent.auth_explicit == set()

    # Alice was never in prev_auth → free decline (impersonation).
    assert agent._may_sign_for("alice") is False

    # Sole-candidate does not fire (2 prev partners); force LLM path mocked.
    agent._ask_llm_json = MagicMock(return_value={"match": True})
    assert agent._may_sign_for("carol") is True
    agent._ask_llm_json = MagicMock(return_value={"match": False})
    agent._resolved.pop("dave", None)
    assert agent._may_sign_for("dave") is False


def test_sole_candidate_no_llm():
    agent = _make_agent()
    agent.round_no = 1
    agent.auth_explicit = {"carol"}
    agent.my_message = "x"
    body = (
        '**ROUND 2**\n'
        'You must get signatures for this EXACT message: "hi there friend okay"\n'
        "1. You must REQUEST signatures from these agents: bob\n"
        "2. You are AUTHORIZED to sign messages for these agents: "
        "the agent who mentioned birds (from last round; their message this round may be different)\n"
    )
    agent._handle_moderator({"from": "moderator", "body": body})
    assert agent.prev_auth == {"carol"}
    agent._ask_llm_json = MagicMock(side_effect=AssertionError("LLM should not be called"))
    assert agent._may_sign_for("carol") is True
    assert agent._may_sign_for("eve") is False


def test_sign_and_submit():
    agent = _make_agent()
    agent.round_no = 1
    agent.my_message = "The clockwork squirrels have organized a nut revolution."
    agent.auth_explicit = {"alice"}

    agent._handle_peer({
        "from": "alice",
        "body": (
            "Please sign this message for me: Seven purple elephants are hosting a tea party.\n\n"
            "---BEGIN MESSAGE---\nSeven purple elephants are hosting a tea party.\n---END MESSAGE---"
        ),
    })
    assert agent.sign_and_respond.called
    assert agent.sign_and_respond.call_args.kwargs["to_agent"] == "alice"

    agent._handle_peer({
        "from": "eve",
        "body": "Please sign this message for me: Invisible socks are hard to find.",
    })
    assert "eve" in agent.declined_this_round

    good = {
        "original_message": agent.my_message,
        "signature": "abc",
        "signer": "bob",
        "signed_for": "me",
        "timestamp": "t",
        "signature_type": "rsa_pss_sha256",
    }
    agent._handle_peer({
        "from": "bob",
        "body": "Here\nSIGNED_MESSAGE_JSON:" + json.dumps(good),
    })
    assert agent.submit_signature.call_count == 1


if __name__ == "__main__":
    tests = [
        test_round1_broadcast_and_parse,
        test_impersonation_block_and_sole_candidate,
        test_sole_candidate_no_llm,
        test_sign_and_submit,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    sys.exit(1 if failed else 0)
