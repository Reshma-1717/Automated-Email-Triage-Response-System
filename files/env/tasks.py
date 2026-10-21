"""
Task definitions for the AI Email Triage System.
Three tasks of increasing difficulty, each with its own configuration
and the set of emails used for evaluation.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any

# ─── IDs of emails used in each task ──────────────────────────────────────────
TASK1_EMAIL_IDS: List[str] = [
    "spam_001",
    "spam_002",
    "work_001",
    "work_003",
    "personal_001",
    "personal_003",
]

TASK2_EMAIL_IDS: List[str] = [
    "spam_001",
    "spam_003",
    "work_001",
    "work_002",
    "work_004",
    "personal_002",
]

TASK3_EMAIL_IDS: List[str] = [
    "spam_002",
    "spam_004",
    "work_002",
    "work_003",
    "personal_001",
    "personal_003",
]


@dataclass
class TaskConfig:
    task_id: str
    name: str
    description: str
    difficulty: str                         # easy / medium / hard
    email_ids: List[str]
    required_actions: List[str]             # actions the agent must perform
    max_steps: int
    scoring_weights: Dict[str, float]       # how sub-scores are combined
    metadata: Dict[str, Any] = field(default_factory=dict)


TASKS: Dict[str, TaskConfig] = {
    "task1": TaskConfig(
        task_id="task1",
        name="Email Classification",
        description=(
            "Classify each email as 'spam', 'important', or 'normal'. "
            "The agent must read each email and assign the correct category."
        ),
        difficulty="easy",
        email_ids=TASK1_EMAIL_IDS,
        required_actions=["classify_email"],
        max_steps=30,
        scoring_weights={"classification_accuracy": 1.0},
        metadata={
            "hint": "Focus on sender credibility, urgency keywords, and subject tone.",
        },
    ),
    "task2": TaskConfig(
        task_id="task2",
        name="Email Classification + Priority Assignment",
        description=(
            "Classify each email AND assign the correct priority level: "
            "'high', 'medium', or 'low'. Both must be correct for full marks."
        ),
        difficulty="medium",
        email_ids=TASK2_EMAIL_IDS,
        required_actions=["classify_email", "assign_priority"],
        max_steps=40,
        scoring_weights={
            "classification_accuracy": 0.5,
            "priority_accuracy": 0.5,
        },
        metadata={
            "hint": (
                "Important + urgent → high; Important but not urgent → medium; "
                "Informational / personal / spam → low."
            ),
        },
    ),
    "task3": TaskConfig(
        task_id="task3",
        name="Full Triage: Classify, Prioritize & Reply",
        description=(
            "Classify, prioritize, and generate a professional reply for each email "
            "that requires one (spam emails do not need a reply). "
            "Replies are evaluated for tone, relevance, and completeness."
        ),
        difficulty="hard",
        email_ids=TASK3_EMAIL_IDS,
        required_actions=["classify_email", "assign_priority", "generate_reply"],
        max_steps=60,
        scoring_weights={
            "classification_accuracy": 0.35,
            "priority_accuracy": 0.35,
            "reply_quality": 0.30,
        },
        metadata={
            "hint": (
                "For important/normal emails, generate a concise, professional reply "
                "that addresses the key points. Spam emails should be skipped."
            ),
        },
    ),
}


def get_task(task_id: str) -> TaskConfig:
    """Return a TaskConfig by task ID."""
    if task_id not in TASKS:
        raise ValueError(
            f"Unknown task '{task_id}'. Available tasks: {list(TASKS.keys())}"
        )
    return TASKS[task_id]


def list_tasks() -> List[str]:
    """Return list of available task IDs."""
    return list(TASKS.keys())
