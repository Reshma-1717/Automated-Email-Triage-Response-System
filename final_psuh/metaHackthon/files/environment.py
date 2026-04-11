"""
AI Email Triage System – OpenEnv-compliant environment.

Implements:
  reset()  → Observation
  step()   → (Observation, float, bool, dict)
  state()  → EnvironmentState

The environment simulates an inbox of emails that an AI agent must
classify, prioritise, and (optionally) reply to.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from .dataset import EMAIL_DATASET, get_email_by_id
from .tasks import TaskConfig, get_task, list_tasks, TASKS
from .grader import (
    grade_task1,
    grade_task2,
    grade_task3,
    apply_efficiency_penalty,
)


# ─── Pydantic-style typed models (plain dataclasses for zero extra deps) ──────

@dataclass
class EmailObservation:
    """What the agent sees for the current email."""
    email_id: str
    subject: str
    body: str
    sender: str
    sender_type: str                    # "unknown" | "known"
    email_index: int                    # 0-based index in the task queue
    total_emails: int


@dataclass
class Observation:
    """Full observation returned by reset() and step()."""
    task_id: str
    task_name: str
    task_description: str
    difficulty: str
    current_email: Optional[EmailObservation]
    emails_processed: int
    emails_remaining: int
    last_action_feedback: str           # human-readable feedback from env
    valid_actions: List[str]
    done: bool


@dataclass
class Action:
    """
    An action the agent can take.
    
    action_type values:
      classify_email   – required field: classification (spam|important|normal)
      assign_priority  – required field: priority (high|medium|low)
      generate_reply   – required field: reply (str)
      skip_email       – move to next email without acting (penalty applied)
      finish           – signal end of task
    """
    action_type: str
    classification: Optional[str] = None       # "spam" | "important" | "normal"
    priority: Optional[str] = None             # "high" | "medium" | "low"
    reply: Optional[str] = None


@dataclass
class EnvironmentState:
    """Full internal state (returned by state())."""
    task_id: str
    email_ids: List[str]
    current_email_index: int
    actions_per_email: Dict[str, Dict[str, Any]]   # email_id → collected actions
    step_count: int
    total_reward: float
    done: bool
    errors: List[str]


# ─── Reward constants ──────────────────────────────────────────────────────────

REWARD = {
    "correct_classification": 0.40,
    "wrong_classification":  -0.30,
    "correct_priority":       0.30,
    "off_by_one_priority":    0.10,
    "wrong_priority":        -0.20,
    "good_reply":             0.30,
    "partial_reply":          0.10,
    "bad_reply":             -0.10,
    "skip_penalty":          -0.20,
    "redundant_action":      -0.10,
    "task_completion_bonus":  1.00,
}

VALID_CLASSIFICATIONS = {"spam", "important", "normal"}
VALID_PRIORITIES = {"high", "medium", "low"}


# ─── Environment ──────────────────────────────────────────────────────────────

class EmailTriageEnv:
    """
    OpenEnv-compliant environment for the AI Email Triage task.

    Usage
    -----
    env = EmailTriageEnv(task_id="task1")
    obs = env.reset()
    obs, reward, done, info = env.step(action)
    state = env.state()
    """

    def __init__(self, task_id: str = "task1", seed: Optional[int] = 42):
        if task_id not in TASKS:
            raise ValueError(
                f"Invalid task_id '{task_id}'. Choose from: {list_tasks()}"
            )
        self.task_id = task_id
        self.seed = seed
        self._task: TaskConfig = get_task(task_id)
        self._rng = random.Random(seed)

        # Internal state (initialised by reset)
        self._email_ids: List[str] = []
        self._email_index: int = 0
        self._actions: Dict[str, Dict[str, Any]] = {}
        self._step_count: int = 0
        self._total_reward: float = 0.0
        self._done: bool = False
        self._errors: List[str] = []
        self._initialised: bool = False

    # ── OpenEnv API ────────────────────────────────────────────────────────────

    def reset(self) -> Observation:
        """Initialise a new episode. Returns the first Observation."""
        self._email_ids = list(self._task.email_ids)        # deterministic order
        self._email_index = 0
        self._actions = {}
        self._step_count = 0
        self._total_reward = 0.0
        self._done = False
        self._errors = []
        self._initialised = True
        return self._build_observation("Episode started. Process each email in order.")

    def step(self, action: Action) -> Tuple[Observation, float, bool, Dict[str, Any]]:
        """
        Apply an action and advance the environment.

        Returns
        -------
        observation : Observation
        reward      : float
        done        : bool
        info        : dict  (contains grader results on completion)
        """
        if not self._initialised:
            raise RuntimeError("Call reset() before step().")
        if self._done:
            return self._build_observation("Episode already done."), 0.0, True, {}

        self._step_count += 1
        reward = 0.0
        feedback = ""
        info: Dict[str, Any] = {}

        # ── Validate action type ───────────────────────────────────────────────
        valid_types = {"classify_email", "assign_priority", "generate_reply",
                       "skip_email", "finish"}
        if action.action_type not in valid_types:
            reward = -0.20
            self._total_reward += reward
            return (
                self._build_observation(
                    f"Unknown action type '{action.action_type}'. "
                    f"Valid types: {sorted(valid_types)}"
                ),
                reward,
                False,
                {"error": "invalid_action_type"},
            )

        current_id = self._current_email_id()

        # ── finish ─────────────────────────────────────────────────────────────
        if action.action_type == "finish":
            return self._finalise(info)

        # ── skip_email ─────────────────────────────────────────────────────────
        if action.action_type == "skip_email":
            reward = REWARD["skip_penalty"]
            self._total_reward += reward
            feedback = f"Email {current_id} skipped (penalty applied)."
            self._email_index += 1
            if self._email_index >= len(self._email_ids):
                return self._finalise(info)
            return self._build_observation(feedback), reward, False, {}

        # ── Initialise action record for current email ─────────────────────────
        if current_id not in self._actions:
            self._actions[current_id] = {}

        # ── classify_email ─────────────────────────────────────────────────────
        if action.action_type == "classify_email":
            cls = (action.classification or "").strip().lower()
            if cls not in VALID_CLASSIFICATIONS:
                reward = -0.10
                feedback = (
                    f"Invalid classification '{cls}'. "
                    f"Choose from: {sorted(VALID_CLASSIFICATIONS)}"
                )
            elif "classification" in self._actions[current_id]:
                reward = REWARD["redundant_action"]
                feedback = "Classification already set for this email."
            else:
                truth = self._ground_truth(current_id)["classification"]
                self._actions[current_id]["classification"] = cls
                if cls == truth:
                    reward = REWARD["correct_classification"]
                    feedback = f"Correct classification: '{cls}'."
                else:
                    reward = REWARD["wrong_classification"]
                    feedback = f"Incorrect classification: '{cls}' (expected '{truth}')."

        # ── assign_priority ────────────────────────────────────────────────────
        elif action.action_type == "assign_priority":
            prio = (action.priority or "").strip().lower()
            if prio not in VALID_PRIORITIES:
                reward = -0.10
                feedback = (
                    f"Invalid priority '{prio}'. "
                    f"Choose from: {sorted(VALID_PRIORITIES)}"
                )
            elif "priority" in self._actions[current_id]:
                reward = REWARD["redundant_action"]
                feedback = "Priority already set for this email."
            else:
                truth_prio = self._ground_truth(current_id)["priority"]
                self._actions[current_id]["priority"] = prio
                order = ["low", "medium", "high"]
                diff = abs(order.index(prio) - order.index(truth_prio))
                if diff == 0:
                    reward = REWARD["correct_priority"]
                    feedback = f"Correct priority: '{prio}'."
                elif diff == 1:
                    reward = REWARD["off_by_one_priority"]
                    feedback = f"Priority '{prio}' is close (expected '{truth_prio}')."
                else:
                    reward = REWARD["wrong_priority"]
                    feedback = f"Incorrect priority: '{prio}' (expected '{truth_prio}')."

        # ── generate_reply ─────────────────────────────────────────────────────
        elif action.action_type == "generate_reply":
            reply_text = (action.reply or "").strip()
            if "reply" in self._actions[current_id]:
                reward = REWARD["redundant_action"]
                feedback = "Reply already set for this email."
            else:
                self._actions[current_id]["reply"] = reply_text
                truth_cls = self._ground_truth(current_id)["classification"]
                if truth_cls == "spam":
                    if reply_text == "":
                        reward = 0.10
                        feedback = "Correctly skipped reply for spam email."
                    else:
                        reward = REWARD["bad_reply"]
                        feedback = "Spam emails should not be replied to."
                else:
                    if len(reply_text) < 20:
                        reward = REWARD["bad_reply"]
                        feedback = "Reply is too short or empty."
                    elif len(reply_text) < 50:
                        reward = REWARD["partial_reply"]
                        feedback = "Reply accepted (partial credit – consider more detail)."
                    else:
                        reward = REWARD["good_reply"]
                        feedback = "Reply accepted."

        self._total_reward += reward

        # ── Auto-advance when all required actions for this email are done ─────
        required = set(self._task.required_actions)
        done_actions = set(self._actions.get(current_id, {}).keys())

        # Map action_type → stored key
        action_key_map = {
            "classify_email": "classification",
            "assign_priority": "priority",
            "generate_reply": "reply",
        }
        required_keys = {action_key_map[a] for a in required if a in action_key_map}

        if required_keys.issubset(done_actions):
            self._email_index += 1
            if self._email_index >= len(self._email_ids):
                obs, _, _, fin_info = self._finalise(info)
                fin_info["step_feedback"] = feedback
                return obs, reward, True, fin_info
            feedback += " → Moving to next email."

        obs = self._build_observation(feedback)
        return obs, reward, self._done, info

    def state(self) -> EnvironmentState:
        """Return the full internal state."""
        return EnvironmentState(
            task_id=self.task_id,
            email_ids=list(self._email_ids),
            current_email_index=self._email_index,
            actions_per_email=copy.deepcopy(self._actions),
            step_count=self._step_count,
            total_reward=round(self._total_reward, 4),
            done=self._done,
            errors=list(self._errors),
        )

    # ── Convenience methods ───────────────────────────────────────────────────

    def state_dict(self) -> Dict[str, Any]:
        """Serialise state to a plain dict."""
        return asdict(self.state())

    def observation_dict(self) -> Dict[str, Any]:
        """Return current observation as a plain dict (useful for JSON logging)."""
        obs = self._build_observation("")
        return asdict(obs)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _current_email_id(self) -> Optional[str]:
        if self._email_index < len(self._email_ids):
            return self._email_ids[self._email_index]
        return None

    def _current_email(self) -> Optional[Dict[str, Any]]:
        eid = self._current_email_id()
        if eid is None:
            return None
        return get_email_by_id(eid)

    def _ground_truth(self, email_id: str) -> Dict[str, Any]:
        return get_email_by_id(email_id)["ground_truth"]

    def _build_observation(self, feedback: str) -> Observation:
        email = self._current_email()
        if email:
            current_obs = EmailObservation(
                email_id=email["id"],
                subject=email["subject"],
                body=email["body"],
                sender=email["sender"],
                sender_type=email["sender_type"],
                email_index=self._email_index,
                total_emails=len(self._email_ids),
            )
        else:
            current_obs = None

        valid_actions = self._get_valid_actions()
        return Observation(
            task_id=self.task_id,
            task_name=self._task.name,
            task_description=self._task.description,
            difficulty=self._task.difficulty,
            current_email=current_obs,
            emails_processed=self._email_index,
            emails_remaining=max(0, len(self._email_ids) - self._email_index),
            last_action_feedback=feedback,
            valid_actions=valid_actions,
            done=self._done,
        )

    def _get_valid_actions(self) -> List[str]:
        """Return action types still applicable to the current email."""
        if self._done or self._email_index >= len(self._email_ids):
            return ["finish"]

        current_id = self._current_email_id()
        done_keys = set(self._actions.get(current_id, {}).keys())
        actions: List[str] = []

        if "classification" not in done_keys:
            actions.append("classify_email")
        if "priority" not in done_keys and "assign_priority" in self._task.required_actions:
            actions.append("assign_priority")
        if "reply" not in done_keys and "generate_reply" in self._task.required_actions:
            actions.append("generate_reply")

        actions += ["skip_email", "finish"]
        return actions

    def _finalise(
        self, info: Dict[str, Any]
    ) -> Tuple[Observation, float, bool, Dict[str, Any]]:
        """Run the grader, apply efficiency penalty, and mark episode done."""
        self._done = True

        # Build ground-truth maps
        ground_truths = {
            eid: get_email_by_id(eid)["ground_truth"]
            for eid in self._email_ids
        }
        email_bodies = {
            eid: get_email_by_id(eid)["body"]
            for eid in self._email_ids
        }

        if self.task_id == "task1":
            score, breakdown = grade_task1(self._actions, ground_truths)
        elif self.task_id == "task2":
            score, breakdown = grade_task2(
                self._actions, ground_truths, self._task.scoring_weights
            )
        else:
            score, breakdown = grade_task3(
                self._actions, ground_truths, email_bodies, self._task.scoring_weights
            )

        # Efficiency penalty
        final_score = apply_efficiency_penalty(
            score, self._step_count, self._task.max_steps
        )

        # Completion bonus
        bonus = REWARD["task_completion_bonus"] * final_score
        self._total_reward += bonus

        info.update({
            "grader_score": final_score,
            "grader_breakdown": breakdown,
            "total_reward": round(self._total_reward, 4),
            "steps_taken": self._step_count,
        })

        obs = self._build_observation(
            f"Task complete. Final score: {final_score:.4f} | "
            f"Total reward: {self._total_reward:.4f}"
        )
        return obs, bonus, True, info
