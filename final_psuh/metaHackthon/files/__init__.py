"""AI Email Triage – environment package."""

from .environment import EmailTriageEnv, Observation, Action, EnvironmentState
from .tasks import TASKS, get_task, list_tasks
from .grader import grade_task1, grade_task2, grade_task3
from .dataset import EMAIL_DATASET

__all__ = [
    "EmailTriageEnv",
    "Observation",
    "Action",
    "EnvironmentState",
    "TASKS",
    "get_task",
    "list_tasks",
    "EMAIL_DATASET",
    "grade_task1",
    "grade_task2",
    "grade_task3",
]
