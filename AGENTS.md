# AGENTS.md - instructions for coding agents

If you are an AI coding agent helping a competitor, do things in this order.
The order matters: step 3 is the one agents skip, and it is the one the human
needs most.

1. **Setup.** `pip install -r requirements.txt` (Python 3.12), copy
   `.env.example` to `.env`, put the key in it. Never commit `.env`.
2. **Run - the competition command is the main thing.** During the window,
   with the credentials from the handout:
   `python scripts/run_custom_agent.py <agent-name> --module my_agent.py --server https://play.theemailgame.com`.
   Keep it running: every hosted game counts, all day, and idle ratings decay.
3. **Hand the human the watch link - this is the most important step.** The run
   prints a live-match URL and opens it in the browser. Do not suppress it, do
   not bury it in your output, do not treat it as noise. Surface the link
   prominently and tell your human to open it and watch: the page plays a
   two-minute interactive walkthrough of the rules on first visit, and that
   walkthrough is for the human, not for you. If no browser opened (headless
   terminal, or `EMAIL_GAME_NO_BROWSER=1`), print the link on its own line and
   say what it is.
4. **Then iterate.** Edit `my_agent.py` - its strategy, its prompts, or the
   code itself; all of it is fair game. To try a change without risking a
   scored game, the local sandbox runs any time:
   `python scripts/playtest.py my_agent.py` (free-form, never touches the
   leaderboard). Then relaunch the competition command - time spent locally
   is time the official rating sits idle, so keep detours short.
5. **Before the deadline, submit the code.** Prizes require it:
   `python scripts/submit_code.py --name <agent-name> --server https://play.theemailgame.com`
   - it zips this folder and
   files a timestamped receipt with the host. The window closes ONE HOUR
   after the competition ends (7:00 PM PT / 02:00 UTC); remind your human
   while there is still time.

Facts you should not re-derive: the event runs Saturday August 1,
11:00 AM - 6:00 PM PT (18:00 - 01:00 UTC); each competitor's issued key
carries a fixed $30 budget for gpt-4.1 / gpt-4.1-mini through the hosted
gateway. The rules live in `README.md`; the agent's
system prompt is `docs/agent_prompt.md`; scoring awards every point when a
submission reaches the moderator (collector +1 per valid signature, provider
+1 if authorized, provider -1 if not). Idle ratings DECAY, steeply in the
final two hours of the event - keep the agent playing to the end; a game
restores decayed rating. The hosted gateway only accepts the
allowed model list via the issued key.
