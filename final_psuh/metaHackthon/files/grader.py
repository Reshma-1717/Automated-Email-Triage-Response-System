"""
Deterministic grader for the AI Email Triage System.
Evaluates agent actions against ground truth and returns scores in [0.0, 1.0].
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _classification_score(predicted: str, ground_truth: str) -> float:
    """Return 1.0 for correct classification, 0.0 otherwise."""
    return 1.0 if predicted.strip().lower() == ground_truth.strip().lower() else 0.0


def _priority_score(predicted: str, ground_truth: str) -> float:
    """Return 1.0 for exact match, 0.5 for adjacent level, 0.0 for opposite."""
    order = ["low", "medium", "high"]
    pred = predicted.strip().lower()
    truth = ground_truth.strip().lower()
    if pred == truth:
        return 1.0
    if pred in order and truth in order:
        diff = abs(order.index(pred) - order.index(truth))
        return 0.5 if diff == 1 else 0.0
    return 0.0


def _reply_quality_score(
    predicted_reply: Optional[str],
    ground_truth_reply: Optional[str],
    email_body: str,
    classification: str,
) -> float:
    """
    Evaluate reply quality on three dimensions:
      - relevance  (0.0–1.0): does the reply address key topics?
      - tone       (0.0–1.0): is the reply professional and appropriate?
      - completeness (0.0–1.0): does it cover the main points?
    Returns a weighted average.
    """
    # Spam should NOT have a reply
    if classification == "spam":
        if predicted_reply is None or predicted_reply.strip() == "":
            return 1.0          # correctly skipped
        return 0.0              # penalise for replying to spam

    # Non-spam with no ground-truth reply (e.g., booking confirmations)
    if ground_truth_reply is None:
        # A reply is acceptable but not required; partial credit if present
        if predicted_reply and len(predicted_reply.strip()) > 10:
            return 0.5
        return 0.8              # not penalised for not replying

    # Non-spam with required reply but agent skipped it
    if not predicted_reply or len(predicted_reply.strip()) < 10:
        return 0.0

    pred_lower = predicted_reply.lower()
    body_lower = email_body.lower()

    # ── Relevance: check for keyword overlap ──────────────────────────────────
    # Extract meaningful words (>4 chars) from the email body
    body_words = set(re.findall(r"\b[a-z]{4,}\b", body_lower))
    reply_words = set(re.findall(r"\b[a-z]{4,}\b", pred_lower))
    if body_words:
        overlap = len(body_words & reply_words) / len(body_words)
        relevance = min(1.0, overlap * 3)           # scale up; cap at 1.0
    else:
        relevance = 0.5

    # ── Tone: check for polite / professional markers ─────────────────────────
    polite_markers = [
        "thank", "regards", "please", "appreciate", "happy to",
        "let me know", "best", "sincerely", "hi", "hello", "dear",
    ]
    tone_hits = sum(1 for m in polite_markers if m in pred_lower)
    tone = min(1.0, tone_hits / 3)                  # need ≥3 markers for full score

    # ── Completeness: length and paragraph diversity ──────────────────────────
    word_count = len(predicted_reply.split())
    if word_count < 20:
        completeness = 0.3
    elif word_count < 40:
        completeness = 0.6
    elif word_count < 80:
        completeness = 0.85
    else:
        completeness = 1.0

    # Weighted average: relevance most important
    score = 0.45 * relevance + 0.30 * tone + 0.25 * completeness
    return round(score, 4)


# ─── Per-task graders ─────────────────────────────────────────────────────────

def grade_task1(
    actions_per_email: Dict[str, Dict[str, Any]],
    ground_truths: Dict[str, Dict[str, Any]],
) -> Tuple[float, Dict[str, Any]]:
    """
    Task 1 grader: classification only.
    Returns (final_score, breakdown_dict).
    """
    total, correct = 0, 0
    details: Dict[str, Any] = {}

    for email_id, truth in ground_truths.items():
        total += 1
        agent_action = actions_per_email.get(email_id, {})
        pred_class = agent_action.get("classification", "")
        score = _classification_score(pred_class, truth["classification"])
        correct += score
        details[email_id] = {
            "predicted_classification": pred_class,
            "expected_classification": truth["classification"],
            "score": score,
        }

    final_score = round(correct / total, 4) if total else 0.0
    return final_score, {
        "total_emails": total,
        "correct_classifications": correct,
        "details": details,
    }


def grade_task2(
    actions_per_email: Dict[str, Dict[str, Any]],
    ground_truths: Dict[str, Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    Task 2 grader: classification + priority.
    Returns (final_score, breakdown_dict).
    """
    if weights is None:
        weights = {"classification_accuracy": 0.5, "priority_accuracy": 0.5}

    total = len(ground_truths)
    class_total, prio_total = 0.0, 0.0
    details: Dict[str, Any] = {}

    for email_id, truth in ground_truths.items():
        agent_action = actions_per_email.get(email_id, {})
        pred_class = agent_action.get("classification", "")
        pred_prio = agent_action.get("priority", "")

        c_score = _classification_score(pred_class, truth["classification"])
        p_score = _priority_score(pred_prio, truth["priority"])

        class_total += c_score
        prio_total += p_score
        details[email_id] = {
            "predicted_classification": pred_class,
            "expected_classification": truth["classification"],
            "classification_score": c_score,
            "predicted_priority": pred_prio,
            "expected_priority": truth["priority"],
            "priority_score": p_score,
        }

    class_accuracy = class_total / total if total else 0.0
    prio_accuracy = prio_total / total if total else 0.0
    final_score = round(
        weights["classification_accuracy"] * class_accuracy
        + weights["priority_accuracy"] * prio_accuracy,
        4,
    )
    return final_score, {
        "total_emails": total,
        "classification_accuracy": round(class_accuracy, 4),
        "priority_accuracy": round(prio_accuracy, 4),
        "details": details,
    }


