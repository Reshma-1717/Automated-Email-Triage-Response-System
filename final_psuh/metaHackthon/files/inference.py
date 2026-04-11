"""
inference.py – Baseline agent that runs the AI Email Triage environment
               using an OpenAI-compatible LLM API.

Environment variables
---------------------
API_BASE_URL   : Base URL for the OpenAI-compatible API  (default: https://api.openai.com/v1)
MODEL_NAME     : Model identifier                         (default: gpt-4o-mini)
OPENAI_API_KEY : API key (required)

Usage
-----
python inference.py                        # run all tasks
python inference.py --task task1           # run a single task
python inference.py --seed 0 --verbose     # detailed logging
"""

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional

import openai
from dotenv import load_dotenv

from env.environment import EmailTriageEnv, Action
from env.tasks import list_tasks

# Load environment variables from .env file
load_dotenv()


# ─── Configuration ─────────────────────────────────────────────────────────────

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

# ─── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an AI email triage assistant. Your job is to process emails
and take structured actions based on what you observe.

For each email, you must respond with a JSON object containing ONLY the actions
required by the current task. The JSON must be valid and match the schema exactly.

## Action Types
- classify_email:   "spam" | "important" | "normal"
- assign_priority:  "high" | "medium" | "low"
- generate_reply:   a professional reply string (empty string "" for spam)

## Classification Rules
- spam: unsolicited, phishing, promotional from unknown sender
- important: work-related, requires action or response
- normal: informational, personal, does not require urgent action

## Priority Rules
- high: deadline-sensitive, critical business impact, immediate action needed
- medium: needs attention within days, scheduled meetings, project updates
- low: informational, newsletters, casual personal emails, spam

## Reply Rules
- Spam emails: leave reply as empty string ""
- Important emails: write a professional, concise reply addressing key points
- Normal emails needing response: write a brief, friendly reply
- Informational-only emails (confirmations, newsletters): leave reply as ""

Always respond with valid JSON only, no prose."""


def build_user_prompt(obs_dict: Dict[str, Any]) -> str:
    """Construct the user message from an observation dict."""
    email = obs_dict.get("current_email", {})
    task_id = obs_dict.get("task_id", "")
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
        f"From: {email.get('sender', '')} [{email.get('sender_type', 'unknown')}]\n\n"
        f"Body:\n{email.get('body', '')}\n\n"
        f"## Required Actions: {required_actions}\n\n"
        f"Respond with a JSON object containing only the required actions.\n"
        f"Example for task3: "
        f'{{"classify_email": "important", "assign_priority": "high", "generate_reply": "Hi Team..."}}'
    )
    return prompt


def call_llm(messages: List[Dict[str, str]], retries: int = 3) -> str:
    """Call the LLM with retry logic. Returns the response text."""
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.0,        # deterministic
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


def parse_llm_response(text: str, task_id: str) -> Action:
    """Parse the LLM JSON response into an Action object."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fallback: attempt to extract JSON from the text
        import re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}

    # Map JSON keys → Action fields; handle alternative key names gracefully
    classification = (
        data.get("classify_email")
        or data.get("classification")
        or ""
    )
    priority = (
        data.get("assign_priority")
        or data.get("priority")
        or ""
    )
    reply = (
        data.get("generate_reply")
        or data.get("reply")
        or None
    )

    if task_id == "task1":
        return Action(action_type="classify_email", classification=classification)
    elif task_id == "task2":
        return Action(action_type="classify_email", classification=classification,
                      priority=priority)
    else:
        return Action(action_type="classify_email", classification=classification,
                      priority=priority, reply=reply)


# ─── Multi-action step helper ──────────────────────────────────────────────────

