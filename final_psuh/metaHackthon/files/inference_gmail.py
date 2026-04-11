"""
inference_gmail.py – Run email triage on real Gmail emails
Uses the same AI logic but with real emails from your Gmail inbox.

Usage
-----
python inference_gmail.py                  # run task1 with Gmail emails
python inference_gmail.py --task task2     # run task2
python inference_gmail.py --emails 20      # fetch 20 emails
"""

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from typing import Any, Dict, List

import openai
from dotenv import load_dotenv

from env.gmail_environment import GmailTriageEnv, GmailAction

# Load environment variables
load_dotenv()

# Configuration
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

if not OPENAI_API_KEY:
    print("[ERROR] OPENAI_API_KEY environment variable is not set.", file=sys.stderr)
    sys.exit(1)

client = openai.OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=API_BASE_URL,
)

# System prompt (same as original)
SYSTEM_PROMPT = """You are an AI email triage assistant. Your job is to process emails
and take structured actions based on what you observe.

For each email, respond with a JSON object containing required actions.

## Action Types
- classify_email:   "spam" | "important" | "normal"
- assign_priority:  "high" | "medium" | "low"
- generate_reply:   a professional reply string (empty string "" for spam)

## Classification Rules
- spam: unsolicited, phishing, promotional, suspicious
- important: work-related, requires action, urgent
- normal: informational, personal, does not require urgent action

## Priority Rules
- high: deadline-sensitive, critical, immediate action needed
- medium: needs attention within days, important but not urgent
- low: informational, newsletters, casual emails, spam

## Reply Rules
- Spam emails: leave reply as empty string ""
- Important emails: write a professional, concise reply
- Normal emails: write a brief, friendly reply if needed
- Informational-only emails: leave reply as ""

Always respond with valid JSON only, no prose."""


def call_llm(messages: List[Dict[str, str]], retries: int = 3) -> str:
    """Call the LLM with retry logic."""
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.0,
                max_tokens=600,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content or ""
        except openai.RateLimitError:
            wait = 2 ** attempt
            print(f"  [rate-limit] Waiting {wait}s…", flush=True)
            time.sleep(wait)
        except openai.APIError as exc:
            print(f"  [api-error] {exc}", flush=True)
            if attempt == retries - 1:
                raise
    return ""


def build_user_prompt(obs_dict: Dict[str, Any], task_id: str) -> str:
    """Build the user prompt for the LLM."""
    email = obs_dict.get("current_email", {})
    required_actions = []

    if task_id == "task1":
        required_actions = ["classify_email"]
    elif task_id == "task2":
        required_actions = ["classify_email", "assign_priority"]
    else:
        required_actions = ["classify_email", "assign_priority", "generate_reply"]

    prompt = (
        f"## Email to Process\n"
        f"Subject: {email.get('subject', '')}\n"
        f"From: {email.get('sender', '')}\n\n"
        f"Body:\n{email.get('body', '')}\n\n"
        f"## Required Actions: {required_actions}\n\n"
        f"Respond with a JSON object containing only the required actions."
    )
    return prompt


def run_email_actions(
    env: GmailTriageEnv,
    task_id: str,
    obs_dict: Dict[str, Any],
) -> tuple:
    """Process one email with the LLM."""
    user_msg = build_user_prompt(obs_dict, task_id)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    llm_response = call_llm(messages)

    try:
        data = json.loads(llm_response)
    except Exception:
        data = {}

    classification = data.get("classify_email") or data.get("classification") or "normal"
    priority = data.get("assign_priority") or data.get("priority") or "medium"
    reply_text = data.get("generate_reply") or data.get("reply") or ""

    total_step_reward = 0.0
    done = False

    # Step 1: classify
    action = GmailAction(action_type="classify_email", classification=classification)
    obs, reward, done, info = env.step(action)
    obs_dict = asdict(obs)
    total_step_reward += reward
    print(f"    classify → reward={reward:.2f}  feedback={obs.last_action_feedback}")
    if done:
        return obs_dict, total_step_reward, done, info

    # Step 2: priority
    if task_id in ("task2", "task3"):
        action = GmailAction(action_type="assign_priority", priority=priority)
        obs, reward, done, info = env.step(action)
        obs_dict = asdict(obs)
        total_step_reward += reward
        print(f"    priority  → reward={reward:.2f}  feedback={obs.last_action_feedback}")
        if done:
            return obs_dict, total_step_reward, done, info

    # Step 3: reply
    if task_id == "task3":
        action = GmailAction(action_type="generate_reply", reply=reply_text)
        obs, reward, done, info = env.step(action)
        obs_dict = asdict(obs)
        total_step_reward += reward
        print(f"    reply     → reward={reward:.2f}  feedback={obs.last_action_feedback}")
        if done:
            return obs_dict, total_step_reward, done, info

    return obs_dict, total_step_reward, done, {}


