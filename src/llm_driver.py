import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Callable, Any

try:
    import openai  # type: ignore
except ImportError:
    openai = None

try:
    import anthropic  # type: ignore
except ImportError:
    anthropic = None


class LLMDriver:
    """Wraps OpenAI or Anthropic chat-completion calls and dispatches tool calls.
    Model names starting with 'claude-' use the Anthropic API; all others use OpenAI.
    """

    def __init__(
        self,
        agent_id: str,
        system_prompt: str,
        send_email_callable: Callable[[str, str, str], Dict[str, Any]],
        sign_message_callable: Callable[[str, str], Dict[str, Any]] = None,
        sign_and_respond_callable: Callable[[str, str, str, str], Dict[str, Any]] = None,
        submit_signature_callable: Callable[[Dict[str, Any]], Dict[str, Any]] = None,
        model: str = "gpt-4o",
        temperature: float = 1.0,
        verbose: bool = True,
        frequency_penalty: float = None,
        presence_penalty: float = None,
        max_tokens: int = None,
    ) -> None:
        self.agent_id = agent_id
        self.system_prompt = system_prompt
        self.send_email_fn = send_email_callable
        self.sign_message_fn = sign_message_callable
        self.sign_and_respond_fn = sign_and_respond_callable
        self.submit_signature_fn = submit_signature_callable
        self.model = model
        self.temperature = temperature
        # Anti-degeneration defaults. Small models at temperature 1.0 with no
        # penalties fall into repetition loops - walls of "Thanks! :) Best! :)"
        # and re-pasted sentences. A mild frequency penalty plus a hard output
        # cap keeps responses substantively the same while killing the runaway
        # repetition (which also bloated requests toward the 431 header limit).
        # All three are overridable via constructor args or env vars.
        self.frequency_penalty = (
            frequency_penalty if frequency_penalty is not None
            else float(os.getenv("EMAIL_GAME_LLM_FREQUENCY_PENALTY", "0.4"))
        )
        self.presence_penalty = (
            presence_penalty if presence_penalty is not None
            else float(os.getenv("EMAIL_GAME_LLM_PRESENCE_PENALTY", "0.1"))
        )
        self.max_tokens = (
            max_tokens if max_tokens is not None
            else int(os.getenv("EMAIL_GAME_LLM_MAX_TOKENS", "1000"))
        )
        self.message_log: List[Dict[str, Any]] = []
        # Backstop cap on conversation length. Context is reset each game (round 1
        # clears message_log), so a normal game stays well under this; the cap
        # only bounds pathological growth (e.g. a reconnect/replay storm) so the
        # request can't balloon without limit. Generous enough never to truncate
        # a single clean game's within-game history (needed for fuzzy rounds).
        self.max_log_messages = int(os.getenv("EMAIL_GAME_LLM_MAX_LOG_MESSAGES", "400"))
        self.verbose = verbose
        self._is_claude = model.startswith("claude-")

        # Per-agent log file - grouped by session start time (env var) or wallclock
        session_tag = os.environ.get("INBOX_ARENA_SESSION_ID", datetime.now().strftime("%Y%m%d_%H%M%S"))
        log_dir = Path(__file__).resolve().parents[1] / "agent_logs" / session_tag
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{agent_id}.log"
        self._logger = logging.getLogger(f"agent.{agent_id}.{session_tag}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        if not self._logger.handlers:
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
            self._logger.addHandler(handler)
        self._logger.info(f"=== Agent {agent_id} started | model={model} ===")

        # Tool schemas - OpenAI format (converted for Claude on the fly)
        self.tools_openai = [
            {
                "type": "function",
                "function": {
                    "name": "send_email",
                    "description": "Send an email to another agent via the game server.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to": {"type": "string", "description": "Recipient agent id"},
                            "subject": {"type": "string", "description": "Email subject"},
                            "body": {"type": "string", "description": "Email body text"},
                        },
                        "required": ["to", "subject", "body"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sign_message",
                    "description": "Sign a message for another agent using RSA cryptography.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "description": "Message to sign"},
                            "for_agent": {"type": "string", "description": "Agent ID to sign the message for"},
                        },
                        "required": ["message", "for_agent"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sign_and_respond",
                    "description": "Sign a message for another agent and send it back to them in a single operation. Use this when responding to signature requests.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to_agent": {"type": "string", "description": "The agent ID to send the signed message back to"},
                            "message_to_sign": {"type": "string", "description": "The exact message text to sign"},
                            "response_body": {"type": "string", "description": "Friendly response message to include before the signature JSON"},
                        },
                        "required": ["to_agent", "message_to_sign", "response_body"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "submit_signature",
                    "description": "Submit a received signature to the moderator for scoring.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "signed_message": {
                                "type": "object",
                                "description": "The complete signed message object",
                                "properties": {
                                    "original_message": {"type": "string"},
                                    "signature": {"type": "string"},
                                    "signer": {"type": "string"},
                                    "signed_for": {"type": "string"},
                                    "timestamp": {"type": "string"},
                                    "signature_type": {"type": "string"},
                                },
                                "required": ["original_message", "signature", "signer", "signed_for", "timestamp", "signature_type"],
                                "additionalProperties": False,
                            },
                        },
                        "required": ["signed_message"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
        ]

        # Claude tool schema format
        self.tools_claude = [
            {
                "name": "send_email",
                "description": "Send an email to another agent via the game server.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient agent id"},
                        "subject": {"type": "string", "description": "Email subject"},
                        "body": {"type": "string", "description": "Email body text"},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
            {
                "name": "sign_message",
                "description": "Sign a message for another agent using RSA cryptography.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "Message to sign"},
                        "for_agent": {"type": "string", "description": "Agent ID to sign the message for"},
                    },
                    "required": ["message", "for_agent"],
                },
            },
            {
                "name": "sign_and_respond",
                "description": "Sign a message for another agent and send it back to them in a single operation. Use this when responding to signature requests.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to_agent": {"type": "string", "description": "The agent ID to send the signed message back to"},
                        "message_to_sign": {"type": "string", "description": "The exact message text to sign"},
                        "response_body": {"type": "string", "description": "Friendly response message to include before the signature JSON"},
                    },
                    "required": ["to_agent", "message_to_sign", "response_body"],
                },
            },
            {
                "name": "submit_signature",
                "description": "Submit a received signature to the moderator for scoring.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "signed_message": {
                            "type": "object",
                            "description": "The complete signed message object",
                            "properties": {
                                "original_message": {"type": "string"},
                                "signature": {"type": "string"},
                                "signer": {"type": "string"},
                                "signed_for": {"type": "string"},
                                "timestamp": {"type": "string"},
                                "signature_type": {"type": "string"},
                            },
                            "required": ["original_message", "signature", "signer", "signed_for", "timestamp", "signature_type"],
                        },
                    },
                    "required": ["signed_message"],
                },
            },
        ]

        # Per-request timeout. Without this the SDK default is 600s, so a single
        # stalled request silently freezes the whole agent (its WebSocket stops
        # answering pings and it gets dropped from the match/queue). A short
        # timeout makes a stalled call error out quickly so the agent can recover
        # and keep playing. Override via EMAIL_GAME_LLM_TIMEOUT_SEC.
        llm_timeout = float(os.getenv("EMAIL_GAME_LLM_TIMEOUT_SEC", "60"))

        # Initialise clients
        if self._is_claude:
            if anthropic is None:
                raise ImportError("anthropic package is required for Claude models. Run: pip install anthropic")
            self._claude_client = anthropic.Anthropic(
                api_key=os.getenv("ANTHROPIC_API_KEY", ""), timeout=llm_timeout)
        else:
            if openai is None:
                raise ImportError("openai package is required but not installed")
            # OPENAI_BASE_URL lets the host point agents at an LLM gateway (e.g. a
            # LiteLLM proxy that issues budget-capped keys) instead of OpenAI
            # directly. Unset -> the OpenAI default endpoint.
            openai_base_url = os.getenv("OPENAI_BASE_URL") or None
            if hasattr(openai, "OpenAI"):
                self._openai_client = openai.OpenAI(
                    api_key=os.getenv("OPENAI_API_KEY", ""),
                    base_url=openai_base_url, timeout=llm_timeout)
                self._is_v1 = True
            else:
                self._is_v1 = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_message_to_sign(body: str) -> str:
        """Extract the exact message string from a signing request email body.
        Returns empty string if no signing request is found.
        """
        import re
        # Match patterns like "please sign this message for me: X" or "sign: X"
        # Stop at newline or the injection separator ---
        patterns = [
            r'please sign (?:this )?message for me:\s*["\']?([^\n"\']+?)["\']?\s*(?:\n|$|---)',
            r'sign this(?:\s+message)?(?:\s+for me)?:\s*["\']?([^\n"\']+?)["\']?\s*(?:\n|$|---)',
            r'sign the following(?:\s+message)?:\s*["\']?([^\n"\']+?)["\']?\s*(?:\n|$|---)',
        ]
        for pattern in patterns:
            m = re.search(pattern, body, re.IGNORECASE)
            if m:
                return m.group(1).strip().strip('"\'')
        return ""

    def on_emails(self, messages: List[Dict[str, Any]]) -> None:
        signing_reminders = []

        for message in messages:
            frm = message.get("from", "?")
            subj = message.get("subject", "")
            body = message.get("body", "")

            # Log round marker when moderator message arrives
            if frm == "moderator":
                self._logger.info(f"{'='*60}")
                self._logger.info(f"ROUND START | subject={subj}")
                self._logger.info(f"{'='*60}")

            self._logger.info(f"EMAIL IN | from={frm} | subject={subj}")
            self._logger.debug(f"  body: {body[:500]}")
            if self.verbose:
                print(f"[{self.agent_id}] <<< EMAIL: {frm} - {subj}")

            # Extract message-to-sign from signing request emails and build reminder
            if frm != "moderator" and frm != "system_reminder":
                msg_to_sign = self._extract_message_to_sign(body)
                if msg_to_sign:
                    signing_reminders.append(
                        f"[SIGNING REQUEST from {frm}]: The ONLY valid message_to_sign for this request is exactly: \"{msg_to_sign}\""
                    )
                    self._logger.info(f"EXTRACTED message_to_sign from {frm}: \"{msg_to_sign}\"")

            self.message_log.append({"role": "user", "content": json.dumps(message)})

        # Append a single reinforcement message if any signing requests were detected
        if signing_reminders:
            reminder = (
                "[EXTRACTION REMINDER]: The following message strings were extracted verbatim from the emails above. "
                "You MUST use these exact strings as message_to_sign - do not use any other string from your history:\n"
                + "\n".join(signing_reminders)
            )
            self.message_log.append({"role": "user", "content": reminder})
            self._logger.info(f"SIGNING REMINDER injected: {reminder}")

        self._call_llm_and_dispatch()

    def on_email(self, message: Dict[str, Any]) -> None:
        self.on_emails([message])

    def _trim_message_log(self) -> None:
        """Backstop: keep the conversation under max_log_messages so a pathological
        growth (e.g. reconnect/replay storm) can't balloon the request. Drops the
        oldest entries, then any leading tool/function results so the trimmed log
        never starts with an orphaned tool result (which the chat APIs reject)."""
        cap = self.max_log_messages
        if cap <= 0 or len(self.message_log) <= cap:
            return
        dropped = len(self.message_log) - cap
        self.message_log = self.message_log[dropped:]
        while self.message_log and self.message_log[0].get("role") == "function":
            self.message_log.pop(0)
        print(f"[{self.agent_id}] (context cap hit - trimmed {dropped}+ old message(s))")

    def _call_llm_and_dispatch(self) -> None:
        try:
            self._trim_message_log()
            context_size = len(self.message_log) + 1  # +1 for system prompt
            print(f"[{self.agent_id}] thinking...")
            self._logger.info(f"LLM CALL | context_size={context_size}")

            if self._is_claude:
                tool_calls, text_content = self._chat_complete_claude()
            else:
                assistant_msg = self._chat_complete_openai()
                text_content = assistant_msg.get("content") or ""
                tool_calls = self._extract_openai_tool_calls(assistant_msg)
                self._store_assistant_turn_openai(assistant_msg)

            if text_content and text_content.strip():
                if self.verbose:
                    print(f"[{self.agent_id}] note: {text_content[:200]}")
                self._logger.info(f"LLM TEXT | {text_content}")

            if tool_calls:
                for call in tool_calls:
                    self._logger.info(f"TOOL CALL | {call['name']} | args={json.dumps(call['args'])}")
                    self._execute_tool(call["name"], call["args"], call.get("id"))
            else:
                print(f"[{self.agent_id}] reviewed messages, nothing to send or sign")
                self._logger.warning("NO TOOL CALLS - LLM responded with text only")

            self._logger.info(f"TURN DONE | {len(tool_calls)} tool call(s)")

        except Exception as e:
            self._logger.error(f"LLM ERROR | {e}")
            msg = str(e)
            low = msg.lower()
            # Budget exhausted: a clear, friendly notice instead of a scary
            # traceback. The issued key's spend hit its cap; the agent stays up
            # but can't act until the cap is raised.
            if "budget" in low and ("exceed" in low or "exceeded" in low):
                print(f"\n[{self.agent_id}] 💸 Your LLM budget is used up - the agent can't "
                      f"think until it's topped up. Contact the organizers if you need more; "
                      f"the agent is otherwise fine and will resume once the budget is raised.\n")
            elif "rate limit" in low or "ratelimit" in low or "429" in low:
                print(f"[{self.agent_id}] LLM rate-limited; will retry on the next turn.")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}][{self.agent_id}] LLM Driver error: {e}")
                import traceback
                traceback.print_exc()

    # ------------------------------------------------------------------
    # OpenAI backend
    # ------------------------------------------------------------------

    # $/1M tokens (input, output): local-sandbox spend ESTIMATES only - the
    # hosted competition reads real numbers from the gateway instead.
    _PRICES = {"gpt-4.1": (2.00, 8.00), "gpt-4.1-mini": (0.40, 1.60),
               "gpt-4o": (2.50, 10.00), "gpt-4o-mini": (0.15, 0.60)}

    def usage_totals(self):
        pt = getattr(self, "_usage_pt", 0)
        ct = getattr(self, "_usage_ct", 0)
        pin, pout = self._PRICES.get(self.model, (2.00, 8.00))
        return {"prompt_tokens": pt, "completion_tokens": ct,
                "est_cost": round((pt * pin + ct * pout) / 1_000_000, 6)}

    def _track_usage(self, resp):
        try:
            u = getattr(resp, "usage", None)
            if u is None:
                return
            self._usage_pt = getattr(self, "_usage_pt", 0) + int(getattr(u, "prompt_tokens", 0) or 0)
            self._usage_ct = getattr(self, "_usage_ct", 0) + int(getattr(u, "completion_tokens", 0) or 0)
        except Exception:
            pass

    def _chat_complete_openai(self):
        full_messages = [{"role": "system", "content": self.system_prompt}] + self.message_log
        if self._is_v1:
            resp = self._openai_client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                tools=self.tools_openai,
                tool_choice="auto",
                temperature=self.temperature,
                frequency_penalty=self.frequency_penalty,
                presence_penalty=self.presence_penalty,
                max_tokens=self.max_tokens,
            )
            self._track_usage(resp)
            return resp.choices[0].message.dict()
        else:
            resp = openai.ChatCompletion.create(
                model=self.model,
                messages=full_messages,
                functions=self.tools_openai,
                function_call="auto",
                temperature=self.temperature,
                frequency_penalty=self.frequency_penalty,
                presence_penalty=self.presence_penalty,
                max_tokens=self.max_tokens,
            )
            return resp.choices[0].message

    def _extract_openai_tool_calls(self, assistant_msg):
        calls = []
        if assistant_msg.get("tool_calls"):
            for call in assistant_msg["tool_calls"]:
                fn = call["function"] if isinstance(call, dict) else call
                args_raw = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except json.JSONDecodeError:
                    args = {}
                calls.append({"name": fn["name"], "args": args, "id": call.get("id") if isinstance(call, dict) else None})
        elif assistant_msg.get("function_call"):
            fn = assistant_msg["function_call"]
            args_raw = fn.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {}
            calls.append({"name": fn["name"], "args": args, "id": None})
        return calls

    def _store_assistant_turn_openai(self, assistant_msg):
        content_val = assistant_msg.get("content") or ""
        self.message_log.append({
            "role": "assistant",
            "content": content_val,
            "tool_call": assistant_msg.get("tool_call") or assistant_msg.get("function_call") or assistant_msg.get("tool_calls"),
        })

    # ------------------------------------------------------------------
    # Claude backend
    # ------------------------------------------------------------------

    def _build_claude_messages(self):
        """Convert the internal message_log to Claude's message format."""
        claude_messages = []
        i = 0
        while i < len(self.message_log):
            entry = self.message_log[i]
            role = entry["role"]

            if role == "user":
                claude_messages.append({"role": "user", "content": entry["content"]})
                i += 1

            elif role == "assistant":
                # Check if there are tool results immediately following
                content_blocks = []
                text = entry.get("content", "")
                if text and text.strip():
                    content_blocks.append({"type": "text", "text": text})

                tool_uses = entry.get("tool_call") or []
                if isinstance(tool_uses, dict):
                    tool_uses = [tool_uses]
                for tu in (tool_uses or []):
                    if isinstance(tu, dict) and tu.get("_claude_tool_use_id"):
                        args = tu.get("_args", {})
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tu["_claude_tool_use_id"],
                            "name": tu["_name"],
                            "input": args,
                        })

                if not content_blocks:
                    content_blocks.append({"type": "text", "text": ""})

                claude_messages.append({"role": "assistant", "content": content_blocks})
                i += 1

                # Collect tool results that follow
                tool_results = []
                while i < len(self.message_log) and self.message_log[i]["role"] == "function":
                    fr = self.message_log[i]
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": fr.get("_tool_use_id", "unknown"),
                        "content": fr["content"],
                    })
                    i += 1

                if tool_results:
                    claude_messages.append({"role": "user", "content": tool_results})

            elif role == "function":
                # Orphaned tool result - wrap in user message
                claude_messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": entry.get("_tool_use_id", "unknown"), "content": entry["content"]}],
                })
                i += 1

            else:
                i += 1

        return claude_messages

    def _chat_complete_claude(self):
        claude_messages = self._build_claude_messages()

        # Anthropic has no frequency/presence penalty; the output cap is the
        # repetition backstop here. temperature is honored.
        resp = self._claude_client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=self.system_prompt,
            messages=claude_messages,
            tools=self.tools_claude,
        )

        # Extract text and tool use blocks
        text_content = ""
        tool_calls = []
        assistant_content_blocks = []

        for block in resp.content:
            if block.type == "text":
                text_content += block.text
                assistant_content_blocks.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append({
                    "name": block.name,
                    "args": block.input,
                    "id": block.id,
                })
                assistant_content_blocks.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        # Store assistant turn with Claude metadata
        tool_use_refs = [
            {"_claude_tool_use_id": tc["id"], "_name": tc["name"], "_args": tc["args"]}
            for tc in tool_calls
        ]
        self.message_log.append({
            "role": "assistant",
            "content": text_content,
            "tool_call": tool_use_refs or None,
            "_claude_content_blocks": assistant_content_blocks,
        })

        return tool_calls, text_content

    # ------------------------------------------------------------------
    # Tool execution (shared)
    # ------------------------------------------------------------------

    def _execute_tool(self, name: str, args: dict, tool_use_id: str = None) -> None:
        result = {}

        def _snip(s, n=45):
            s = str(s or "")
            return s if len(s) <= n else s[:n] + "..."

        if name == "send_email":
            to = args.get("to")
            subject = args.get("subject", "")
            body = args.get("body")
            if to and body is not None:
                result = self.send_email_fn(to, subject, body)
                print(f"[{self.agent_id}] -> emailed {to}: \"{_snip(subject)}\"")

        elif name == "sign_message":
            message = args.get("message")
            for_agent = args.get("for_agent")
            if message and for_agent and self.sign_message_fn:
                result = self.sign_message_fn(message, for_agent)
                print(f"[{self.agent_id}] signed \"{_snip(message)}\" for {for_agent}")

        elif name == "sign_and_respond":
            to_agent = args.get("to_agent")
            message_to_sign = args.get("message_to_sign")
            response_body = args.get("response_body")
            if to_agent and message_to_sign and response_body is not None and self.sign_and_respond_fn:
                result = self.sign_and_respond_fn(to_agent, message_to_sign, response_body, "Signed Message")
                print(f"[{self.agent_id}] signed \"{_snip(message_to_sign)}\" for {to_agent} and replied")

        elif name == "submit_signature":
            signed_message = args.get("signed_message")
            if signed_message and self.submit_signature_fn:
                result = self.submit_signature_fn(signed_message)
                sm = signed_message if isinstance(signed_message, dict) else {}
                print(f"[{self.agent_id}] submitted signature (by {sm.get('signer','?')} for {sm.get('signed_for','?')})")

        self._logger.info(f"TOOL RESULT | {name} | {json.dumps(result)[:300]}")
        self.message_log.append({
            "role": "function",
            "name": name,
            "content": json.dumps(result),
            "_tool_use_id": tool_use_id or "unknown",
        })
