"""
agent_loop.py — Runs email agent continuously on YOUR Gmail
Checks every 15 minutes automatically
"""

import time
import schedule
import json
import os
import sys
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure current script folder is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_log.json")
CHECK_INTERVAL_MINUTES = 10


def log_run(result: dict):
    logs = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f:
                logs = json.load(f)
        except:
            logs = []

    logs.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "score": result.get("grader_score", 0),
        "reward": result.get("total_reward", 0),
        "steps": result.get("steps_taken", 0),
    })

    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)
    print(f"  📝 Log saved → {LOG_FILE}")


def run_agent():
    print(f"\n{'='*60}")
    print(f"  ⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  🤖 Agent Starting...")
    print(f"{'='*60}")

    try:
        from inference_gmail import run_task
        result = run_task(task_id="task3", email_limit=10)
        log_run(result)
        print(f"\n  ✅ Done — Score: {result['grader_score']:.4f} | Reward: {result['total_reward']:.4f}")

    except Exception as e:
        print(f"\n  ❌ Error: {e}")
        # Log the error too
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r") as f:
                    logs = json.load(f)
            except:
                logs = []
        else:
            logs = []

        logs.append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": str(e)
        })
        with open(LOG_FILE, "w") as f:
            json.dump(logs, f, indent=2)

    print(f"\n  ⏳ Next check in {CHECK_INTERVAL_MINUTES} minutes...")
    print(f"  (Press Ctrl+C to stop)\n")


# ── Start ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "="*60)
    print("  🚀 AI Email Agent — Always On")
    print(f"  Checking every {CHECK_INTERVAL_MINUTES} minutes")
    print("="*60)

    # Run once immediately on start
    run_agent()

    # Then repeat every 15 minutes
    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(run_agent)

    while True:
        schedule.run_pending()
        time.sleep(30)