def grade_task3(
    actions_per_email: Dict[str, Dict[str, Any]],
    ground_truths: Dict[str, Dict[str, Any]],
    email_bodies: Dict[str, str],
    weights: Optional[Dict[str, float]] = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    Task 3 grader: classification + priority + reply quality.
    Returns (final_score, breakdown_dict).
    """
    if weights is None:
        weights = {
            "classification_accuracy": 0.35,
            "priority_accuracy": 0.35,
            "reply_quality": 0.30,
        }

    total = len(ground_truths)
    class_total, prio_total, reply_total = 0.0, 0.0, 0.0
    details: Dict[str, Any] = {}

    for email_id, truth in ground_truths.items():
        agent_action = actions_per_email.get(email_id, {})
        pred_class = agent_action.get("classification", "")
        pred_prio = agent_action.get("priority", "")
        pred_reply = agent_action.get("reply", None)

        c_score = _classification_score(pred_class, truth["classification"])
        p_score = _priority_score(pred_prio, truth["priority"])
        r_score = _reply_quality_score(
            predicted_reply=pred_reply,
            ground_truth_reply=truth.get("suggested_reply"),
            email_body=email_bodies.get(email_id, ""),
            classification=truth["classification"],
        )

        class_total += c_score
        prio_total += p_score
        reply_total += r_score
        details[email_id] = {
            "predicted_classification": pred_class,
            "expected_classification": truth["classification"],
            "classification_score": c_score,
            "predicted_priority": pred_prio,
            "expected_priority": truth["priority"],
            "priority_score": p_score,
            "reply_score": r_score,
        }

    class_accuracy = class_total / total if total else 0.0
    prio_accuracy = prio_total / total if total else 0.0
    reply_quality = reply_total / total if total else 0.0

    final_score = round(
        weights["classification_accuracy"] * class_accuracy
        + weights["priority_accuracy"] * prio_accuracy
        + weights["reply_quality"] * reply_quality,
        4,
    )
    return final_score, {
        "total_emails": total,
        "classification_accuracy": round(class_accuracy, 4),
        "priority_accuracy": round(prio_accuracy, 4),
        "reply_quality": round(reply_quality, 4),
        "details": details,
    }


# ─── Efficiency penalty ────────────────────────────────────────────────────────

def apply_efficiency_penalty(
    base_score: float,
    steps_taken: int,
    max_steps: int,
    penalty_rate: float = 0.05,
) -> float:
    """
    Deduct a small penalty for excessive steps.
    Starts deducting only after the agent uses >80% of allowed steps.
    """
    threshold = int(max_steps * 0.8)
    if steps_taken <= threshold:
        return base_score
    excess = steps_taken - threshold
    penalty = excess * penalty_rate / max_steps
    return round(max(0.0, base_score - penalty), 4)
