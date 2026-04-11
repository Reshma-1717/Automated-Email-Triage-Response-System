"""
AI Email Triage System package.
"""

from .environment import EmailTriageEnv, Observation, Action, EnvironmentState
from .tasks import TASKS, get_task, list_tasks
from .dataset import EMAIL_DATASET, get_email_by_id, get_emails_by_type

__all__ = [
    "EmailTriageEnv",
    "Observation",
    "Action",
    "EnvironmentState",
    "TASKS",
    "get_task",
    "list_tasks",
    "EMAIL_DATASET",
    "get_email_by_id",
    "get_emails_by_type",
]
