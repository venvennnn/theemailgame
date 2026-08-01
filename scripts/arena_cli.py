#!/usr/bin/env python3
"""
The Email Game Developer CLI
A command-line interface for developers to easily interact with The Email Game.
"""

import click
import asyncio
import os
import sys
import json
import requests
import subprocess
import time
from pathlib import Path
from datetime import datetime

# Keep emoji/log output safe when stdout is redirected on non-UTF-8 consoles.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config_manager import ConfigManager


@click.group()
@click.pass_context
def cli(ctx):
    """The Email Game developer tools for building and testing agents."""
    ctx.ensure_object(dict)
    ctx.obj['config'] = ConfigManager()


def _clear_local_data() -> int:
    """Delete all LOCAL test artifacts: session_results (the local leaderboard +
    history), agent_logs, and current_game_*.json. Returns how many items removed.
    Local only - never touches the competition server."""
    import shutil
    removed = 0
    for sub in ("session_results", "agent_logs"):
        d = PROJECT_ROOT / sub
        if d.exists():
            for f in list(d.iterdir()):
                try:
                    shutil.rmtree(f) if f.is_dir() else f.unlink()
                    removed += 1
                except Exception:
                    pass
    for f in PROJECT_ROOT.glob("current_game_*.json"):
        try:
            f.unlink(); removed += 1
        except Exception:
            pass
    return removed


@cli.command()
def reset():
    """Clear all LOCAL data: your local leaderboard + match history
    (session_results), agent logs, and current-game files. Start fresh anytime.
    Does NOT affect the competition server."""
    n = _clear_local_data()
    click.echo(f"🧹 Cleared {n} local item(s). Your Local Testing Leaderboard and "
               f"history are now empty (the competition server is untouched).")


@cli.command()
@click.option('--agent-id', '-a', default=None, help='Agent ID (defaults to config or generates one)')
@click.option('--username', '-u', default=None, help='Agent username (defaults to agent ID)')
@click.option('--server', '-s', default=None, help='Server URL (defaults to config)')
@click.pass_context
def join(ctx, agent_id, username, server):
    """Join the live game queue with your agent."""
    config = ctx.obj['config']
    
    # Resolve parameters with defaults
    server_url = server or config.get_server_url()
    if not server_url:
        click.echo("❌ No server URL provided. Use --server or run 'arena config'")
        return
    
    if not agent_id:
        agent_id = config.get_agent_id() or f"dev_{int(time.time())}"
        click.echo(f"📝 Using agent ID: {agent_id}")
    
    username = username or agent_id.title()
    
    click.echo(f"🤖 Joining queue as {agent_id} ({username})...")
    click.echo(f"🌐 Server: {server_url}")
    
    # Check OpenAI key
    if not os.getenv('OPENAI_API_KEY'):
        click.echo("❌ OPENAI_API_KEY not set!")
        return
    
    # Start the agent
    try:
        subprocess.run([
            sys.executable, "-m", "src.base_agent",
            agent_id, username, server_url
        ], cwd=PROJECT_ROOT)
    except KeyboardInterrupt:
        click.echo("\n⏹️  Agent disconnected")


