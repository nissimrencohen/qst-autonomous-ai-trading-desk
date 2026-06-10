"""One-shot scaffolder for the four FastAPI microservices (Step 2).

Generates an identical, production-shaped skeleton per service:
ops endpoints (/health, /ready), 12-factor config, JSON logging,
Dockerfile, pinned requirements, smoke tests.

Idempotent — overwrites scaffold files only. Service-specific logic
(Steps 3-5) replaces `app/main.py` readiness checks and adds routers.
"""
from __future__ import annotations

from pathlib import Path
from string import Template

SERVICES_ROOT = Path(__file__).resolve().parents[1] / "services"

SERVICES: dict[str, dict] = {
    "rag-service": {
        "port": 8001,
        "title": "RAG Service",
        "env_prefix": "RAG",
        "summary": (
            "Retrieves historical financial reports and news from ChromaDB "
            "(HuggingFace embeddings) and summarizes context via AWS Bedrock "
            "or local Llama.cpp."
        ),
    },
    "vision-analyser": {
        "port": 8002,
        "title": "Vision Analyser",
        "env_prefix": "VISION",
        "summary": (
            "Scores technical chart screenshots with PyTorch "
            "(ResNet-50/EfficientNet): support, resistance, breakouts -> "
            "bullish/bearish condition score."
        ),
    },
    "agentic-engine": {
        "port": 8003,
        "title": "Agentic Engine",
        "env_prefix": "AGENTIC",
        "summary": (
            "CrewAI multi-agent team (Technical Analyst, Fundamental Analyst, "
            "Risk Manager) synthesizing RAG and Vision outputs into a "
            "structured JSON probability report."
        ),
    },
    "guardrails-service": {
        "port": 8004,
        "title": "Guardrails Service",
        "env_prefix": "GUARDRAILS",
        "summary": (
            "NeMo Guardrails validation: blocks off-topic/illegal-asset "
            "requests on input; blocks absolute financial guarantees and "
            "hallucinated metrics on output."
        ),
    },
}

INIT_PY = Template('"""$title."""\n\n__version__ = "0.2.0"\n')

CONFIG_PY = Template('''"""Runtime configuration via environment variables (12-factor)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="${env_prefix}_", env_file=".env", extra="ignore"
    )

    service_name: str = "$name"
    port: int = $port
    log_level: str = "INFO"
    environment: str = "dev"


settings = Settings()
''')

LOGGING_PY = Template('''"""Structured JSON logging to stdout (12-factor)."""
from __future__ import annotations

import json
import logging
import sys
import time

from app.config import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "service": settings.service_name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())
''')

MAIN_PY = Template('''"""$title — FastAPI entrypoint.

$summary
"""
from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.logging_conf import configure_logging

configure_logging()

app = FastAPI(title="$title", version=__version__, description="""$summary""")

_STARTED_AT = time.monotonic()


def readiness_checks() -> dict[str, bool]:
    """Dependency probes for /ready. Real checks land with the core logic step."""
    return {"config": True}


@app.get("/health", tags=["ops"])
def health() -> dict:
    """Liveness — the process is up and serving requests."""
    return {
        "status": "ok",
        "service": settings.service_name,
        "version": __version__,
        "uptime_s": round(time.monotonic() - _STARTED_AT, 1),
    }


@app.get("/ready", tags=["ops"])
def ready() -> JSONResponse:
    """Readiness — every service dependency is reachable."""
    checks = readiness_checks()
    ok = all(checks.values())
    return JSONResponse(
        status_code=200 if ok else 503, content={"ready": ok, "checks": checks}
    )
''')

TEST_HEALTH_PY = Template('''"""Smoke tests for the ops endpoints."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["service"] == "$name"


def test_ready_reports_all_checks_passing() -> None:
    res = client.get("/ready")
    assert res.status_code == 200
    body = res.json()
    assert body["ready"] is True
    assert body["checks"] and all(body["checks"].values())
''')

CONFTEST_PY = '"""Puts the service root on sys.path so tests import `app.*`."""\n'

REQUIREMENTS = """fastapi==0.115.*
uvicorn[standard]==0.34.*
pydantic-settings==2.*
"""

DOCKERFILE = Template('''FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

EXPOSE $port

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:$port/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "$port"]
''')

ENV_EXAMPLE = Template('''# $title — environment overrides (prefix: ${env_prefix}_)
${env_prefix}_LOG_LEVEL=INFO
${env_prefix}_ENVIRONMENT=dev
''')

DOCKERIGNORE = """__pycache__/
*.py[cod]
.pytest_cache/
tests/
.env
.venv/
"""


def scaffold(name: str, spec: dict) -> None:
    root = SERVICES_ROOT / name
    ctx = {"name": name, **spec}

    files = {
        root / "app" / "__init__.py": INIT_PY.substitute(ctx),
        root / "app" / "config.py": CONFIG_PY.substitute(ctx),
        root / "app" / "logging_conf.py": LOGGING_PY.substitute(ctx),
        root / "app" / "main.py": MAIN_PY.substitute(ctx),
        root / "tests" / "test_health.py": TEST_HEALTH_PY.substitute(ctx),
        root / "conftest.py": CONFTEST_PY,
        root / "requirements.txt": REQUIREMENTS,
        root / "Dockerfile": DOCKERFILE.substitute(ctx),
        root / ".env.example": ENV_EXAMPLE.substitute(ctx),
        root / ".dockerignore": DOCKERIGNORE,
    }
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"  wrote {path.relative_to(SERVICES_ROOT.parent)}")
    gitkeep = root / ".gitkeep"
    gitkeep.unlink(missing_ok=True)


if __name__ == "__main__":
    for svc_name, svc_spec in SERVICES.items():
        print(f"[{svc_name}]")
        scaffold(svc_name, svc_spec)
