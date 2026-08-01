"""Offline unit tests for CustomAgent parsing / fuzzy matching (no API key)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _make_agent():
    # Avoid BaseAgent.__init__ (network/register). Build a bare instance.
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


def test_parse_round1_instructions():
    agent = _make_agent()
    body = (
        'Welcome, Me!\n\n**ROUND 1** - Message signing.\n\n'
        '**Your Assigned Message:**\n'
        'You must get signatures for this EXACT message: '
        '"The dancing penguins have arrived at the ice cream parlor!"\n\n'
        "**Your Signing Requirements:**\n"
        "1. You must REQUEST signatures from these agents: alice, bob\n"
        "2. You are AUTHORIZED to sign messages for these agents: charlie, diana\n"
    )
    agent._handle_moderator({"from": "moderator", "subject": "instructions", "body": body})
    assert agent.round_number == 1
    assert agent.assigned_message == "The dancing penguins have arrived at the ice cream parlor!"
    assert agent.request_list == ["alice", "bob"]
    assert agent.explicit_authorized == {"charlie", "diana"}
    # Should request from known agents (alice, bob at minimum).
    assert agent.send_message.call_count >= 2


def test_fuzzy_auth_split_and_resolve():
    agent = _make_agent()
    agent.seen_messages = {
        "alice": ["The dancing penguins have arrived at the ice cream parlor!"],
        "bob": ["My pet cactus just learned how to play the harmonica."],
    }
    body = (
        '**ROUND 2**\n'
        'You must get signatures for this EXACT message: "Quantum jellybeans taste different in parallel universes."\n'
        "1. You must REQUEST signatures from these agents: bob, charlie\n"
        "2. You are AUTHORIZED to sign messages for these agents: "
        "the agent who mentioned waddling arctic birds visiting a frozen dessert establishment "
        "(from last round; their message this round may be different), bob\n"
    )
    agent._handle_moderator({"from": "moderator", "subject": "r2", "body": body})
    assert "bob" in agent.explicit_authorized
    assert any("waddling arctic birds" in e for e in agent.auth_entries)
    assert agent.fuzzy_resolved
    assert "alice" in agent.fuzzy_resolved.values()
    assert agent._is_authorized("alice") is True
    assert agent._is_authorized("charlie") is False


def test_sign_authorized_decline_unauthorized():
    agent = _make_agent()
    agent.round_number = 1
    agent.assigned_message = "hello"
    agent.explicit_authorized = {"alice"}
    agent.known_agents = {"alice", "eve"}

    agent._handle_peer({
        "from": "alice",
        "body": 'Please sign this message for me: "Seven purple elephants are hosting a tea party in my backyard."',
    })
    assert agent.sign_and_respond.called
    assert agent.sign_and_respond.call_args.kwargs["to_agent"] == "alice"
    assert "Seven purple elephants" in agent.sign_and_respond.call_args.kwargs["message_to_sign"]

    agent._handle_peer({
        "from": "eve",
        "body": 'Please sign this message for me: "Invisible socks are surprisingly difficult to find in the morning."',
    })
    # Decline via send_message, never sign.
    assert "eve" in agent.declined_this_round
    assert agent.sign_and_respond.call_count == 1


def test_submit_only_own_correct_message():
    agent = _make_agent()
    agent.assigned_message = "The clockwork squirrels have organized a nut revolution."
    good = {
        "original_message": agent.assigned_message,
        "signature": "abc",
        "signer": "alice",
        "signed_for": "me",
        "timestamp": "t",
        "signature_type": "rsa_pss_sha256",
    }
    bad_for = dict(good, signed_for="other")
    bad_msg = dict(good, original_message="wrong text")

    agent._maybe_submit_signed({
        "from": "alice",
        "body": "Here\n\nSIGNED_MESSAGE_JSON:" + __import__("json").dumps(good),
    })
    agent._maybe_submit_signed({
        "from": "alice",
        "body": "SIGNED_MESSAGE_JSON:" + __import__("json").dumps(bad_for),
    })
    agent._maybe_submit_signed({
        "from": "alice",
        "body": "SIGNED_MESSAGE_JSON:" + __import__("json").dumps(bad_msg),
    })
    assert agent.submit_signature.call_count == 1


def test_synonym_scores():
    from my_agent import _score_match

    desc = "The agent who mentioned waddling arctic birds visiting a frozen dessert establishment"
    msg = "The dancing penguins have arrived at the ice cream parlor!"
    other = "My pet cactus just learned how to play the harmonica."
    assert _score_match(desc, msg) > _score_match(desc, other)
    assert _score_match(desc, msg) >= 0.22


if __name__ == "__main__":
    tests = [
        test_synonym_scores,
        test_parse_round1_instructions,
        test_fuzzy_auth_split_and_resolve,
        test_sign_authorized_decline_unauthorized,
        test_submit_only_own_correct_message,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    if failed:
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
