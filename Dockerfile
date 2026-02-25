FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/SubhanshuMG/oncall-burnout-fix"
LABEL org.opencontainers.image.description="Automated on-call incident triage system"

WORKDIR /app

# Install Ansible for runbook execution
RUN apt-get update && apt-get install -y --no-install-recommends \
    ansible \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN ansible-galaxy collection install kubernetes.core

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
