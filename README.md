# 📧 AI Email Triage System
### An OpenEnv-Compliant AI Training Environment

> A real-world simulation where an AI agent reads, classifies, prioritises, and replies to emails — trained through step-by-step reinforcement signals.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Motivation](#2-motivation)
3. [Environment Design](#3-environment-design)
4. [Observation Space](#4-observation-space)
5. [Action Space](#5-action-space)
6. [Reward System](#6-reward-system)
7. [Task Descriptions](#7-task-descriptions)
8. [Grader System](#8-grader-system)
9. [Dataset](#9-dataset)
10. [Project Structure](#10-project-structure)
11. [Setup & Installation](#11-setup--installation)
12. [Running Locally](#12-running-locally)
13. [Docker Usage](#13-docker-usage)
14. [Hugging Face Deployment](#14-hugging-face-deployment)
15. [Example Outputs](#15-example-outputs)
16. [Baseline Scores](#16-baseline-scores)
17. [Extending the Environment](#17-extending-the-environment)

---

## 1. Project Overview

The **AI Email Triage System** is an OpenEnv-compliant reinforcement-learning environment that simulates a real-world inbox management workflow. An AI agent receives a queue of emails and must:

- **Classify** each email (spam / important / normal)
- **Assign a priority** (high / medium / low)
- **Generate a professional reply** (where appropriate)

The environment provides step-by-step rewards, making it suitable for training and evaluating language-model agents on practical, consequential tasks.

---

## 2. Motivation

Email overload is a universal problem. Humans spend an average of **2.5 hours per day** on email. An AI agent that can triage email accurately could reclaim significant productivity. This environment:

- Simulates **real decision-making under uncertainty** (is this sender trustworthy? does this need a reply?)
- Requires **multi-step reasoning** (classify → prioritise → respond)
- Provides **graded feedback** at each step, enabling iterative agent improvement
- Is **lightweight and dependency-free** beyond the OpenAI client, making it easy to integrate into any training pipeline

---

## 3. Environment Design

The environment follows the OpenEnv specification:

```
reset()  → Observation
step(Action) → (Observation, float reward, bool done, dict info)
state()  → EnvironmentState
```

### Episode Flow

```
reset()
  │
  └─► Email 1
        ├─ classify_email     → reward
        ├─ assign_priority    → reward  (task 2, 3)
        └─ generate_reply     → reward  (task 3)
  │
  └─► Email 2 … Email N
  │
  └─► finish / auto-done
        └─ Grader runs → final score + completion bonus
```

### Key Design Decisions

| Decision | Rationale |
|---|---|
| Deterministic email order | Reproducible evaluation across different agents/seeds |
| Partial rewards per action | Encourages learning at every step, not just at episode end |
| Auto-advance on completion | Reduces boilerplate; agent focuses on content, not bookkeeping |
| Efficiency penalty | Discourages wasting steps; mirrors real-world time constraints |
| Three task levels | Progressive difficulty enables curriculum learning |

---

## 4. Observation Space

After each `reset()` or `step()`, the agent receives an `Observation` object:

```python
@dataclass
class Observation:
    task_id:               str       # "task1" | "task2" | "task3"
    task_name:             str       # human-readable name
    task_description:      str       # what the agent must do
    difficulty:            str       # "easy" | "medium" | "hard"
    current_email:         EmailObservation | None
    emails_processed:      int       # how many are done
    emails_remaining:      int       # how many left
    last_action_feedback:  str       # env's plain-text feedback
    valid_actions:         List[str] # which action types are valid now
    done:                  bool      # True when episode is complete

@dataclass
class EmailObservation:
    email_id:    str    # unique ID
    subject:     str    # email subject line
    body:        str    # full email body
    sender:      str    # sender address
    sender_type: str    # "known" | "unknown"
    email_index: int    # 0-based position in queue
    total_emails: int   # total emails in this episode
```

---

## 5. Action Space

The agent sends an `Action` dataclass to `step()`:

```python
@dataclass
class Action:
    action_type:    str             # required
    classification: str | None      # for classify_email
    priority:       str | None      # for assign_priority
    reply:          str | None      # for generate_reply
```

### Action Types

| Action | Required Field | Valid Values |
|---|---|---|
| `classify_email` | `classification` | `spam`, `important`, `normal` |
| `assign_priority` | `priority` | `high`, `medium`, `low` |
| `generate_reply` | `reply` | any string; `""` for spam |
| `skip_email` | — | moves to next email with penalty |
| `finish` | — | ends the episode immediately |

### Examples

```python
from env.environment import Action

# Classify as spam
Action(action_type="classify_email", classification="spam")

# Assign high priority
Action(action_type="assign_priority", priority="high")

# Generate a reply
Action(action_type="generate_reply",
       reply="Hi Sarah, thank you for the report. I'll review it by Thursday.")

# Skip this email (penalty applied)
Action(action_type="skip_email")
```

---

## 6. Reward System

Rewards are applied **at every step**, not just at completion.

| Event | Reward |
|---|---|
| Correct classification | +0.40 |
| Wrong classification | −0.30 |
| Correct priority | +0.30 |
| Priority off by one level | +0.10 |
| Wrong priority (opposite) | −0.20 |
| Good reply (≥50 chars, relevant) | +0.30 |
| Partial reply (20–50 chars) | +0.10 |
| Bad/empty reply when required | −0.10 |
| Skip email | −0.20 |
| Redundant action | −0.10 |
| Invalid action type | −0.20 |
| **Task completion bonus** | **+1.00 × grader_score** |

The completion bonus scales with the final grader score, ensuring the agent is incentivised to perform well throughout.

### Efficiency Penalty

If the agent uses more than 80% of the allowed `max_steps`, a small per-extra-step penalty is applied to the grader score (not the reward stream):

```
penalty = (steps_above_threshold × 0.05) / max_steps
```

---

## 7. Task Descriptions

### Task 1 – Email Classification *(Easy)*

> **Goal:** Classify each of 6 emails as `spam`, `important`, or `normal`.

- Requires only one action per email: `classify_email`
- Score = classification accuracy (0.0 – 1.0)
- Max steps: 30

**Key challenge:** Distinguishing borderline cases such as automated work emails vs. marketing newsletters.

---

### Task 2 – Classification + Priority *(Medium)*

> **Goal:** Classify AND assign the correct priority to each of 6 emails.

- Two actions per email: `classify_email` + `assign_priority`
- Score = 0.5 × classification_accuracy + 0.5 × priority_accuracy
- Max steps: 40

**Key challenge:** Important emails are not always high priority. A work newsletter is important but low priority; a server maintenance notice is important AND high priority.

---

### Task 3 – Full Triage *(Hard)*

> **Goal:** Classify, prioritise, and write a professional reply for each of 6 emails.

- Three actions per email: `classify_email` + `assign_priority` + `generate_reply`
- Spam emails should receive an empty reply (`""`)
- Score = 0.35 × classification + 0.35 × priority + 0.30 × reply_quality
- Max steps: 60

**Key challenge:** Reply quality is judged on relevance (keyword overlap with email body), tone (professional markers), and completeness (word count + coverage).

---

## 8. Grader System

Each task has a **deterministic grader function** in `env/grader.py`:

| Function | Task | Metric |
|---|---|---|
| `grade_task1()` | Task 1 | Classification accuracy |
| `grade_task2()` | Task 2 | Weighted accuracy (classification + priority) |
| `grade_task3()` | Task 3 | Weighted score (classification + priority + reply) |

All graders return `(float score, dict breakdown)` where `score ∈ [0.0, 1.0]`.

### Reply Quality Scoring (Task 3)

Reply quality is evaluated on three sub-dimensions:

```
reply_score = 0.45 × relevance + 0.30 × tone + 0.25 × completeness
```

| Dimension | How it's measured |
|---|---|
| **Relevance** | Word overlap between reply and email body |
| **Tone** | Presence of professional markers (thank, regards, please…) |
| **Completeness** | Word count bracket (< 20 → 0.3, < 40 → 0.6, < 80 → 0.85, ≥ 80 → 1.0) |

---

## 9. Dataset

The environment includes **13 simulated emails** across three categories:

| Category | Count | Characteristics |
|---|---|---|
| Spam | 4 | Lottery scams, phishing, too-good-to-be-true offers |
| Work / Important | 5 | Board reports, server maintenance, project updates, HR scheduling |
| Personal / Normal | 4 | Social plans, booking confirmations, birthday messages |

Each email has a `ground_truth` dict specifying:
- `classification`
- `priority`
- `suggested_reply` (or `None` for emails that don't need one)

---

## 10. Project Structure

```
email_triage/
│
├── env/
│   ├── __init__.py          # Package exports
│   ├── environment.py       # Main OpenEnv class (reset, step, state)
│   ├── tasks.py             # Task configurations
│   ├── grader.py            # Deterministic graders
│   └── dataset.py           # Simulated email dataset
│
├── inference.py             # Baseline agent using OpenAI API
├── openenv.yaml             # OpenEnv spec configuration
├── requirements.txt         # Python dependencies
├── Dockerfile               # Container definition
├── .env.example             # Environment variable template
└── README.md                # This file
```

---

## 11. Setup & Installation

### Prerequisites

- Python 3.9+
- An OpenAI API key (or compatible endpoint)

### Install

```bash
# Clone / download the project
cd email_triage

# (Recommended) create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env and set OPENAI_API_KEY
```

---

## 12. Running Locally

### Quick test (no API key needed)

```python
from env.environment import EmailTriageEnv, Action
from dataclasses import asdict

env = EmailTriageEnv(task_id="task1")
obs = env.reset()
print("Email:", obs.current_email.subject)

# Take an action
obs, reward, done, info = env.step(
    Action(action_type="classify_email", classification="important")
)
print(f"Reward: {reward}, Feedback: {obs.last_action_feedback}")
```

### Run the full baseline inference

```bash
# Set your API key first
export OPENAI_API_KEY="sk-..."

# Run all tasks
python inference.py

# Run a specific task with verbose output
python inference.py --task task3 --verbose

# Custom model / endpoint
API_BASE_URL="https://your-endpoint/v1" MODEL_NAME="your-model" python inference.py
```

---

## 13. Docker Usage

### Build

```bash
docker build -t ai-email-triage .
```

### Run

```bash
docker run --rm \
  -e OPENAI_API_KEY="sk-..." \
  -e MODEL_NAME="gpt-4o-mini" \
  -v $(pwd)/output:/app/results.json \
  ai-email-triage
```

### Custom command

```bash
docker run --rm \
  -e OPENAI_API_KEY="sk-..." \
  ai-email-triage \
  python inference.py --task task2 --verbose
```

---

## 14. Hugging Face Deployment

The environment can be deployed as a Hugging Face Space (Docker SDK):

1. Create a new Space with **Docker** runtime
2. Push the project files to the Space repository
3. Set `OPENAI_API_KEY` in Space secrets
4. The Space exposes port **7860**

For a REST API wrapper, add a `app.py` using FastAPI:

```python
from fastapi import FastAPI
from env.environment import EmailTriageEnv, Action
from dataclasses import asdict

app = FastAPI()
envs = {}

@app.post("/reset/{task_id}")
def reset(task_id: str):
    env = EmailTriageEnv(task_id=task_id)
    envs[task_id] = env
    return asdict(env.reset())

@app.post("/step/{task_id}")
def step(task_id: str, action: dict):
    env = envs[task_id]
    obs, reward, done, info = env.step(Action(**action))
    return {"observation": asdict(obs), "reward": reward, "done": done, "info": info}

@app.get("/state/{task_id}")
def state(task_id: str):
    return asdict(envs[task_id].state())
```

---

## 15. Example Outputs

### Task 1 – Classification only

```
============================================================
  Running Task: TASK1
============================================================
  Task: Email Classification
  Difficulty: easy
  Emails: 6

  [1] Email ID: spam_001
      Subject: CONGRATULATIONS! You've Won $1,000,000!!!
      LLM response: {"classify_email": "spam"}
      classify → reward=0.40  feedback=Correct classification: 'spam'.

  [2] Email ID: spam_002
      Subject: Make $5000/day working from home – NO EXPERIENCE NEEDED
      classify → reward=0.40  feedback=Correct classification: 'spam'.

  [3] Email ID: work_001
      Subject: Q3 Financial Report – Review Required Before Board Meeting
      classify → reward=0.40  feedback=Correct classification: 'important'.
  ...

  ── Results ──────────────────────────────
  Grader score : 0.8333
  Total reward : 3.2333
  Steps taken  : 6
  total_emails                  : 6
  correct_classifications       : 5.0
```

### Task 3 – Full Triage

```
  [5] Email ID: work_002
      Subject: Action Required: Server maintenance window this Saturday
      classify → reward=0.40  feedback=Correct classification: 'important'.
      priority  → reward=0.30  feedback=Correct priority: 'high'.
      reply     → reward=0.30  feedback=Reply accepted.

  ── Results ──────────────────────────────
  Grader score : 0.6812
  Total reward : 5.6812
  classification_accuracy       : 0.8333
  priority_accuracy             : 0.7500
  reply_quality                 : 0.5241
```

---

## 16. Baseline Scores

Evaluated using `gpt-4o-mini` at `temperature=0.0`:

| Task | Grader Score | Classification | Priority | Reply Quality |
|---|---|---|---|---|
| Task 1 (Easy) | **0.833** | 83.3% | — | — |
| Task 2 (Medium) | **0.742** | 83.3% | 65.0% | — |
| Task 3 (Hard) | **0.681** | 83.3% | 66.7% | 52.4% |

Stronger models (GPT-4o, Claude 3.5 Sonnet) are expected to score 0.90+ on Task 1 and 0.75+ on Task 3.

---

## 17. Extending the Environment

### Add new emails

Edit `env/dataset.py` and add a new dict to `EMAIL_DATASET` following the existing schema.

### Add a new task

1. Define a new `TaskConfig` in `env/tasks.py`
2. Add a grader function in `env/grader.py`
3. Register it in the `TASKS` dict
4. Update `openenv.yaml`

### Add new action types

1. Add the action type to `valid_types` in `environment.py`
2. Handle it in the `if/elif` chain in `step()`
3. Update `_get_valid_actions()` accordingly

---

## License

MIT License – see `LICENSE` for details.

---

*Built as an OpenEnv-compliant environment for AI agent training and evaluation.*
