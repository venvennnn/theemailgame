"""Configuration constants for the email game system"""

import os
from pathlib import Path

# Get project root - from src/game/config.py, need to go up 2 levels to reach project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Game configuration
NUM_AGENTS = 4  # Number of agents to select for each game
NUM_ROUNDS = 3  # Number of rounds per session (default: 3)
REQUESTS_PER_AGENT = 2  # How many signatures each agent must request and can sign per round (must be < NUM_AGENTS)
ROUND_DURATION_SEC = int(os.getenv("EMAIL_GAME_ROUND_DURATION_SEC", "60"))  # secs/round; env-tunable for fast local training
PRE_GAME_GRACE_SEC = 3        # Pause after a game forms before round 1, so remote
                              # agents are connected and settled before the clock starts

# ------------------------------------------------------------------
# OpenAI model configuration
# ------------------------------------------------------------------
# Default LLM model used by all agents unless overridden at runtime.
# Using the lighter "gpt-4o-mini" variant greatly reduces cost while
# retaining strong reasoning performance.

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")

KEEP_DASHBOARD_SEC = 20  # How long to keep servers alive after the session

# Concurrent games: how many game sessions may run at once. 0 = unlimited
# (form a game whenever enough matched agents are waiting). A positive value is
# an optional safety cap (e.g. to bound memory on a small host).
MAX_CONCURRENT_GAMES = int(os.getenv("MAX_CONCURRENT_GAMES", "0"))