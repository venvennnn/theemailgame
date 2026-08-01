"""
Base Agent - Phase 1
"""

# Standard libraries
import json
import re
import asyncio
import signal
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
import base64
import os
import warnings

# Quiet third-party DeprecationWarnings (e.g. datetime.utcnow) so the player's
# console shows game activity, not library noise. Full detail is still logged.
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Keep emoji/log output safe when stdout is redirected on non-UTF-8 consoles.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Cryptography imports
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

# Third-party
import requests
import jwt  # PyJWT – used for decoding token expiry
import ssl

import certifi
import websockets
from dotenv import load_dotenv
from .llm_driver import LLMDriver
from .game.config import OPENAI_MODEL

# Auto-load local .env so OPENAI_API_KEY and other secrets are available
load_dotenv()

class BaseAgent:
    """Basic agent for The Email Game"""
    
    def __init__(self, agent_id: str, username: Optional[str] = None,
                 email_server_url: str = "http://localhost:8000",
                 moderator_agent: str = "moderator",
                 dev_mode: bool = False,
                 prompt_file: Optional[str] = None,
                 model: Optional[str] = None,
                 temperature: float = 1.0):
        # agent_id is the one public identity (leaderboard + in-game addressing).
        # username is just an optional local label; it defaults to the agent_id.
        self.agent_id = agent_id
        self.username = username or agent_id
        self.email_server_url = email_server_url
        print(f"[{self.agent_id}] starting up, server: {email_server_url}")
        self.moderator_agent = moderator_agent
        self.dev_mode = dev_mode
        
        # Agent state
        self.running = False
        self.instructions_processed = 0
        self.messages_sent = 0
        self.current_instruction = None

        # Game/round state - driven by the moderator's messages (not a self-
        # incremented counter, which drifts after a reconnect). in_game is False
        # while waiting in the queue; current_round is the moderator's stated
        # round for the current game.
        self.in_game = False
        self.current_round = 0
        
        # Inactivity reminder system
        self.can_send_reminder = False  # Set to True when moderator message received
        self.last_message_time = datetime.now()
        self.inactivity_threshold_seconds = 25  # Send reminder after 25 seconds of inactivity

        
        # ------------------------------------------------------------------
        # RSA signing capability + JWT auth state
        # ------------------------------------------------------------------

        self.rsa_private_key, self.rsa_public_key = self._load_rsa_keys()

        # Keep PEM around for registration
        self._public_key_pem: str = self.rsa_public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

        # JWT auth fields
        self._jwt_token: Optional[str] = None
        self._view_token: Optional[str] = None  # read-only token for the watch link
        self._jwt_expiry: float = 0.0  # unix timestamp

        # Optionally pin the Watch/Leaderboard links to a stationary top line via
        # an ANSI scroll region. OFF by default: most terminals confine scrollback
        # to the scroll region, so pinning costs you normal terminal scrolling.
        # Opt in with EMAIL_GAME_PIN_LINKS=1 if you want the fixed header and
        # accept that tradeoff; otherwise the links are re-printed each new game.
        self._pin_links: bool = os.getenv("EMAIL_GAME_PIN_LINKS", "").strip().lower() in ("1", "true", "yes", "on")
        self._pinned: bool = False

        # Async task for the WebSocket listener
        self._ws_task: Optional[asyncio.Task] = None
        
        # Development mode features
        if self.dev_mode:
            self._setup_dev_features()
        
        # Register and join queue immediately (raises on failure)
        # Delay heavy WebSocket / LLM startup until server interaction works.
        self._register_with_server(initial=True)
        self._join_queue()
        
        # LLM driver setup
        default_prompt_file = Path(__file__).resolve().parent.parent / "docs" / "agent_prompt.md"
        resolved_prompt_file = Path(prompt_file) if prompt_file else default_prompt_file
        try:
            system_prompt = resolved_prompt_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            print(f"[{self.agent_id}] WARNING: Prompt file not found: {resolved_prompt_file}, using default")
            try:
                system_prompt = default_prompt_file.read_text(encoding="utf-8")
            except FileNotFoundError:
                system_prompt = "The Email Game agent system prompt (file not found)"

        # Add signing tools to LLM driver
        self.driver = LLMDriver(
            agent_id=self.agent_id,
            system_prompt=system_prompt,
            send_email_callable=self.send_message,
            sign_message_callable=self.sign_message,
            sign_and_respond_callable=self.sign_and_respond,
            submit_signature_callable=self.submit_signature,
            model=model or OPENAI_MODEL,
            temperature=temperature,
            verbose=False,
        )

        # Transparency: show which model and LLM endpoint this agent will use, so
        # in a hosted event it's obvious whether you're on the organizers' gateway
        # (with your issued, budgeted key) vs. OpenAI directly. Never prints the key.
        _endpoint = os.getenv("OPENAI_BASE_URL") or "OpenAI default (api.openai.com)"
        print(f"[{self.agent_id}] LLM: model={model or OPENAI_MODEL}  endpoint={_endpoint}")
        self._print_budget()

        # Set by the SIGINT handler: "stop when this game ends" rather than now.
        self._stop_after_game = False

        # Deduplication – keep track of message_ids we have already processed so
        # reconnect-triggered backlog replays do not feed the same email to the
        # LLM multiple times.
        self._seen_message_ids: set[str] = set()

        # Prevent re-submitting the same signed payload across rounds.
        # Key: (signer, signed_for, original_message)
        self._submitted_signature_keys: set[tuple] = set()
    
    def _print_budget(self) -> None:
        """Show what is left of the issued key's budget, when there is one.

        Competitors had no way to see this: the first sign that a $30 key was
        spent was the agent failing mid-afternoon, which is both unrecoverable
        and indistinguishable from a bug. Asking the gateway costs one cheap
        call at startup and after each game. Silent on any error, and silent
        entirely when not running through a gateway - a personal OpenAI key has
        no budget to report.
        """
        base = (os.getenv("OPENAI_BASE_URL") or "").strip().rstrip("/")
        key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not base or not key:
            return
        try:
            r = requests.get(f"{base}/key/info",
                             headers={"Authorization": f"Bearer {key}"}, timeout=4)
            body = r.json() or {}
            # A blocked or invalid key is the one failure worth interrupting for:
            # the agent connects and queues normally (entry gating checks the key's
            # hash, not the gateway), then every LLM call fails with nothing on
            # screen to explain why. Say it here instead.
            if r.status_code in (401, 403):
                msg = ((body.get("error") or {}).get("message") or "").strip()
                print(f"[{self.agent_id}] ⚠️  Your LLM key was rejected by the gateway"
                      f"{': ' + msg if msg else ''}\n"
                      f"[{self.agent_id}]     You can still join games, but every LLM call "
                      f"will fail. Ask the organizers before playing.")
                return
            info = body.get("info") or {}
            spend, cap = info.get("spend"), info.get("max_budget")
            if spend is None:
                return
            if cap:
                left = max(0.0, float(cap) - float(spend))
                pct = 100.0 * float(spend) / float(cap)
                warn = "  <- running low" if left < 0.15 * float(cap) else ""
                print(f"[{self.agent_id}] Budget: ${left:.2f} left "
                      f"of ${float(cap):.2f} ({pct:.0f}% used){warn}")
            else:
                print(f"[{self.agent_id}] Budget: ${float(spend):.2f} spent")
        except Exception:
            pass  # never let a status line stop an agent from playing

    def _install_sigint_handler(self) -> None:
        """Make the first Ctrl+C mean "stop when this game ends", not "quit now".

        Quitting mid-game forfeits it and hands the other three a no-contest, and
        the only safe window was the ~15s between matches - so improving your
        agent meant racing a timer, all day, every iteration. Now the press is
        always safe: it takes effect at the next game boundary. A second press
        quits immediately, for anyone who means it.
        """
        def _on_sigint(_signum, _frame):
            if not self.in_game or self._stop_after_game:
                signal.signal(signal.SIGINT, signal.SIG_DFL)  # let the next one through
                raise KeyboardInterrupt
            self._stop_after_game = True
            print(f"\n[{self.agent_id}] Will stop when this game ends - it still counts, "
                  f"no forfeit. Press Ctrl+C again to quit now (forfeits this game).")
        try:
            signal.signal(signal.SIGINT, _on_sigint)
        except (ValueError, OSError):
            pass  # not the main thread: leave the default handler alone

    def register_with_moderator(self) -> bool:
        """Register this agent with the moderator"""
        return True
    
    # ------------------------------------------------------------------
    # Networking helpers – registration / queue / JWT handling
    # ------------------------------------------------------------------

    def _register_with_server(self, force: bool = False, initial: bool = False) -> None:
        """Register this agent with the email server and cache the JWT.

        force=True re-registers even if the cached token still looks valid - used
        on reconnect, since a server restart wipes the server's record of this
        agent (its public key) and we need a fresh registration + token.
        initial=True (first registration at startup) turns an unreachable-server
        error into a short, friendly message + clean exit instead of a traceback.
        """

        if not force and self._jwt_token and (self._jwt_expiry - time.time() > 120):
            return  # still valid

        url = f"{self.email_server_url}/register_agent"
        # The issued key doubles as the entry credential during the competition:
        # the server (when gating is on) checks it matches this agent_id, so no
        # one can register under someone else's name. Harmless when gating is off.
        payload = {"agent_id": self.agent_id, "rsa_public_key": self._public_key_pem,
                   "entry_key": os.getenv("OPENAI_API_KEY")}
        try:
            r = requests.post(url, json=payload, timeout=10)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if initial:
                print(
                    f"\n[{self.agent_id}] Could not reach the game server at "
                    f"{self.email_server_url}.\n"
                    f"  - Is the server running? During the event use the host's "
                    f"--server URL; for local testing start one first.\n"
                    f"  - Then run this command again.\n"
                )
                raise SystemExit(1)
            raise

        if r.status_code in (409, 403, 401, 400):
            # Fatal registration rejections - surface the server's competitor-facing
            # message and stop (retrying won't help): 409 name taken by another key,
            # 403 off-roster / key does not match this name, 401 missing issued key,
            # 400 reserved name.
            try:
                detail = r.json().get("detail", f"Registration rejected ({r.status_code})")
            except Exception:
                detail = f"Registration rejected ({r.status_code})"
            print(f"\n[{self.agent_id}] Could not join: {detail}\n")
            raise SystemExit(1)

        r.raise_for_status()
        data = r.json()
        self._jwt_token = data["token"]
        # Read-only token for the watch link (falls back to None on older servers).
        self._view_token = data.get("view_token")

        # Decode to get expiry (without verifying signature – we only need 'exp')
        try:
            payload = jwt.decode(self._jwt_token, options={"verify_signature": False}, algorithms=["HS256"])
            self._jwt_expiry = float(payload.get("exp", 0))
        except Exception:
            self._jwt_expiry = time.time() + 1800  # fallback 30m

        # Surface ready-to-click links so the human can watch this agent's games
        # live (own perspective only) and open the leaderboard. The token is this
        # agent's login - only shown locally in its own console.
        self._print_watch_banner()

    @staticmethod
    def _osc8(url: str, label: str) -> str:
        """A terminal hyperlink (OSC 8): shows clickable `label`, hides the URL.

        Supported by modern terminals (Windows Terminal, VS Code, iTerm2,
        GNOME Terminal). On terminals that don't support it, the escape codes are
        ignored and only `label` shows - so we also keep a plain fallback line.
        """
        esc = "\033"
        return f"{esc}]8;;{url}{esc}\\{label}{esc}]8;;{esc}\\"

    def _watch_links(self):
        """Return (watch_url, board_url, watch_link, board_link) or None."""
        link_token = self._view_token or self._jwt_token
        if not link_token:
            return None
        from urllib.parse import quote
        base = self.email_server_url
        watch_url = (f"{base}/watch?agent={quote(self.agent_id)}"
                     f"&token={quote(link_token)}")
        # Point the leaderboard link at the board this competitor is in: the
        # build-week testing board while the competition is only scheduled,
        # otherwise the main board. Best-effort; defaults to the main board.
        board_path = "/leaderboard"
        try:
            nav = requests.get(f"{base}/queue_status", timeout=3).json().get("nav_board")
            if nav == "build":
                board_path = "/leaderboard/testing"
        except Exception:
            pass
        board_url = f"{base}{board_path}"
        return (
            watch_url, board_url,
            self._osc8(watch_url, "▶ Watch your match"),
            self._osc8(board_url, "🏆 View leaderboard"),
        )

    def _print_watch_banner(self) -> None:
        """Show the Watch + Leaderboard links. With EMAIL_GAME_PIN_LINKS=1 on a
        real terminal, pin them to a stationary top line; otherwise print a banner
        (re-printed each new game so it stays near the latest output)."""
        # EMAIL_GAME_NO_LINKS suppresses the per-agent banner. playtest sets it so
        # the 4 agents sharing one console don't each spam links; playtest prints
        # one consolidated "Watch & review" block instead.
        if os.getenv("EMAIL_GAME_NO_LINKS", "").strip().lower() in ("1", "true", "yes", "on"):
            return
        links = self._watch_links()
        if not links:
            return
        if self._pin_links and self._render_pinned_header(links):
            return
        watch_url, board_url, watch_link, board_link = links
        bar = "─" * 60
        try:
            # No [agent] prefix here: this is setup info, not an in-game action.
            # Each clickable link sits above its full URL (a fallback for terminals
            # without hyperlink support), with a blank line between the two so the
            # long tokenised watch URL doesn't read as a wall of text.
            print(f"\n{bar}")
            print(f"  {watch_link}")
            print(f"      {watch_url}")
            print()
            print(f"  {board_link}")
            print(f"      {board_url}")
            print(f"{bar}\n")
        except Exception:
            pass
        self._open_watch_page(watch_url)

    def _open_watch_page(self, watch_url: str) -> None:
        """Open the watch page in a browser, once per run.

        Printing the link assumes a human is reading the terminal. Increasingly
        nobody is: competitors hand setup to a coding agent, which runs the
        commands and reports back a summary - so the one link that shows them
        their own agent playing scrolls past unseen, and the game reads as log
        spam rather than something to watch.

        Opening it once removes that dependency. Failures are ignored (headless
        boxes, containers and SSH sessions have no browser), and it never
        repeats - reconnects and new matches must not spawn tabs.
        """
        if getattr(self, "_browser_opened", False):
            return
        self._browser_opened = True
        if os.getenv("EMAIL_GAME_NO_BROWSER", "").strip().lower() in ("1", "true", "yes", "on"):
            return
        try:
            import webbrowser
            if webbrowser.open(watch_url):
                print(f"[{self.agent_id}] opened your live match view in the browser "
                      f"(set EMAIL_GAME_NO_BROWSER=1 to stop this)")
        except Exception:
            pass                      # no browser available; the printed link still works

    def _render_pinned_header(self, links) -> bool:
        """Pin the links to a stationary 3-row header via an ANSI scroll region so
        they stay visible while the log scrolls below. Returns True if applied.

        Redraw it freely (it is cheap and idempotent): re-asserting the region and
        repainting the header each batch keeps it intact even if stray output or a
        window resize disturbed it. Restored on exit by _restore_scroll_region.
        Relies on ANSI scroll-region support; gated by self._pin_links."""
        try:
            import shutil
            size = shutil.get_terminal_size((80, 24))
            cols, rows = size.columns, max(8, size.lines)
            watch_url, board_url, watch_link, board_link = links
            out = sys.stdout
            sep = "─" * min(cols, 100)
            line1 = f"  {watch_link}      {board_link}"
            line2 = f"  watch: {watch_url}    leaderboard: {board_url}"
            out.write("\0337")                       # save cursor
            out.write("\033[1;1H\033[2K" + line1)    # row 1: clickable links
            out.write("\033[2;1H\033[2K" + line2)    # row 2: full URLs (fallback)
            out.write("\033[3;1H\033[2K" + sep)      # row 3: separator
            out.write(f"\033[4;{rows}r")             # reserve rows 4..bottom for the log
            out.write("\0338")                       # restore cursor
            if not self._pinned:
                out.write(f"\033[{rows};1H")         # first time: park the log at the bottom
                self._pinned = True
            out.flush()
            return True
        except Exception:
            return False

    def _redraw_pin(self) -> None:
        """Repaint the pinned header if pinning is on (no-op otherwise)."""
        if not self._pin_links:
            return
        links = self._watch_links()
        if links:
            self._render_pinned_header(links)

    def _restore_scroll_region(self) -> None:
        """Undo the scroll region so the terminal isn't left scroll-locked on exit."""
        if not self._pinned:
            return
        try:
            sys.stdout.write("\033[r")     # reset scroll region to full screen
            sys.stdout.write("\033[2;1H")  # move below the (now inert) header
            sys.stdout.flush()
        except Exception:
            pass
        finally:
            self._pinned = False

    def _join_queue(self) -> int:
        """Join the waiting_queue; returns new queue length."""
        # Ensure we have a valid token
        if not self._jwt_token:
            self._register_with_server()

        hdr = {"Authorization": f"Bearer {self._jwt_token}"}
        r = requests.post(f"{self.email_server_url}/join_queue", json={"agent_id": self.agent_id}, headers=hdr, timeout=10)

        # If we get 401, try to re-register and retry once
        if r.status_code == 401:
            self._jwt_token = None
            self._jwt_expiry = 0.0
            self._register_with_server()
            hdr = {"Authorization": f"Bearer {self._jwt_token}"}
            r = requests.post(f"{self.email_server_url}/join_queue", json={"agent_id": self.agent_id}, headers=hdr, timeout=10)
        
        if r.status_code not in (200, 201):
            raise RuntimeError(f"join_queue failed: {r.status_code} {r.text}")
        pos = r.json().get("position", -1)
        print(f"[{self.agent_id}] ⏳ Joined matchmaking queue (position {pos}) - waiting for a match...")
        return pos

    def _safe_rejoin_queue(self) -> None:
        """Rejoin the ladder queue after reconnecting; tolerate already-queued.

        Unlike _join_queue this never raises - a transient reconnect or a server
        restart should quietly put us back in the queue without crashing the agent.
        """
        try:
            hdr = {"Authorization": f"Bearer {self._jwt_token}"}
            r = requests.post(f"{self.email_server_url}/join_queue",
                              json={"agent_id": self.agent_id}, headers=hdr, timeout=10)
            if r.status_code in (200, 201):
                print(f"[{self.agent_id}] Rejoined the ladder queue")
            elif r.status_code == 409:
                pass  # already queued or mid-game - nothing to do
            else:
                print(f"[{self.agent_id}] Rejoin queue returned {r.status_code}")
        except Exception as e:
            print(f"[{self.agent_id}] Rejoin queue failed (will retry on next reconnect): {e}")

    def _auth_headers(self) -> Dict[str, str]:
        """Return Bearer-token headers; refresh if token is close to expiry."""
        if time.time() > self._jwt_expiry - 60:
            # Very close to expiry; attempt re-register (simple refresh placeholder)
            self._register_with_server()
        return {"Authorization": f"Bearer {self._jwt_token}"}

    # ------------------------------------------------------------------
    # Public API wrappers (polling, sending)
    # ------------------------------------------------------------------

    def poll_messages(self) -> List[Dict]:
        """Poll for new messages from the email server"""
        try:
            response = requests.get(
                f"{self.email_server_url}/get_messages/{self.agent_id}",
                headers=self._auth_headers(),
            )
            
            if response.status_code == 200:
                data = response.json()
                if data["success"]:
                    return data["messages"]
            
            return []
            
        except Exception as e:
            print(f"Error polling messages: {e}")
            return []
    
    def send_message(self, to_agent: str, subject: str, body: str) -> Dict:
        """Send a message via the email server API"""
        try:
            # Sender is derived from JWT token, not specified in payload
            message_data = {
                "to": to_agent,
                "subject": subject,
                "body": body,
            }
            
            response = requests.post(
                f"{self.email_server_url}/send_message",
                json=message_data,
                headers=self._auth_headers(),
            )
            
            if response.status_code == 200:
                data = response.json()
                if data["success"]:
                    self.messages_sent += 1
                    return {"success": True, "message_id": data["message_id"]}
            
            return {"success": False, "error": "Failed to send message"}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # -----------------------------
    # WebSocket real-time listener
    # -----------------------------

    async def _ws_loop(self):
        # Shared queue between producer (WebSocket) and consumer (LLM processor).
        # The producer never blocks on LLM calls, so messages are never dropped
        # or delayed due to a long-running LLM response.
        self._message_queue: asyncio.Queue = asyncio.Queue()

        loop = asyncio.get_running_loop()
        consumer_task = loop.create_task(self._llm_consumer())

        first_connect = True
        while self.running:
            try:
                # On any reconnect (network blip or full server restart) refresh
                # registration + token, then rebuild the URL with the new token.
                # A server restart wipes the server's record of us, so we must
                # re-register to restore our public key and get a valid token.
                if not first_connect:
                    self._register_with_server(force=True)

                ws_base = self.email_server_url.replace("http://", "ws://").replace("https://", "wss://")
                uri = f"{ws_base}/ws/{self.agent_id}?token={self._jwt_token}"

                # Verify wss:// against certifi's CA bundle, not the OS store.
                # `requests` already uses certifi, so HTTP worked while the
                # WebSocket failed with "certificate has expired" on machines
                # carrying a stale root (the DST Root CA X3 expiry bites a lot of
                # older Windows/Python installs). The agent then silently fell
                # back to polling: games still ran, but live delivery was gone
                # and the player never showed as online.
                ssl_ctx = ssl.create_default_context(cafile=certifi.where())                     if uri.startswith("wss://") else None

                async with websockets.connect(uri, ssl=ssl_ctx) as ws:
                    if first_connect:
                        print(f"[{self.agent_id}] connected, in the ladder queue, waiting for a game...")
                    else:
                        print(f"[{self.agent_id}] reconnected, rejoining the ladder queue...")

                    # After a reconnect, get back into the ladder queue (the
                    # first connect already joined during startup).
                    if not first_connect:
                        self._safe_rejoin_queue()
                    first_connect = False

                    # Catch up on any messages that arrived while offline.
                    backlog = self.poll_messages()
                    for msg in backlog:
                        await self._message_queue.put(msg)

                    while self.running:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                            message = json.loads(raw) if isinstance(raw, str) else raw
                            await self._message_queue.put(message)
                        except asyncio.TimeoutError:
                            self._check_inactivity()
                        except websockets.exceptions.ConnectionClosed:
                            print(f"[{self.agent_id}] connection dropped; reconnecting...")
                            break
            except Exception as e:
                print(f"[{self.agent_id}] connection error ({e}); retrying...")
                if self.running:
                    await asyncio.sleep(2)

        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

    async def _llm_consumer(self):
        """Drain the message queue and process batches with a single LLM call.

        Waits for at least one message, then collects any additional messages
        that arrived during the previous LLM call before invoking the LLM.
        This ensures the model always sees everything in its inbox before deciding
        what to do next.
        """
        while self.running:
            try:
                # Block until a message arrives, checking inactivity every 5s.
                try:
                    first = await asyncio.wait_for(self._message_queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._check_inactivity()
                    continue

                batch = [first]

                # Drain any additional messages already in the queue (arrived
                # while the LLM was processing the previous batch).
                while not self._message_queue.empty():
                    try:
                        batch.append(self._message_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                # Run the (blocking) LLM turn in a worker thread so the event
                # loop keeps running while the model thinks. Otherwise a long or
                # stalled LLM call freezes this coroutine, the WebSocket can't
                # answer the server, and the agent gets dropped from its match.
                # The batch handler only does sync HTTP (requests) + signing and
                # never touches asyncio state, so this is safe. New mail keeps
                # arriving into _message_queue via _ws_loop while we wait.
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._handle_message_batch, batch)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[{self.agent_id}] LLM consumer error: {e}")

    # -----------------------------
    # Helpers
    # -----------------------------

    def _dedup_message(self, message: Dict) -> bool:
        """Return True if the message is new (not seen before), False if duplicate."""
        msg_id = message.get("message_id")
        if msg_id and msg_id in self._seen_message_ids:
            print(f"[{self.agent_id}] Duplicate message {msg_id} - skipping")
            return False
        if msg_id:
            self._seen_message_ids.add(msg_id)
        return True

    def _handle_message_batch(self, messages: List[Dict]) -> None:
        """Filter duplicates, update state, then make a single LLM call for the batch."""
        self._redraw_pin()  # keep the pinned links intact as the log scrolls
        fresh = []
        for message in messages:
            if not self._dedup_message(message):
                continue

            from_agent = message.get('from', message.get('from_agent', ''))
            subject = message.get('subject', 'No Subject')
            body = message.get('body', '')

            if from_agent == self.moderator_agent:
                low = subject.lower()
                # Game-over notice: leave the in-game state and wait for the next
                # match. Informational only - no action to take, so don't feed it
                # to the LLM.
                if "game over" in low or "final result" in low:
                    self.in_game = False
                    self.current_round = 0
                    self._print_budget()
                    # Ctrl+C was pressed mid-game: this is the boundary we
                    # promised to stop at, so the game just played still counts.
                    if self._stop_after_game:
                        print(f"\n[{self.agent_id}] Game finished and counted - stopping now, "
                              f"as requested. Edit your agent and relaunch the same command.")
                        self.stop()
                        continue
                    buf = " The next match starts shortly"
                    try:
                        cd = requests.get(f"{self.email_server_url}/queue_status",
                                          timeout=3).json().get("requeue_cooldown_sec")
                        if cd:
                            buf = f" The next match starts in about {int(cd)}s"
                    except Exception:
                        pass
                    print(f"\n[{self.agent_id}] 🏁 Game over - between matches now (not in a game)."
                          f"{buf}. Ctrl+C is safe at any time - it stops after the "
                          f"current game, never mid-game.")
                    continue

                # Drive the round number from the moderator's stated round, never
                # a self-incremented counter (which drifts after a reconnect and
                # produces the bogus "Round 46" / "cannot sign - round N" labels).
                m = re.search(r'\*\*ROUND\s+(\d+)\*\*', body, re.IGNORECASE)
                if not m:
                    # Moderator mail with NO round marker (scoring notices, ad
                    # hoc host mail): informational. The LLM still reads it,
                    # but it must never advance the round counter and never
                    # start or reset a game - both of which the instruction
                    # path below would do.
                    print(f"[{self.agent_id}] received from moderator: {subject} (Round {self.current_round})")
                    self.last_message_time = datetime.now()
                    fresh.append(message)
                    continue
                rnum = int(m.group(1))

                # Start of a new game (round 1, or first instruction seen while
                # not in a game). Reset the LLM context so memory from previous
                # games can't accumulate - that balloons cost/latency and lets
                # stale cross-game history corrupt reasoning. Memory is kept
                # WITHIN a game (only the game boundary resets).
                if rnum <= 1 or not self.in_game:
                    self.in_game = True
                    print(f"\n[{self.agent_id}] ✅ Match found - game starting! "
                          f"(Ctrl+C now = stop cleanly once this game ends)")
                    self.driver.message_log.clear()
                    print(f"[{self.agent_id}] (new game - reset LLM context)")
                    self._print_watch_banner()
                    try:
                        self.on_new_game()
                    except Exception as e:
                        print(f"[{self.agent_id}] on_new_game() error: {e}")

                self.current_round = rnum
                print(f"[{self.agent_id}] 🎮 IN GAME - Round {rnum} | {subject}")
                self.can_send_reminder = True
            else:
                # Not in a game: ignore stray/backlog peer mail instead of burning
                # an LLM turn on it (the server already scopes delivery to the
                # current game; this is the agent-side backstop).
                if not self.in_game:
                    print(f"[{self.agent_id}] (ignored mail from {from_agent}: not in a game)")
                    continue
                print(f"[{self.agent_id}] received from {from_agent}: {subject} (Round {self.current_round})")

            self.last_message_time = datetime.now()
            self.instructions_processed += 1
            fresh.append(message)

        if not fresh:
            return

        try:
            self.on_message_batch(fresh)
            self._maybe_report_usage()
        except Exception as e:
            print(f"[{self.agent_id}] Error handling messages: {e}")
            import traceback
            traceback.print_exc()

    def _maybe_report_usage(self) -> None:
        """Push cumulative LLM usage to the server (throttled). Powers the
        local sandbox's spend display; the hosted game uses gateway numbers
        instead, so there this is simply ignored. Best-effort only."""
        try:
            totals = self.driver.usage_totals()
        except Exception:
            return
        if not totals or totals.get("est_cost", 0) <= getattr(self, "_usage_reported", -1):
            return
        now = time.time()
        if now - getattr(self, "_usage_reported_at", 0) < 15:
            return
        self._usage_reported_at = now
        self._usage_reported = totals.get("est_cost", 0)
        try:
            requests.post(f"{self.email_server_url}/report_usage",
                          json={"agent_id": self.agent_id, "model": self.driver.model, **totals},
                          headers={"Authorization": f"Bearer {self._jwt_token}"} if self._jwt_token else {},
                          timeout=5)
        except Exception:
            pass

    def on_new_game(self) -> None:
        """Hook called once at the start of each new game (the round-1
        instruction), before that batch is processed.

        The built-in LLM context (self.driver) is already reset for you. Override
        this to reset any state YOUR agent tracks across rounds (counters,
        remembered messages, your own conversation history, etc.) so it does not
        leak between games in the live ladder. Default: no-op.
        """
        pass

    def on_message_batch(self, messages: List[Dict]) -> None:
        """
        Hook called with each batch of fresh incoming messages.

        Override this in a subclass to implement custom agent logic.
        The default behavior forwards all messages to the LLM driver.

        Each message dict has keys: from, to, subject, body, message_id, timestamp.
        Use self.send_message(), self.sign_message(), self.sign_and_respond(),
        and self.submit_signature() to take action.
        """
        self.driver.on_emails(messages)

    def _handle_incoming_message(self, message: Dict) -> None:
        """Legacy single-message entry point (used by inactivity reminder)."""
        self._handle_message_batch([message])
    
    def _send_inactivity_reminder(self) -> None:
        """Send an inactivity reminder to help agent complete pending actions"""
        try:
            
            # Mark that we've sent the reminder (prevents duplicates this round)
            self.can_send_reminder = False
            
            # Create reminder message
            reminder_content = {
                "message_id": f"reminder_{self.agent_id}_{datetime.now().isoformat()}",
                "from": "system_reminder",
                "to": self.agent_id,
                "subject": "⏰ Action Completion Reminder",
                "body": (
                    "REMINDER: Ensure you have completed all required actions for this round.\n\n"
                    "Check if you have:\n"
                    "- Submitted ALL signatures you received (missing submissions cost points)\n"
                    "- Responded to ALL signature requests you're authorized for\n"
                    "- Completed ALL tasks from the moderator's instructions\n\n"
                    "Remember your system prompt requirements:\n"
                    "- ALWAYS use function calls when taking action\n"
                    "- NEVER respond with markdown code blocks\n"
                    "- Submit every signature you receive immediately\n\n"
                    "Review your recent messages and ensure no actions are incomplete."
                ),
                "timestamp": datetime.now().isoformat(),
                "status": "sent"
            }
            
            # Send reminder through the message handling system
            self._handle_incoming_message(reminder_content)
            
        except Exception:
            pass
    
    def _check_inactivity(self) -> None:
        """Send an inactivity reminder if the agent has been quiet too long."""
        try:
            if not self.can_send_reminder:
                return
            time_since_last = (datetime.now() - self.last_message_time).total_seconds()
            if time_since_last >= self.inactivity_threshold_seconds:
                self._send_inactivity_reminder()
        except Exception:
            pass
    
    # -----------------------------
    # Public control API
    # -----------------------------

    async def run(self) -> None:
        """Run the agent until `stop()` is called or the process exits."""
        if self.running:
            return

        self.running = True
        self._install_sigint_handler()

        # Launch WebSocket listener task
        loop = asyncio.get_running_loop()
        self._ws_task = loop.create_task(self._ws_loop())

        try:
            # Wait for the WebSocket task to finish (runs until stop())
            await self._ws_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            self.running = False
            self._restore_scroll_region()

    def run_sync(self):
        """Convenience wrapper to run the async agent with `asyncio.run`.

        On Windows, prefer the Selector event loop: the default Proactor loop
        emits a harmless-but-alarming "Exception ignored in __del__ ... NoneType
        has no attribute 'close'" traceback when interrupted. The agent only does
        network I/O (no subprocess pipes), so Selector is fully compatible. The
        policy is process-wide, so the graceful-disconnect loop the entry points
        run on Ctrl+C also avoids the noise. KeyboardInterrupt is left to
        propagate so those handlers can disconnect gracefully."""
        if sys.platform.startswith("win"):
            try:
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            except Exception:
                pass
        asyncio.run(self.run())

    def stop(self) -> None:
        """Stop the agent gracefully"""
        self.running = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()

        self._restore_scroll_region()  # don't leave the terminal scroll-locked

        # Save transcript when stopping
        self.save_transcript()
    
    def _load_rsa_keys(self) -> tuple:
        """Load RSA keys for this agent from sample_agents.json"""
        try:
            # Load agent data from sample_agents.json
            agents_file = Path(__file__).resolve().parents[1] / "data" / "sample_agents.json"
            with open(agents_file, 'r') as f:
                data = json.load(f)
            
            # Find this agent's data
            agent_data = None
            for agent in data['agents']:
                if agent['id'] == self.agent_id:
                    agent_data = agent
                    break
            
            if not agent_data:
                raise ValueError(f"Agent {self.agent_id} not found in sample_agents.json")
            
            # Load private key
            private_key_pem = agent_data['rsa_private_key']
            private_key = serialization.load_pem_private_key(
                private_key_pem.encode(),
                password=None
            )
            
            # Load public key  
            public_key_pem = agent_data['rsa_public_key']
            public_key = serialization.load_pem_public_key(
                public_key_pem.encode()
            )
            
            return private_key, public_key
            
        except Exception:
            # Not a built-in sample agent (i.e. a real user-chosen agent_id).
            # Persist a stable identity key locally so reconnects keep the same
            # public key - this preserves the agent's name-lock and leaderboard
            # identity across restarts and competitions.
            return self._load_or_create_local_key()

    def _load_or_create_local_key(self) -> tuple:
        """Load this agent's persistent RSA key from ~/.email_game/keys, or create it."""
        key_dir = Path.home() / ".email_game" / "keys"
        key_dir.mkdir(parents=True, exist_ok=True)
        key_path = key_dir / f"{self.agent_id}.pem"

        if key_path.exists():
            private_key = serialization.load_pem_private_key(
                key_path.read_bytes(), password=None
            )
            print(f"[{self.agent_id}] Loaded persistent identity key from {key_path}")
        else:
            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            key_path.write_bytes(private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ))
            print(f"[{self.agent_id}] Created persistent identity key at {key_path}")

        return private_key, private_key.public_key()
    
    def sign_message(self, message: str, for_agent: str) -> Dict[str, Any]:
        """Sign a message for another agent using RSA"""
        
        timestamp = datetime.now().isoformat()
        
        # Create message to sign
        sign_data = f"{message}|{self.agent_id}|{for_agent}|{timestamp}"
        
        try:
            # Generate RSA signature
            signature_bytes = self.rsa_private_key.sign(
                sign_data.encode(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
            
            # Convert to base64 for JSON serialization
            signature_b64 = base64.b64encode(signature_bytes).decode('utf-8')
            
            signed_message = {
                "original_message": message,
                "signature": signature_b64,
                "signer": self.agent_id,
                "signed_for": for_agent,
                "timestamp": timestamp,
                "signature_type": "rsa_pss_sha256"
            }
            
            return signed_message
            
        except Exception as e:
            return {"error": str(e)}
    
    def sign_and_respond(self, to_agent: str, message_to_sign: str, response_body: str, subject: str = "Signed Message") -> Dict[str, Any]:
        """Sign a message and send it back to the requesting agent in a single operation"""
        
        try:
            # 1. Create the RSA signature
            timestamp = datetime.now().isoformat()
            sign_data = f"{message_to_sign}|{self.agent_id}|{to_agent}|{timestamp}"
            
            # Generate RSA signature
            signature_bytes = self.rsa_private_key.sign(
                sign_data.encode(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
            
            # Convert to base64 for JSON serialization
            signature_b64 = base64.b64encode(signature_bytes).decode('utf-8')
            
            signed_message = {
                "original_message": message_to_sign,
                "signature": signature_b64,
                "signer": self.agent_id,
                "signed_for": to_agent,
                "timestamp": timestamp,
                "signature_type": "rsa_pss_sha256"
            }
            
            
            # 2. Prepare email body with signature appended
            signature_json = json.dumps(signed_message, separators=(',', ':'))
            full_body = f"{response_body}\n\nSIGNED_MESSAGE_JSON:{signature_json}"
            
            
            # 3. Send the email
            email_result = self.send_message(to_agent, subject, full_body)
            
            if email_result.get("success"):
                return {
                    "success": True,
                    "message_id": email_result.get("message_id"),
                    "signed_message": signed_message,
                    "to_agent": to_agent
                }
            else:
                return {"success": False, "error": "Failed to send email"}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def extract_signed_message_from_email(self, email_body: str) -> Optional[Dict[str, Any]]:
        """Extract signed message JSON from an email body"""
        try:
            # Look for the signature JSON marker
            marker = "SIGNED_MESSAGE_JSON:"
            if marker in email_body:
                # Extract everything after the marker
                json_part = email_body.split(marker, 1)[1].strip()
                # Parse the JSON
                signed_message = json.loads(json_part)
                return signed_message
            else:
                return None
        except Exception as e:
            return None
    
    # Note: Signature verification is now handled externally using public keys
    # Agents only sign messages, they don't verify them
    
    def submit_signature(self, signed_message: Dict[str, Any]) -> Dict[str, Any]:
        """Submit a received signature to the moderator via email"""
        try:
            # Deduplicate: prevent re-submitting a signature from a previous round
            key = (
                signed_message.get("signer"),
                signed_message.get("signed_for"),
                signed_message.get("original_message"),
            )
            if key in self._submitted_signature_keys:
                print(f"[{self.agent_id}] Duplicate signature submission blocked: {key[0]}->{key[1]}")
                return {"success": False, "error": "Signature already submitted"}
            self._submitted_signature_keys.add(key)

            # Create submission data
            submission_data = {
                "submission_type": "signature",
                "submitter": self.agent_id,
                "signatures": [signed_message]
            }
            
            # Send as email to moderator
            result = self.send_message(
                to_agent=self.moderator_agent,
                subject=f"Signature Submission - {self.agent_id}",
                body=json.dumps(submission_data, indent=2)
            )
            
            if result.get("success"):
                return {"success": True, "message_id": result.get("message_id")}
            else:
                return {"success": False, "error": result.get("error", "Unknown error")}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_status(self) -> Dict[str, Any]:
        """Get current agent status"""
        return {
            "agent_id": self.agent_id,
            "username": self.username,
            "running": self.running,
            "instructions_processed": self.instructions_processed,
            "messages_sent": self.messages_sent,
            "signatures_received": len(self.received_signatures),
            "current_instruction": self.current_instruction
        }
    
    def save_transcript(self) -> None:
        """Save the complete LLM conversation transcript to a file"""
        try:
            # Always save transcripts inside repo-root /transcripts (independent of cwd)
            project_root = Path(__file__).resolve().parents[1]
            transcript_dir = project_root / "transcripts"
            transcript_dir.mkdir(exist_ok=True)
            
            # Generate timestamp for filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.agent_id}_{timestamp}.json"
            filepath = transcript_dir / filename
            
            # Prepare transcript data
            transcript_data = {
                "agent_id": self.agent_id,
                "username": self.username,
                "timestamp": datetime.now().isoformat(),
                "stats": self.get_status(),
                "system_prompt": self.driver.system_prompt,
                "message_log": self.driver.message_log.copy(),
                "total_messages": len(self.driver.message_log)
            }
            
            # Save to file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(transcript_data, f, indent=2, ensure_ascii=False)
            
            
        except Exception:
            pass
    
    def print_transcript_summary(self) -> None:
        """Print a summary of the LLM conversation"""
        print(f"\n=== {self.agent_id.upper()} TRANSCRIPT SUMMARY ===")
        print(f"Total LLM messages: {len(self.driver.message_log)}")
        print(f"Instructions processed: {self.instructions_processed}")
        print(f"Messages sent: {self.messages_sent}")
        print(f"\nConversation flow:")
        
        for i, msg in enumerate(self.driver.message_log):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            tool_call = msg.get("tool_call")
            
            if role == "user":
                # Parse email data
                try:
                    email_data = json.loads(content)
                    from_agent = email_data.get("from", "unknown")
                    subject = email_data.get("subject", "")
                    print(f"  {i+1}. 📨 RECEIVED EMAIL from {from_agent}: {subject}")
                except:
                    print(f"  {i+1}. 📨 RECEIVED: {content[:50]}...")
                    
            elif role == "assistant":
                if tool_call:
                    print(f"  {i+1}. 🤖 LLM RESPONSE with tool calls")
                else:
                    print(f"  {i+1}. 🤖 LLM RESPONSE: {content[:50]}...")
                    
            elif role == "function":
                func_name = msg.get("name", "unknown")
                print(f"  {i+1}. 🔧 TOOL RESULT ({func_name})")
        
        print(f"=== END {self.agent_id.upper()} TRANSCRIPT ===\n")
    
    def clear_transcript(self) -> None:
        """Clear the LLM conversation transcript for a new round"""
        if self.driver:
            self.driver.message_log.clear()
        
        # Reset counters for new round
        self.instructions_processed = 0
        self.messages_sent = 0
        self.current_instruction = None
        self.can_send_reminder = False
        self.last_message_time = datetime.now()
    
    async def disconnect_gracefully(self):
        """Leave queue and close connections before shutdown."""
        try:
            # Leave queue first
            hdr = self._auth_headers()
            response = requests.post(
                f"{self.email_server_url}/leave_queue",
                headers=hdr,
                timeout=5
            )
            print(f"[{self.agent_id}] Left queue: {response.status_code}")
        except Exception as e:
            print(f"[{self.agent_id}] Error leaving queue: {e}")
        
        # Cancel WebSocket task if running
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        
        print(f"[{self.agent_id}] Disconnected gracefully")
    
    def _setup_dev_features(self):
        """Enable development-friendly features."""
        print(f"[{self.agent_id}] 🛠️  Development mode enabled")
        
        # More verbose logging
        if not hasattr(self, '_original_print'):
            self._original_print = print
            
        # Longer JWT expiry for development
        self._dev_jwt_expiry_boost = 3600  # 1 hour extra
        
        # Auto-reconnect settings
        self._auto_reconnect = True
        self._reconnect_delay = 5  # seconds
        self._max_reconnect_attempts = 10
        
        # Hot reload settings
        self._prompt_file_mtime = None
        self._check_prompt_reload = True
    
    def hot_reload_prompt(self, new_prompt_file: Optional[str] = None) -> bool:
        """Reload agent prompt without restarting.
        
        Returns True if prompt was reloaded, False otherwise.
        """
        if not self.dev_mode:
            print(f"[{self.agent_id}] Hot reload only available in dev mode")
            return False
        
        prompt_file = Path(new_prompt_file) if new_prompt_file else (
            Path(__file__).resolve().parent.parent / "docs" / "agent_prompt.md"
        )
        
        if not prompt_file.exists():
            print(f"[{self.agent_id}] Prompt file not found: {prompt_file}")
            return False
        
        try:
            # Check if file has changed
            current_mtime = prompt_file.stat().st_mtime
            if self._prompt_file_mtime and current_mtime == self._prompt_file_mtime:
                return False  # No change
            
            # Reload prompt
            new_prompt = prompt_file.read_text(encoding="utf-8")
            if self.driver:
                self.driver.system_prompt = new_prompt
                self._prompt_file_mtime = current_mtime
                print(f"[{self.agent_id}] 🔄 Prompt reloaded from {prompt_file.name}")
                return True
                
        except Exception as e:
            print(f"[{self.agent_id}] ❌ Failed to reload prompt: {e}")
            
        return False
    
    async def _dev_auto_reconnect(self):
        """Auto-reconnect logic for development mode."""
        if not self.dev_mode or not self._auto_reconnect:
            return
        
        attempts = 0
        while attempts < self._max_reconnect_attempts:
            attempts += 1
            print(f"[{self.agent_id}] 🔄 Reconnection attempt {attempts}/{self._max_reconnect_attempts}")
            
            try:
                # Re-register and rejoin
                self._register_with_server()
                self._join_queue()
                
                # Restart WebSocket
                await self._start_websocket_listener()
                
                print(f"[{self.agent_id}] ✅ Reconnected successfully!")
                return
                
            except Exception as e:
                print(f"[{self.agent_id}] ❌ Reconnection failed: {e}")
                await asyncio.sleep(self._reconnect_delay)
        
        print(f"[{self.agent_id}] ❌ Max reconnection attempts reached")


def main():
    """Main function for running an agent standalone"""
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Run an The Email Game agent")
    parser.add_argument("agent_id", help="Your agent name: the public identity shown on the leaderboard and used in-game")
    parser.add_argument("username", nargs="?", default=None,
                        help="Optional local display label (defaults to your agent name; not shown publicly)")
    parser.add_argument("--server", default=os.environ.get("INBOX_ARENA_SERVER", "http://localhost:8000"), help="Email server URL")
    parser.add_argument("--prompt", default=None, help="Path to a custom system prompt file (default: docs/agent_prompt.md)")
    parser.add_argument("--model", default=None, help="OpenAI model to use (default: from config)")
    parser.add_argument("--temperature", type=float, default=1.0, help="LLM temperature 0.0-2.0 (default: 1.0)")
    parser.add_argument("--dev", action="store_true", help="Enable development mode")
    args = parser.parse_args()

    print(f"Starting agent {args.agent_id}")

    agent = BaseAgent(
        args.agent_id,
        args.username,
        email_server_url=args.server,
        dev_mode=args.dev,
        prompt_file=args.prompt,
        model=args.model,
        temperature=args.temperature,
    )
    
    try:
        agent.run_sync()
    except KeyboardInterrupt:
        print("\nShutting down agent...")
        agent.stop()
        # Gracefully disconnect
        import asyncio
        try:
            asyncio.run(agent.disconnect_gracefully())
        except Exception as e:
            print(f"Error during graceful disconnect: {e}")


if __name__ == "__main__":
    main() 