@cli.command()
@click.option('--server', '-s', default=None, help='Server URL')
@click.option('--watch', '-w', is_flag=True, help='Watch queue status continuously')
@click.pass_context
def status(ctx, server, watch):
    """Show current queue status and game progress."""
    config = ctx.obj['config']
    server_url = server or config.get_server_url()
    
    if not server_url:
        click.echo("❌ No server URL provided. Use --server or run 'arena config'")
        return
    
    def show_status():
        try:
            response = requests.get(f"{server_url}/queue_status", timeout=5)
            if response.status_code == 200:
                data = response.json()
                
                click.clear()
                click.echo("📊 The Email Game Queue Status")
                click.echo("=" * 40)
                click.echo(f"🌐 Server: {server_url}")
                click.echo(f"⏰ Time: {datetime.now().strftime('%H:%M:%S')}")
                click.echo()
                
                if data['game_in_progress']:
                    click.echo("🎮 Game Status: IN PROGRESS")
                else:
                    click.echo("🎮 Game Status: Waiting for players")
                
                click.echo(f"👥 Queue Length: {data['queue_length']}/4")
                
                if data['agents_waiting']:
                    click.echo("📋 Agents in Queue:")
                    for agent in data['agents_waiting']:
                        click.echo(f"   • {agent}")
                
                if data['connected_agents']:
                    click.echo(f"\n🔗 Connected Agents: {len(data['connected_agents'])}")
                    for agent in data['connected_agents']:
                        click.echo(f"   • {agent}")
                
                # Check for recent games
                try:
                    results_response = requests.get(f"{server_url}/session_results", timeout=5)
                    if results_response.status_code == 200:
                        results = results_response.json()
                        if results.get('files'):
                            click.echo("\n📜 Recent Games:")
                            for i, file_info in enumerate(results['files'][:3]):
                                timestamp = datetime.fromtimestamp(file_info['modified'])
                                click.echo(f"   • {timestamp.strftime('%H:%M')} - {file_info['filename']}")
                except:
                    pass
                    
            else:
                click.echo(f"❌ Failed to get status: {response.status_code}")
                
        except requests.exceptions.ConnectionError:
            click.echo("❌ Cannot connect to server")
        except Exception as e:
            click.echo(f"❌ Error: {e}")
    
    if watch:
        click.echo("👀 Watching queue status (Ctrl+C to stop)...")
        try:
            while True:
                show_status()
                time.sleep(2)
        except KeyboardInterrupt:
            click.echo("\n⏹️  Stopped watching")
    else:
        show_status()