def run_email_actions(
    env: EmailTriageEnv,
    task_id: str,
    obs_dict: Dict[str, Any],
    verbose: bool,
) -> tuple:
    """
    For one email, call the LLM once and then apply all required actions
    sequentially via env.step().
    Returns (obs_dict, total_reward_this_email, done).
    """
    user_msg = build_user_prompt(obs_dict)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    llm_response = call_llm(messages)

    if verbose:
        print(f"    LLM response: {llm_response[:200]}")

    # Parse into a "plan"
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
    action = Action(action_type="classify_email", classification=classification)
    obs, reward, done, info = env.step(action)
    obs_dict = asdict(obs)
    total_step_reward += reward
    if verbose:
        print(f"    classify → reward={reward:.2f}  feedback={obs.last_action_feedback}")
    if done:
        return obs_dict, total_step_reward, done, info

    # Step 2 (task2 + task3): priority
    if task_id in ("task2", "task3"):
        action = Action(action_type="assign_priority", priority=priority)
        obs, reward, done, info = env.step(action)
        obs_dict = asdict(obs)
        total_step_reward += reward
        if verbose:
            print(f"    priority  → reward={reward:.2f}  feedback={obs.last_action_feedback}")
        if done:
            return obs_dict, total_step_reward, done, info

    # Step 3 (task3): reply
    if task_id == "task3":
        action = Action(action_type="generate_reply", reply=reply_text)
        obs, reward, done, info = env.step(action)
        obs_dict = asdict(obs)
        total_step_reward += reward
        if verbose:
            print(f"    reply     → reward={reward:.2f}  feedback={obs.last_action_feedback}")
        if done:
            return obs_dict, total_step_reward, done, info

    return obs_dict, total_step_reward, done, {}


# ─── Task runner ──────────────────────────────────────────────────────────────

def run_task(task_id: str, seed: int = 42, verbose: bool = False) -> Dict[str, Any]:
    """Run a single task and return a results dict."""
    print(f"\n{'='*60}")
    print(f"  Running Task: {task_id.upper()}")
    print(f"{'='*60}")

    env = EmailTriageEnv(task_id=task_id, seed=seed)
    obs = env.reset()
    obs_dict = asdict(obs)

    print(f"  Task: {obs.task_name}")
    print(f"  Difficulty: {obs.difficulty}")
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
        print(f"\n  [{email_count}] Email ID: {email_id}")
        print(f"      Subject: {obs_dict['current_email']['subject'][:60]}")

        obs_dict, step_reward, done, info = run_email_actions(
            env, task_id, obs_dict, verbose
        )
        episode_log.append({
            "email_id": email_id,
            "reward": step_reward,
        })
        if info:
            final_info = info

        print(f"      Step reward: {step_reward:.3f}")

    # If loop ended without done signal, call finish
    if not done:
        obs, reward, done, final_info = env.step(Action(action_type="finish"))

    grader_score = final_info.get("grader_score", 0.0)
    total_reward = final_info.get("total_reward", env.state().total_reward)
    steps = final_info.get("steps_taken", env.state().step_count)

    print(f"\n  ── Results ──────────────────────────────")
    print(f"  Grader score : {grader_score:.4f}")
    print(f"  Total reward : {total_reward:.4f}")
    print(f"  Steps taken  : {steps}")
    if "grader_breakdown" in final_info:
        bd = final_info["grader_breakdown"]
        for k, v in bd.items():
            if k != "details":
                print(f"  {k:30s}: {v}")

    return {
        "task_id": task_id,
        "grader_score": grader_score,
        "total_reward": total_reward,
        "steps_taken": steps,
        "episode_log": episode_log,
        "breakdown": final_info.get("grader_breakdown", {}),
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI Email Triage – Inference Script")
    parser.add_argument("--task", choices=list_tasks() + ["all"], default="all",
                        help="Task to run (default: all)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--verbose", action="store_true", help="Print LLM outputs")
    parser.add_argument("--output", default="results.json",
                        help="JSON file to save results")
    args = parser.parse_args()

    tasks_to_run = list_tasks() if args.task == "all" else [args.task]
    all_results: Dict[str, Any] = {
        "model": MODEL_NAME,
        "api_base": API_BASE_URL,
        "seed": args.seed,
        "tasks": {},
    }

    start = time.time()
    for task_id in tasks_to_run:
        result = run_task(task_id, seed=args.seed, verbose=args.verbose)
        all_results["tasks"][task_id] = result

    elapsed = time.time() - start
    all_results["elapsed_seconds"] = round(elapsed, 2)

    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    for tid, res in all_results["tasks"].items():
        print(f"  {tid}: score={res['grader_score']:.4f}  reward={res['total_reward']:.4f}")
    print(f"\n  Total elapsed: {elapsed:.1f}s")

    with open(args.output, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved to: {args.output}")


if __name__ == "__main__":
    main()
