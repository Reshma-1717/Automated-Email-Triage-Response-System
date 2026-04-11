# ──────────────────────────────────────────────────────────────────────────────
# AI Email Triage System – Dockerfile
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Metadata
LABEL maintainer="AI Email Triage Team"
LABEL description="OpenEnv-compliant AI Email Triage environment"
LABEL version="1.0.0"

# Prevent interactive prompts
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Working directory
WORKDIR /app

# Copy dependency spec first (layer caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the full project
COPY . .

# Environment variables (override at runtime)
ENV API_BASE_URL="https://api.openai.com/v1"
ENV MODEL_NAME="gpt-4o-mini"
# OPENAI_API_KEY must be supplied at runtime via -e flag

# Expose port (for future HTTP API / Hugging Face Spaces)
EXPOSE 7860

# Default: run inference across all tasks
CMD ["python", "inference.py", "--task", "all", "--output", "/app/results.json"]