def run_task(task_id: str, email_limit: int = 10) -> Dict[str, Any]:
    """Run a single task with Gmail emails."""
    print(f"\n{'='*60}")
    print(f"  Running Task: {task_id.upper()} (Gmail)")
    print(f"{'='*60}")

    env = GmailTriageEnv(task_id=task_id, email_limit=email_limit)
    obs = env.reset()
    obs_dict = asdict(obs)

    if not obs_dict.get("current_email"):
        print("  ⚠️  No emails to process!")
        return {
            "task_id": task_id,
            "grader_score": 0.0,
            "total_reward": 0.0,
            "steps_taken": 0,
            "episode_log": [],
            "breakdown": {},
        }

    print(f"  Task: {obs.task_name}")
    print(f"  Emails: {obs.current_email.total_emails if obs.current_email else 0}")

    episode_log: List[Dict[str, Any]] = []
    done = False
    final_info: Dict[str, Any] = {}
    email_count = 0

    while not done:
        if not obs_dict.get("current_email"):
            break

        email_id = obs_dict["current_email"]["email_id"]
        email_count += 1
        print(f"\n  [{email_count}] Email ID: {email_id[:16]}...")
        print(f"      Subject: {obs_dict['current_email']['subject'][:60]}")

        obs_dict, step_reward, done, info = run_email_actions(
            env, task_id, obs_dict
        )
        episode_log.append({
            "email_id": email_id,
            "reward": step_reward,
        })
        if info:
            final_info = info

        print(f"      Step reward: {step_reward:.3f}")

    # Finish if not done
    if not done:
        obs, reward, done, final_info = env.step(GmailAction(action_type="finish"))

    grader_score = final_info.get("grader_score", 0.0)
    total_reward = final_info.get("total_reward", 0.0)
    steps = final_info.get("steps_taken", 0)

    print(f"\n  ── Results ──────────────────────────────")
    print(f"  Grader score : {grader_score:.4f}")
    print(f"  Total reward : {total_reward:.4f}")
    print(f"  Steps taken  : {steps}")

    return {
        "task_id": task_id,
        "grader_score": grader_score,
        "total_reward": total_reward,
        "steps_taken": steps,
        "episode_log": episode_log,
        "breakdown": final_info.get("grader_breakdown", {}),
    }


def main():
    parser = argparse.ArgumentParser(
        description="AI Email Triage – Real Gmail Inbox"
    )
    parser.add_argument(
        "--task", choices=["task1", "task2", "task3"], default="task1",
        help="Task to run (default: task1)"
    )
    parser.add_argument(
        "--emails", type=int, default=10,
        help="Number of emails to fetch (default: 10)"
    )
    parser.add_argument(
        "--output", default="results_gmail.json",
        help="JSON file to save results"
    )
    args = parser.parse_args()

    print("\n" + "="*60)
    print("  🚀 Gmail Email Triage System (Real Emails)")
    print("="*60)

    start = time.time()
    result = run_task(args.task, email_limit=args.emails)
    elapsed = time.time() - start

    all_results = {
        "model": MODEL_NAME,
        "api_base": API_BASE_URL,
        "email_source": "Gmail API",
        "task": args.task,
        "result": result,
        "elapsed_seconds": round(elapsed, 2),
    }

    print(f"\n{'='*60}")
    print("  RESULTS")
    print(f"{'='*60}")
    print(f"  Task: {args.task}")
    print(f"  Score: {result['grader_score']:.4f}")
    print(f"  Reward: {result['total_reward']:.4f}")
    print(f"  Time: {elapsed:.1f}s")

    with open(args.output, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    print(f"\n  ✅ Results saved to: {args.output}\n")


if __name__ == "__main__":
    main()