@cli.command()
@click.option('--against', default='base', help='Opponents: base, smart, or mixed')
@click.option('--agent-path', default=None, help='Path to custom agent module')
@click.pass_context
def local_game(ctx, against, agent_path):
    """Start a local game against base agents."""
    from src.game.config import NUM_ROUNDS
    click.echo("🎮 Starting local game...")
    click.echo(f"🤖 Opponents: {against} agents")
    click.echo(f"🏁 Rounds: {NUM_ROUNDS}")
    
    if agent_path:
        click.echo(f"📂 Using custom agent: {agent_path}")
    
    # Start local server
    click.echo("\n⚡ Starting local email server...")
    server_process = subprocess.Popen([
        sys.executable, "-m", "src.email_server"
    ], cwd=PROJECT_ROOT)

    # Wait for server to be ready
    import urllib.request
    for _ in range(20):
        try:
            urllib.request.urlopen("http://localhost:8000/health", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    else:
        click.echo("❌ Server failed to start")
        server_process.terminate()
        return
    
    try:
        # Start base agents
        agent_processes = []
        base_agents = ['alice', 'bob', 'charlie']
        
        click.echo("🤖 Starting base agents...")
        for agent_id in base_agents:
            process = subprocess.Popen([
                sys.executable, "-m", "src.base_agent",
                agent_id, agent_id.title()
            ], cwd=PROJECT_ROOT)
            agent_processes.append(process)
            click.echo(f"   • Started {agent_id}")
            time.sleep(2)
        
        # Start player agent
        if agent_path:
            click.echo(f"\n🎯 Waiting for your agent to connect (run it in another terminal)...")
            click.echo("   Press Ctrl+C when the game is done.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        else:
            click.echo("\n🎯 Starting your agent...")
            player_process = subprocess.Popen([
                sys.executable, "-m", "src.base_agent",
                "player", "Player"
            ], cwd=PROJECT_ROOT)
            
            try:
                player_process.wait()
            except KeyboardInterrupt:
                player_process.terminate()
        
    finally:
        # Cleanup
        click.echo("\n🧹 Cleaning up...")
        for process in agent_processes:
            process.terminate()
        server_process.terminate()
        
        # Wait for processes to end
        time.sleep(1)
        click.echo("✅ Local game ended")


def _wait_for_single_game(timeout: int = 480):
    """Wait for one game to finish (its result file appears), then print scores."""
    import glob

    results_dir = PROJECT_ROOT / "session_results"
    before = set(glob.glob(str(results_dir / "session_arena_*.json")))
    waited = 0
    try:
        while waited < timeout:
            if set(glob.glob(str(results_dir / "session_arena_*.json"))) - before:
                time.sleep(1)  # let the file finish flushing
                break
            time.sleep(2)
            waited += 2
        else:
            click.echo("⚠️  Timed out waiting for the game to finish "
                       "(did enough agents start?).")
            return
    except KeyboardInterrupt:
        click.echo("\n(interrupted)")
        return

    new = sorted(set(glob.glob(str(results_dir / "session_arena_*.json"))) - before,
                 key=os.path.getmtime)
    if not new:
        click.echo("⚠️  Game finished but no new result file was found.")
        return
    try:
        d = json.load(open(new[-1], encoding="utf-8"))
        scores = d.get("cumulative_scores", {})
        click.echo("\n🏁 Final scores:")
        for aid, sc in sorted(scores.items(), key=lambda x: -x[1]):
            click.echo(f"   {aid}: {sc}")
        click.echo(f"   (saved: {os.path.basename(new[-1])})")
    except Exception as e:
        click.echo(f"⚠️  Could not read result file: {e}")


@cli.command()
@click.option('--agent', '-a', multiple=True, help=(
    'Agent spec. Formats:\n'
    '  base:<id>[:<model>[:<temperature>]]          - base LLM agent\n'
    '  prompt:<id>:<prompt_file>[:<model>[:<temp>]] - base agent with custom prompt\n'
    '  custom:<id>:<module_file>                    - custom agent module\n'
    'Example: --agent base:alice --agent base:bob:gpt-4o-mini:0.5'
))
@click.option('--competition', is_flag=True, help=(
    'Run the live ladder (continuous auto-requeue + concurrency). This is the '
    'competition behavior and is OFF by default - without it, the session runs '
    'the game(s) once and exits, which is the safe mode for local testing.'))
@click.option('--loop', is_flag=True, help=(
    'Local requeue: keep playing back-to-back games (auto-requeue) WITHOUT '
    'competition mode - no login, local board. For watching your agent play '
    'repeatedly while you iterate. Ctrl+C to stop. (Ignored with --competition.)'))
@click.option('--quiet-opponents', is_flag=True, help=(
    'Only show the FIRST agent (yours) in the terminal; opponents run quietly '
    '(their console output goes to agent_logs/<id>.console.log). Keeps the terminal '
    'focused on your agent. You can still watch any agent in the browser.'))
@click.pass_context
def session(ctx, agent, competition, loop, quiet_opponents):
    """Start a full session with any combination of agents in one terminal."""
    from src.game.config import NUM_ROUNDS

    if not agent:
        click.echo("❌ Provide at least one --agent spec. See --help for format.")
        return

    click.echo(f"🎮 Starting session with {len(agent)} agents, {NUM_ROUNDS} rounds...")

    # Parse agent specs
    agent_configs = []
    for spec in agent:
        parts = spec.split(':')
        kind = parts[0]
        if kind == 'base' and len(parts) >= 2:
            cfg = {'kind': 'base', 'id': parts[1], 'username': parts[1].title()}
            if len(parts) >= 3: cfg['model'] = parts[2]
            if len(parts) >= 4: cfg['temperature'] = parts[3]
            agent_configs.append(cfg)
        elif kind == 'prompt' and len(parts) >= 3:
            cfg = {'kind': 'prompt', 'id': parts[1], 'username': parts[1].title(), 'prompt': parts[2]}
            if len(parts) >= 4: cfg['model'] = parts[3]
            if len(parts) >= 5: cfg['temperature'] = parts[4]
            agent_configs.append(cfg)
        elif kind == 'custom' and len(parts) >= 3:
            agent_configs.append({'kind': 'custom', 'id': parts[1], 'username': parts[1].title(), 'module': parts[2]})
        else:
            click.echo(f"❌ Invalid agent spec: {spec}")
            return

    # Refuse to start if port 8000 is already taken: otherwise the new server
    # fails to bind but an existing one answers, so we'd silently attach agents to
    # it and produce confusing errors. Abort with a clear message instead.
    import socket
    _probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _probe.bind(("127.0.0.1", 8000))
    except OSError:
        click.echo("❌ Port 8000 is already in use (another local server or a previous "
                   "playtest/session is still running). Stop it and try again.")
        return
    finally:
        _probe.close()

    # Start server
    click.echo("\n⚡ Starting local email server...")
    server_env = {**os.environ}
    if competition:
        server_env["EMAIL_GAME_COMPETITION"] = "1"
        click.echo("   (competition mode: live ladder, continuous auto-requeue)")
    elif loop:
        server_env["EMAIL_GAME_REQUEUE"] = "1"   # local requeue, no competition mode
        click.echo("   (local loop: continuous auto-requeue, local board, no login)")
    else:
        click.echo("   (testing mode: runs once and exits, no auto-requeue)")
    server_process = subprocess.Popen([sys.executable, "-m", "src.email_server"], cwd=PROJECT_ROOT, env=server_env)

    import urllib.request
    for _ in range(20):
        try:
            urllib.request.urlopen("http://localhost:8000/health", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    else:
        click.echo("❌ Server failed to start")
        server_process.terminate()
        return

    agent_processes = []
    try:
        for idx, cfg in enumerate(agent_configs):
            if cfg['kind'] == 'base':
                cmd = [sys.executable, "-m", "src.base_agent", cfg['id'], cfg['username']]
            elif cfg['kind'] == 'prompt':
                cmd = [sys.executable, "-m", "src.base_agent", cfg['id'], cfg['username'], "--prompt", cfg['prompt']]
            elif cfg['kind'] == 'custom':
                cmd = [sys.executable, "scripts/run_custom_agent.py", cfg['id'], cfg['username'], "--module", cfg['module']]
            if cfg.get('model'):
                cmd += ["--model", cfg['model']]
            if cfg.get('temperature'):
                cmd += ["--temperature", cfg['temperature']]

            # Only the FIRST agent (yours) advertises its watch/leaderboard links -
            # at startup and on each new game, exactly like a single agent does in
            # the live phases. Opponents never print a banner (it would be noise and
            # you watch your own agent), so suppress theirs.
            agent_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
            if idx > 0:
                agent_env["EMAIL_GAME_NO_LINKS"] = "1"
            # With --quiet-opponents, only the first agent (yours) prints to the
            # terminal; the rest stream to a log file so the console stays focused.
            if quiet_opponents and idx > 0:
                (PROJECT_ROOT / "agent_logs").mkdir(exist_ok=True)
                logf = open(PROJECT_ROOT / "agent_logs" / f"{cfg['id']}.console.log", "w")
                process = subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=agent_env,
                                           stdout=logf, stderr=subprocess.STDOUT)
                click.echo(f"   • Started {cfg['id']} ({cfg['kind']}) [quiet -> agent_logs/{cfg['id']}.console.log]")
            else:
                process = subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=agent_env)
                click.echo(f"   • Started {cfg['id']} ({cfg['kind']})")
            agent_processes.append(process)
            time.sleep(1)

        # Local viewing links (the server hosts these; no token needed locally).
        view_agent = agent_configs[0]['id'] if agent_configs else ""
        click.echo("\n👀 Watch & review in your browser:")
        click.echo(f"   • Watch live:   http://localhost:8000/watch?agent={view_agent}")
        click.echo(f"   • Leaderboard:  http://localhost:8000/leaderboard")
        click.echo(f"   • Match history: http://localhost:8000/history?agent={view_agent}")

        if competition or loop:
            label = "Live ladder" if competition else "Local loop (continuous local games)"
            click.echo(f"\n⏳ {label} running... (Ctrl+C to stop)")
            try:
                while all(p.poll() is None for p in agent_processes):
                    time.sleep(2)
            except KeyboardInterrupt:
                pass
        else:
            click.echo("\n⏳ Running the game(s) once... (Ctrl+C to stop early)")
            _wait_for_single_game()
            # Keep the server up briefly so you can review the match/leaderboard/
            # history in the browser before everything shuts down.
            review = int(os.getenv("EMAIL_GAME_LOCAL_REVIEW_SEC", "120"))
            if review > 0:
                click.echo(f"\n✅ Game finished. Review in your browser for {review}s "
                           f"(leaderboard / history / watch links above). Ctrl+C to stop now.")
                try:
                    time.sleep(review)
                except KeyboardInterrupt:
                    pass

    finally:
        click.echo("\n🧹 Cleaning up...")
        for p in agent_processes:
            p.terminate()
        server_process.terminate()
        time.sleep(1)
        click.echo("✅ Session ended")


@cli.command()
@click.option('--server', prompt='Server URL', help='The Email Game server URL')
@click.option('--agent-id', prompt='Default agent ID', default=lambda: f"dev_{os.getenv('USER', 'agent')}", help='Your default agent ID')
@click.option('--global', 'is_global', is_flag=True, help='Save to global config')
@click.pass_context
def config(ctx, server, agent_id, is_global):
    """Configure server URL and default settings."""
    config_manager = ctx.obj['config']
    
    config_data = {
        'server_url': server.rstrip('/'),
        'agent_id': agent_id,
        'configured_at': datetime.now().isoformat()
    }
    
    if is_global:
        config_path = Path.home() / '.email_game' / 'config.json'
    else:
        config_path = Path('./agent_config.json')
    
    config_manager.save_config(config_data, config_path)
    
    click.echo("✅ Configuration saved!")
    click.echo(f"📝 Server: {config_data['server_url']}")
    click.echo(f"🤖 Agent ID: {config_data['agent_id']}")
    click.echo(f"💾 Saved to: {config_path}")


@cli.command()
@click.option('--file', '-f', default=None, help='Session result file to analyze')
@click.option('--latest', is_flag=True, help='Analyze the latest game')
@click.pass_context
def analyze(ctx, file, latest):
    """Analyze game results and performance."""
    config = ctx.obj['config']
    server_url = config.get_server_url()
    
    if not server_url and not file:
        click.echo("❌ No server URL provided. Use --server or provide --file")
        return
    
    session_data = None
    
    if file:
        # Load from local file
        file_path = Path(file)
        if not file_path.exists():
            click.echo(f"❌ File not found: {file}")
            return
        with open(file_path) as f:
            session_data = json.load(f)
    elif latest or server_url:
        # Fetch from server
        try:
            response = requests.get(f"{server_url}/session_results", timeout=5)
            if response.status_code == 200:
                results = response.json()
                if results.get('files'):
                    latest_file = results['files'][0]
                    
                    # Fetch the actual data
                    data_response = requests.get(
                        f"{server_url}/session_results/{latest_file['filename']}", 
                        timeout=5
                    )
                    if data_response.status_code == 200:
                        session_data = data_response.json()['data']
                    else:
                        click.echo("❌ Failed to fetch session data")
                        return
                else:
                    click.echo("❌ No game results found on server")
                    return
        except Exception as e:
            click.echo(f"❌ Error fetching results: {e}")
            return
    
    if not session_data:
        click.echo("❌ No session data to analyze")
        return
    
    # Analyze the session
    click.echo("\n📊 Game Analysis")
    click.echo("=" * 50)
    click.echo(f"🎮 Session ID: {session_data.get('session_id', 'Unknown')}")
    click.echo(f"🏁 Total Rounds: {session_data.get('total_rounds', 0)}")
    
    if 'cumulative_scores' in session_data:
        click.echo("\n🏆 Final Scores:")
        scores = session_data['cumulative_scores']
        sorted_agents = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        
        for rank, (agent_id, score) in enumerate(sorted_agents, 1):
            medal = ['🥇', '🥈', '🥉'][rank-1] if rank <= 3 else f'{rank}.'
            click.echo(f"   {medal} {agent_id}: {score} points")
    
    if 'performance_trends' in session_data:
        click.echo("\n📈 Performance Trends:")
        trends = session_data['performance_trends']
        
        for agent_id, scores in trends.items():
            trend = "📈" if scores[-1] > scores[0] else "📉" if scores[-1] < scores[0] else "➡️"
            click.echo(f"   {agent_id}: {' → '.join(map(str, scores))} {trend}")
    
    # Detailed round analysis
    if 'rounds' in session_data and session_data['rounds']:
        click.echo("\n🎯 Round Details:")
        for round_data in session_data['rounds']:
            round_num = round_data.get('round_number', '?')
            total_messages = round_data.get('total_messages', 0)
            click.echo(f"\n   Round {round_num}: {total_messages} messages")
            
            if 'agent_scores' in round_data:
                for agent, score in round_data['agent_scores'].items():
                    click.echo(f"      • {agent}: {score} points")


@cli.command()
@click.pass_context
def version(ctx):
    """Show version information."""
    click.echo("The Email Game CLI v0.1.0")
    click.echo("Python " + sys.version.split()[0])
    click.echo(f"Project root: {PROJECT_ROOT}")


if __name__ == '__main__':
    cli()