# Changelog

All notable changes to the Autonomous AI Trading Desk are documented here,
newest first. Format loosely follows [Keep a Changelog](https://keepachangelog.com/):
**Added** / **Changed** / **Fixed** / **Docs**.

## [Unreleased]

_(Step 2 — microservice scaffolding — pending)_

## [0.1.0] — 2026-06-10 · Step 1: Repository & documentation initialization

### Added
- Initialized git repository and full directory skeleton: `/docs`, `/services/{rag-service, vision-analyser, agentic-engine, guardrails-service}`, `/frontend/{trading-dashboard, admin-panel}`, `/orchestration/n8n/workflows`, `/infra`, `/data/seed`, `/scripts`, `/tests`.
- `docs/ARCHITECTURE.md` — 4-layer system design, service port map, planned endpoints, data flow, Docker/deployment conventions.
- `docs/TODO_AND_METRICS.md` — requirement checklist mapped to the 7-step execution plan; updated after every verified step.
- `docs/PROMPT_ENGINEERING_LOG.md` — mandatory iteration-log template (V1 baseline → V2/V3 failure-mode iterations → V4/V5 refinement → 10-case pass rate) and index of the 5 prompt families to be tuned.
- Root `README.md` and `.gitignore` (Python, Node, Docker, model artifacts, secrets).
