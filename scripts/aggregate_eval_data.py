#!/usr/bin/env python3
"""
QST EVAL Research Lab — Phase 3 Data Aggregation Pipeline
==========================================================
Fuses three data sources into a single dashboard-ready JSON payload:

  1. LOCAL JSONL   — Phase 2 benchmark runner output (latency, status, ticker,
                     swarm_size, model_label, bullish, confidence).

  2. LANGFUSE API  — Per-trace: total cost (USD), LLM token usage, end-to-end
                     latency (ms), and eval scores (faithfulness,
                     answer_relevancy, schema_compliance) attached as Score
                     objects by our eval_hooks pipeline.

  3. PHOENIX API   — Per-run_id: evaluation rows posted by _post_to_phoenix().
                     Provides an independent cross-check of faithfulness /
                     schema_compliance and the hallucination_flag field.

JOIN STRATEGY
-------------
  JSONL  →  Langfuse : run_label is stored as a Langfuse trace tag AND in
                       trace metadata["eval_run_label"]. We filter traces by
                       the experiment tag then match on run_label.
  JSONL  →  Phoenix  : run_id (server UUID) == Phoenix subject_id.document_id.
                       We POST to Phoenix with run_id, so we GET by run_id.

OUTPUT
------
  data/dashboard_ready_data.json   — structured for the Phase 4 Next.js frontend:
    • by_config[]   → bar charts (faithfulness by swarm+model)
    • by_model[]    → bar charts (cost by model)
    • by_swarm[]    → bar charts (quality by swarm)
    • scatter_data[] → cost vs quality scatter plot
    • conclusions   → auto-generated best-config highlights

USAGE
-----
  # Aggregate the latest JSONL in ./data and write dashboard JSON:
    python scripts/aggregate_eval_data.py

  # Point at a specific JSONL file:
    python scripts/aggregate_eval_data.py --jsonl data/eval_results_20260628_123456.jsonl

  # Output to custom path:
    python scripts/aggregate_eval_data.py --output data/my_dashboard.json

  # Skip Langfuse (offline mode):
    python scripts/aggregate_eval_data.py --no-langfuse

  # Skip Phoenix (offline mode):
    python scripts/aggregate_eval_data.py --no-phoenix
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from collections import defaultdict
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

log = logging.getLogger("eval_aggregator")

# ── Constants ──────────────────────────────────────────────────────────────────

EXPERIMENT_NAME = "swarm_size_vs_model_impact"

# Quality composite: faithfulness carries the most weight for a derivatives
# research desk where factual grounding is the primary concern.
_QUALITY_WEIGHTS = {
    "faithfulness":       0.50,
    "answer_relevancy":   0.30,
    "schema_compliance":  0.20,
}

_DATA_DIR = Path("./data")
_DEFAULT_OUTPUT = _DATA_DIR / "dashboard_ready_data.json"


# ══════════════════════════════════════════════════════════════════════════════
# 1.  DATA MODELS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class LocalRun:
    """One row from the Phase 2 JSONL benchmark output."""
    run_label:       str
    experiment_name: str
    ticker:          str
    prompt_id:       str
    swarm_size:      str
    target_model:    str | None
    model_label:     str
    status:          str
    run_id:          str | None = None
    http_status:     int | None = None
    latency_s:       float | None = None
    error:           str | None = None
    started_at:      str = ""
    bullish:         float | None = None
    confidence:      float | None = None
    risk_level:      str | None = None


@dataclass
class LangfuseTrace:
    """Aggregated observability data pulled from Langfuse for one synthesis."""
    run_label:           str
    lf_trace_id:         str
    total_cost_usd:      float | None = None
    total_tokens:        int | None = None
    prompt_tokens:       int | None = None
    completion_tokens:   int | None = None
    latency_ms:          float | None = None  # end-to-end from Langfuse
    # Eval scores attached by eval_hooks._post_to_langfuse()
    schema_compliance:   float | None = None
    faithfulness:        float | None = None
    answer_relevancy:    float | None = None


@dataclass
class PhoenixEval:
    """Eval scores fetched from Arize Phoenix for one run_id."""
    run_id:            str
    schema_compliance: float | None = None
    faithfulness:      float | None = None
    answer_relevancy:  float | None = None
    # True when guardrail output_rail flagged ungrounded prose
    hallucination_flag: bool = False


@dataclass
class ConfigMetrics:
    """Aggregated metrics for one (swarm_size, model_label) configuration."""
    config_id:   str       # e.g. "triad|gemini-2.5-flash"
    swarm_size:  str
    model_label: str
    target_model: str | None
    n_runs:      int

    # Local JSONL metrics
    local_ok:         int   = 0
    local_errors:     int   = 0
    error_rate:       float = 0.0

    # Langfuse cost/perf metrics (None when Langfuse is unavailable)
    avg_cost_usd:      float | None = None
    total_cost_usd:    float | None = None
    avg_latency_lf_ms: float | None = None
    avg_total_tokens:  float | None = None
    avg_prompt_tokens: float | None = None
    avg_compl_tokens:  float | None = None

    # Local latency (always available)
    avg_latency_local_s: float | None = None

    # Eval quality metrics (Langfuse scores, cross-checked against Phoenix)
    avg_faithfulness:     float | None = None
    avg_answer_relevancy: float | None = None
    avg_schema_compliance: float | None = None

    # Derived
    hallucination_rate:   float | None = None  # 1 - avg_schema_compliance
    quality_score:        float | None = None  # weighted composite
    cost_per_quality_unit: float | None = None # cost_usd / quality_score

    # Raw signal averages (always available from JSONL)
    avg_bullish:    float | None = None
    avg_confidence: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# 2.  LOCAL JSONL READER
# ══════════════════════════════════════════════════════════════════════════════

def find_latest_jsonl(data_dir: Path) -> Path | None:
    """Return the most recent eval_results_*.jsonl file in data_dir."""
    candidates = sorted(data_dir.glob("eval_results_*.jsonl"), reverse=True)
    return candidates[0] if candidates else None


def load_local_runs(jsonl_path: Path) -> list[LocalRun]:
    """Parse the Phase 2 JSONL benchmark output into LocalRun objects."""
    runs: list[LocalRun] = []
    valid_fields = {f.name for f in fields(LocalRun)}

    with jsonl_path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                # Filter to only known fields to avoid dataclass errors on extra keys
                filtered = {k: v for k, v in raw.items() if k in valid_fields}
                runs.append(LocalRun(**filtered))
            except (json.JSONDecodeError, TypeError) as exc:
                log.warning("JSONL line %d skipped (%s): %s", lineno, exc, line[:80])

    log.info("Loaded %d local runs from %s", len(runs), jsonl_path)
    return runs


# ══════════════════════════════════════════════════════════════════════════════
# 3.  LANGFUSE DATA FETCHER
# ══════════════════════════════════════════════════════════════════════════════

class LangfuseFetcher:
    """Fetches trace cost/latency/scores from the Langfuse v2 Python SDK.

    Matches traces to local runs via the eval_run_label metadata field set
    in langfuse_tracing.synthesis_trace() during EVAL runs.

    Degrades gracefully: any Langfuse API failure is logged as a WARNING
    and the fetcher returns an empty result, allowing the pipeline to
    continue with only local + Phoenix data.
    """

    # Langfuse logs the LLM model under the litellm provider name; map those to
    # the benchmark model_label so per-model cost can be joined to configs.
    _MODEL_LABEL_MAP = {
        "llama-3.1-8b-instant":    "llama-3.1-8b",
        "llama-3.3-70b-versatile": "llama-3.3-70b",
        "gemini-2.5-flash":        "gemini-2.5-flash",
        "gpt-4o":                  "gpt-4o",
    }

    def __init__(self, public_key: str, secret_key: str, host: str) -> None:
        self._pk   = public_key
        self._sk   = secret_key
        self._host = (host or "").rstrip("/")
        try:
            from langfuse import Langfuse
            self._client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host,
            )
            self._available = True
            log.info("Langfuse fetcher initialised — host=%s", host)
        except ImportError:
            log.warning("langfuse package not available; skipping Langfuse enrichment")
            self._client = None
            self._available = False
        except Exception as exc:
            log.warning("Langfuse client init failed (%s); skipping enrichment", exc)
            self._client = None
            self._available = False

    # ── REST helper ───────────────────────────────────────────────────────────
    # The Python SDK's fetch_traces() returns score *stubs* (name/value are None)
    # and never nests the litellm generations under the synthesis trace, so we go
    # straight to the public REST API for scores and generation cost/tokens.

    def _rest_get(self, path: str, timeout: int = 20) -> dict | None:
        import base64
        auth = base64.b64encode(f"{self._pk}:{self._sk}".encode()).decode()
        url = f"{self._host}{path}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Basic {auth}", "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read())
        except Exception as exc:
            log.debug("Langfuse REST GET %s failed: %s", path, exc)
        return None

    # ── Public API ──────────────────────────────────────────────────────────

    def fetch_experiment_traces(
        self,
        experiment_name: str,
        page_limit: int = 100,
    ) -> dict[str, LangfuseTrace]:
        """Aggregate per-run_label scores + latency for one experiment.

        Returns dict keyed by run_label. Unlike the old implementation we keep
        *every* trace for a label (each label has ~10-30 synthesis traces) and
        average the eval scores across all of them, instead of letting the last
        trace overwrite the rest. Scores are pulled from the REST scores API
        because fetch_traces() only returns id-stubs without values.
        """
        if not self._available:
            return {}

        try:
            # 1. trace_id -> run_label map (+ per-label end-to-end latency)
            tid2label: dict[str, str] = {}
            latencies: dict[str, list[float]] = defaultdict(list)
            page = 1
            while True:
                try:
                    resp = self._client.fetch_traces(
                        tags=[experiment_name], limit=page_limit, page=page,
                    )
                except Exception as exc:
                    log.warning("Langfuse fetch_traces page=%d failed: %s", page, exc)
                    break
                if not resp.data:
                    break
                for trace in resp.data:
                    md = getattr(trace, "metadata", None) or {}
                    if isinstance(md, str):
                        try:
                            md = json.loads(md)
                        except Exception:
                            md = {}
                    label = md.get("eval_run_label") if isinstance(md, dict) else None
                    if not label:
                        label = getattr(trace, "session_id", None) or ""
                    if not label:
                        continue
                    tid2label[str(getattr(trace, "id", ""))] = label
                    lat = getattr(trace, "latency", None)
                    try:
                        if lat and float(lat) > 0:
                            latencies[label].append(float(lat) * 1000.0)
                    except (TypeError, ValueError):
                        pass
                if len(resp.data) < page_limit:
                    break
                page += 1

            # 2. Scores via REST, joined to experiment traces by traceId
            score_acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
            page = 1
            while True:
                body = self._rest_get(f"/api/public/scores?limit=100&page={page}")
                if not body:
                    break
                data = body.get("data") or []
                if not data:
                    break
                for sc in data:
                    tid = sc.get("traceId") or sc.get("trace_id")
                    label = tid2label.get(tid)
                    if not label:
                        continue
                    name = sc.get("name")
                    val = sc.get("value")
                    if name and isinstance(val, (int, float)):
                        score_acc[label][name].append(float(val))
                meta = body.get("meta") or {}
                total_pages = meta.get("totalPages")
                if total_pages is not None and page >= total_pages:
                    break
                if len(data) < 100:
                    break
                page += 1

            # 3. Build one aggregated LangfuseTrace per run_label
            result: dict[str, LangfuseTrace] = {}
            for label in set(tid2label.values()):
                lf = LangfuseTrace(run_label=label, lf_trace_id="")
                if latencies.get(label):
                    lf.latency_ms = round(mean(latencies[label]), 1)
                sc = score_acc.get(label, {})
                if sc.get("faithfulness"):
                    lf.faithfulness = round(mean(sc["faithfulness"]), 6)
                if sc.get("answer_relevancy"):
                    lf.answer_relevancy = round(mean(sc["answer_relevancy"]), 6)
                if sc.get("schema_compliance"):
                    lf.schema_compliance = round(mean(sc["schema_compliance"]), 6)
                result[label] = lf

            scored = sum(
                1 for v in result.values()
                if v.faithfulness is not None or v.schema_compliance is not None
            )
            log.info(
                "Langfuse: aggregated %d labels (%d traces, %d scored) for experiment=%s",
                len(result), len(tid2label), scored, experiment_name,
            )
            return result
        except Exception as exc:
            log.warning("Langfuse fetch_experiment_traces failed (%s); skipping", exc)
            return {}

    def fetch_model_costs(self, sample_pages: int = 40) -> dict[str, dict[str, float]]:
        """Derive {model_label: {cost, tokens}} average-per-call from Langfuse.

        Synthesis traces carry no nested generations — litellm logs each
        completion as its own top-level trace — so per-config cost can't be
        joined directly. Instead we read the public observations API
        (type=GENERATION), which exposes model + calculatedTotalCost + tokens
        per call, and compute a real per-model average. The metrics layer then
        attributes that average to each config by its model_label.

        Only a bounded sample of pages is scanned (averages converge quickly and
        there can be tens of thousands of generations).
        """
        if not self._available:
            return {}

        acc: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "tok": 0.0, "calls": 0.0})
        page = 1
        while page <= sample_pages:
            body = self._rest_get(
                f"/api/public/observations?type=GENERATION&limit=100&page={page}"
            )
            if not body:
                break
            data = body.get("data") or []
            if not data:
                break
            for ob in data:
                model = ob.get("model") or ""
                if not model:
                    continue
                d = acc[model]
                d["calls"] += 1
                cost = ob.get("calculatedTotalCost")
                if isinstance(cost, (int, float)):
                    d["cost"] += float(cost)
                tok = ob.get("totalTokens")
                if isinstance(tok, (int, float)):
                    d["tok"] += float(tok)
            meta = body.get("meta") or {}
            total_pages = meta.get("totalPages")
            if total_pages is not None and page >= total_pages:
                break
            page += 1

        costs: dict[str, dict[str, float]] = {}
        for model, d in acc.items():
            if d["calls"] <= 0 or d["cost"] <= 0:
                continue
            label = self._MODEL_LABEL_MAP.get(model, model)
            costs[label] = {
                "avg_cost_per_call": round(d["cost"] / d["calls"], 8),
                "avg_tokens_per_call": round(d["tok"] / d["calls"], 1),
            }
        log.info("Langfuse: derived avg cost/call for %d models", len(costs))
        return costs


# ══════════════════════════════════════════════════════════════════════════════
# 4.  PHOENIX DATA FETCHER
# ══════════════════════════════════════════════════════════════════════════════

class PhoenixFetcher:
    """Fetches evaluation scores from Arize Phoenix REST API.

    Uses the server run_id as the Phoenix subject_id (document_id), which
    is the same identifier we use when POSTing scores in eval_hooks.py.

    Phoenix REST endpoint used:
      GET /v1/evaluations?document_id=<run_id>
      (falls back to /v1/span_evaluations for older Phoenix versions)
    """

    def __init__(self, endpoint: str) -> None:
        self._base = endpoint.rstrip("/")
        self._available = bool(endpoint)
        if self._available:
            log.info("Phoenix fetcher initialised — endpoint=%s", self._base)

    # ── Public API ──────────────────────────────────────────────────────────

    def fetch_for_runs(self, run_ids: list[str]) -> dict[str, PhoenixEval]:
        """Fetch Phoenix eval scores for a list of run_ids.

        Returns dict keyed by run_id.  Missing/pending traces are silently
        omitted — the pipeline degrades to JSONL-only metrics for those runs.
        """
        if not self._available or not run_ids:
            return {}

        result: dict[str, PhoenixEval] = {}
        for run_id in run_ids:
            if not run_id:
                continue
            try:
                ev = self._fetch_one(run_id)
                if ev is not None:
                    result[run_id] = ev
            except Exception as exc:
                log.debug("Phoenix fetch failed for run_id=%s: %s", run_id, exc)

        log.info("Phoenix: fetched %d eval records", len(result))
        return result

    # ── Internals ────────────────────────────────────────────────────────────

    def _fetch_one(self, run_id: str) -> PhoenixEval | None:
        """Fetch evaluations for a single run_id from Phoenix REST API."""
        # Try Phoenix 4.x/5.x trace annotations endpoint (new OTel-compliant pattern)
        ev = self._try_trace_annotations(run_id)
        if ev is not None:
            return ev

        # Pattern 1: Phoenix ≥ 4.x document evaluation endpoint
        ev = self._try_document_eval(run_id)
        if ev is not None:
            return ev

        # Pattern 2: older Phoenix span evaluations endpoint
        ev = self._try_span_eval(run_id)
        return ev

    def _http_get(self, path: str, timeout: int = 10) -> dict | None:
        url = f"{self._base}{path}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 422):
                log.debug("Phoenix GET %s returned HTTP %d", path, exc.code)
        except Exception as exc:
            log.debug("Phoenix GET %s failed: %s", path, exc)
        return None

    def _try_trace_annotations(self, run_id: str) -> PhoenixEval | None:
        """GET /v1/projects/default/trace_annotations?identifier=<run_id>"""
        path = f"/v1/projects/default/trace_annotations?identifier={urllib.parse.quote(run_id)}"
        body = self._http_get(path)
        if body is None:
            return None
        rows = body.get("data") or []
        return self._parse_eval_rows(run_id, rows)

    def _try_document_eval(self, run_id: str) -> PhoenixEval | None:
        """Phoenix ≥ 4.x: GET /v1/evaluations?document_id=<run_id>"""
        path = f"/v1/evaluations?document_id={urllib.parse.quote(run_id)}"
        body = self._http_get(path)
        if body is None:
            return None
        rows = body.get("data") or body.get("evaluations") or []
        return self._parse_eval_rows(run_id, rows)

    def _try_span_eval(self, run_id: str) -> PhoenixEval | None:
        """Phoenix < 4.x: GET /v1/span_evaluations?span_id=<run_id>"""
        path = f"/v1/span_evaluations?span_id={urllib.parse.quote(run_id)}"
        body = self._http_get(path)
        if body is None:
            return None
        rows = body.get("data") or body.get("evaluations") or []
        return self._parse_eval_rows(run_id, rows)

    @staticmethod
    def _parse_eval_rows(run_id: str, rows: list) -> PhoenixEval | None:
        if not rows:
            return None

        ev = PhoenixEval(run_id=run_id)
        scores: dict[str, list[float]] = defaultdict(list)

        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("name") or row.get("metric_name") or ""
            
            # Handle nested result object in new trace annotations API
            res = row.get("result")
            if isinstance(res, dict):
                score = res.get("score") or res.get("value")
                label = (res.get("label") or "").lower()
            else:
                score = row.get("score") or row.get("value")
                label = (row.get("label") or "").lower()

            if score is not None:
                try:
                    scores[name].append(float(score))
                except (TypeError, ValueError):
                    pass

            # hallucination_flag: Phoenix label="fail" on schema_compliance
            # or any metric named "hallucination"
            if name == "schema_compliance" and label == "fail":
                ev.hallucination_flag = True
            if "hallucin" in name.lower() and label == "fail":
                ev.hallucination_flag = True

        if scores.get("faithfulness"):
            ev.faithfulness = mean(scores["faithfulness"])
        if scores.get("answer_relevancy"):
            ev.answer_relevancy = mean(scores["answer_relevancy"])
        if scores.get("schema_compliance"):
            ev.schema_compliance = mean(scores["schema_compliance"])

        return ev


# ══════════════════════════════════════════════════════════════════════════════
# 5.  METRICS CALCULATOR
# ══════════════════════════════════════════════════════════════════════════════

def _safe_mean(values: list) -> float | None:
    vs = [v for v in values if v is not None]
    return round(mean(vs), 6) if vs else None


def _composite_quality(
    faithfulness: float | None,
    answer_relevancy: float | None,
    schema_compliance: float | None,
) -> float | None:
    """Weighted quality composite for the scatter plot quality axis."""
    score = 0.0
    weight_total = 0.0
    for value, metric in [
        (faithfulness,     "faithfulness"),
        (answer_relevancy, "answer_relevancy"),
        (schema_compliance,"schema_compliance"),
    ]:
        if value is not None:
            w = _QUALITY_WEIGHTS[metric]
            score += value * w
            weight_total += w
    if weight_total == 0:
        return None
    return round(score / weight_total, 4)


def _hallucination_rate(schema_compliance: float | None) -> float | None:
    """Hallucination rate = fraction of runs that failed schema compliance.

    A schema_compliance score < 0.5 (the deepeval threshold we use) means the
    run failed structural invariants — a strong proxy for hallucinated or
    incoherent output from the crew.
    """
    if schema_compliance is None:
        return None
    # schema_compliance is averaged over n runs; each run is 0.0 or 1.0
    # so (1 - avg) directly gives the fraction of failing runs.
    return round(1.0 - schema_compliance, 4)


def calculate_config_metrics(
    local_runs:     list[LocalRun],
    lf_traces:      dict[str, LangfuseTrace],
    phoenix_evals:  dict[str, PhoenixEval],
    model_costs:    dict[str, dict[str, float]] | None = None,
) -> list[ConfigMetrics]:
    """Group runs by (swarm_size, model_label) and compute aggregate metrics."""
    model_costs = model_costs or {}

    # Group local runs by config
    by_config: dict[tuple, list[LocalRun]] = defaultdict(list)
    for r in local_runs:
        key = (r.swarm_size, r.model_label)
        by_config[key].append(r)

    results: list[ConfigMetrics] = []

    for (swarm_size, model_label), runs in sorted(by_config.items()):
        config_id = f"{swarm_size}|{model_label}"
        ok_runs   = [r for r in runs if r.status == "ok"]
        err_runs  = [r for r in runs if r.status == "error"]

        m = ConfigMetrics(
            config_id    = config_id,
            swarm_size   = swarm_size,
            model_label  = model_label,
            target_model = runs[0].target_model,
            n_runs       = len(runs),
            local_ok     = len(ok_runs),
            local_errors  = len(err_runs),
            error_rate   = round(len(err_runs) / max(len(runs), 1), 4),
        )

        # ── Local JSONL metrics (always available) ───────────────────────────
        m.avg_latency_local_s = _safe_mean([r.latency_s for r in ok_runs])
        m.avg_bullish          = _safe_mean([r.bullish   for r in ok_runs])
        m.avg_confidence       = _safe_mean([r.confidence for r in ok_runs])

        # ── Langfuse enrichment ──────────────────────────────────────────────
        # Match by run_label (our primary join key)
        lf_matched = [
            lf_traces[r.run_label]
            for r in ok_runs
            if r.run_label in lf_traces
        ]
        if lf_matched:
            costs      = [t.total_cost_usd    for t in lf_matched if t.total_cost_usd    is not None]
            lats       = [t.latency_ms        for t in lf_matched if t.latency_ms        is not None and t.latency_ms > 0]
            tok_total  = [t.total_tokens      for t in lf_matched if t.total_tokens      is not None]
            tok_prompt = [t.prompt_tokens     for t in lf_matched if t.prompt_tokens     is not None]
            tok_compl  = [t.completion_tokens for t in lf_matched if t.completion_tokens is not None]

            if costs:
                m.avg_cost_usd    = _safe_mean(costs)
                m.total_cost_usd  = round(sum(costs), 8)
            if lats:
                m.avg_latency_lf_ms = _safe_mean(lats)
            if tok_total:
                m.avg_total_tokens  = _safe_mean(tok_total)
                m.avg_prompt_tokens = _safe_mean(tok_prompt)
                m.avg_compl_tokens  = _safe_mean(tok_compl)

            # Langfuse scores (primary source — attached by eval_hooks)
            m.avg_faithfulness      = _safe_mean([t.faithfulness     for t in lf_matched])
            m.avg_answer_relevancy  = _safe_mean([t.answer_relevancy for t in lf_matched])
            m.avg_schema_compliance = _safe_mean([t.schema_compliance for t in lf_matched])

        # ── Per-model cost/tokens (from Langfuse generation observations) ────
        # Synthesis traces have no nested generations, so per-run cost isn't
        # joinable; we attribute the model's average cost/tokens-per-call.
        mc = model_costs.get(model_label)
        if mc:
            if m.avg_cost_usd is None and mc.get("avg_cost_per_call"):
                m.avg_cost_usd = mc["avg_cost_per_call"]
                m.total_cost_usd = round(m.avg_cost_usd * m.local_ok, 8) if m.local_ok else m.avg_cost_usd
            if m.avg_total_tokens is None and mc.get("avg_tokens_per_call"):
                m.avg_total_tokens = mc["avg_tokens_per_call"]

        # ── Phoenix enrichment (cross-check / supplement Langfuse scores) ────
        ph_matched = [
            phoenix_evals[r.run_id]
            for r in ok_runs
            if r.run_id and r.run_id in phoenix_evals
        ]
        if ph_matched:
            # If Langfuse scores are missing, use Phoenix as primary
            if m.avg_faithfulness is None:
                m.avg_faithfulness = _safe_mean(
                    [p.faithfulness for p in ph_matched if p.faithfulness is not None]
                )
            if m.avg_answer_relevancy is None:
                m.avg_answer_relevancy = _safe_mean(
                    [p.answer_relevancy for p in ph_matched if p.answer_relevancy is not None]
                )
            if m.avg_schema_compliance is None:
                m.avg_schema_compliance = _safe_mean(
                    [p.schema_compliance for p in ph_matched if p.schema_compliance is not None]
                )
            # Hallucination flag from Phoenix (cross-check)
            hallucination_flags = [p.hallucination_flag for p in ph_matched]
            phoenix_halluc_rate = round(sum(hallucination_flags) / max(len(hallucination_flags), 1), 4)
            # If we have both sources, take the maximum (more conservative)
            schema_based = _hallucination_rate(m.avg_schema_compliance)
            if schema_based is not None:
                m.hallucination_rate = max(schema_based, phoenix_halluc_rate)
            else:
                m.hallucination_rate = phoenix_halluc_rate
        else:
            m.hallucination_rate = _hallucination_rate(m.avg_schema_compliance)

        # ── Derived quality + cost-efficiency metrics ────────────────────────
        m.quality_score = _composite_quality(
            m.avg_faithfulness,
            m.avg_answer_relevancy,
            m.avg_schema_compliance,
        )
        if m.avg_cost_usd is not None and m.quality_score and m.quality_score > 0:
            m.cost_per_quality_unit = round(m.avg_cost_usd / m.quality_score, 8)

        results.append(m)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 6.  CONCLUSIONS ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def build_conclusions(configs: list[ConfigMetrics]) -> dict[str, Any]:
    """Auto-generate best-config highlights for the Conclusions Panel.

    Picks the top configuration on each metric axis, providing the frontend
    with actionable recommendations without requiring manual analysis.
    """
    conclusions: dict[str, Any] = {}

    def _pick_best(metric: str, higher_is_better: bool = True) -> dict | None:
        candidates = [c for c in configs if getattr(c, metric) is not None]
        if not candidates:
            return None
        best = max(candidates, key=lambda c: getattr(c, metric)) if higher_is_better \
               else min(candidates, key=lambda c: getattr(c, metric))
        return {
            "config_id":   best.config_id,
            "swarm_size":  best.swarm_size,
            "model_label": best.model_label,
            "value":       getattr(best, metric),
            "metric":      metric,
        }

    conclusions["best_quality"]          = _pick_best("quality_score",        higher_is_better=True)
    conclusions["best_faithfulness"]     = _pick_best("avg_faithfulness",     higher_is_better=True)
    conclusions["lowest_cost"]           = _pick_best("avg_cost_usd",         higher_is_better=False)
    conclusions["lowest_latency"]        = _pick_best("avg_latency_lf_ms",    higher_is_better=False)
    conclusions["lowest_hallucination"]  = _pick_best("hallucination_rate",   higher_is_better=False)
    conclusions["best_cost_efficiency"]  = _pick_best("cost_per_quality_unit",higher_is_better=False)

    # Best balanced: highest quality_score among configs with cost ≤ median cost
    cost_vals = [c.avg_cost_usd for c in configs if c.avg_cost_usd is not None]
    if cost_vals:
        median_cost = sorted(cost_vals)[len(cost_vals) // 2]
        balanced_candidates = [
            c for c in configs
            if c.avg_cost_usd is not None
            and c.avg_cost_usd <= median_cost
            and c.quality_score is not None
        ]
        if balanced_candidates:
            best_balanced = max(balanced_candidates, key=lambda c: c.quality_score)  # type: ignore
            conclusions["best_balanced"] = {
                "config_id":   best_balanced.config_id,
                "swarm_size":  best_balanced.swarm_size,
                "model_label": best_balanced.model_label,
                "quality_score": best_balanced.quality_score,
                "avg_cost_usd":  best_balanced.avg_cost_usd,
                "rationale": (
                    f"{best_balanced.model_label} + {best_balanced.swarm_size} achieves "
                    f"quality={best_balanced.quality_score:.3f} at "
                    f"cost=${best_balanced.avg_cost_usd:.6f}/run — "
                    "best quality-to-cost balance among below-median-cost configs."
                ),
            }

    return conclusions


# ══════════════════════════════════════════════════════════════════════════════
# 7.  DASHBOARD PAYLOAD BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_dashboard_payload(
    local_runs:    list[LocalRun],
    configs:       list[ConfigMetrics],
    experiment_name: str = EXPERIMENT_NAME,
) -> dict[str, Any]:
    """Assemble the final dashboard-ready JSON payload.

    Structure:
      meta           → run counts, timestamps, data source availability
      by_config[]    → full ConfigMetrics per (swarm, model) — bar charts
      by_model[]     → metrics grouped by model alone — cost/quality comparison
      by_swarm[]     → metrics grouped by swarm alone — swarm impact
      scatter_data[] → one point per config for the Cost vs Quality scatter
      top_runs[]     → highest-quality individual runs (for a detail table)
      conclusions    → auto-generated best-config recommendations
    """
    now = datetime.now(timezone.utc).isoformat()

    # ── by_model aggregation ─────────────────────────────────────────────────
    model_groups: dict[str, list[ConfigMetrics]] = defaultdict(list)
    for c in configs:
        model_groups[c.model_label].append(c)

    by_model = []
    for ml, group in sorted(model_groups.items()):
        by_model.append({
            "model_label":        ml,
            "target_model":       group[0].target_model,
            "n_configs":          len(group),
            "total_runs":         sum(g.n_runs for g in group),
            "avg_cost_usd":       _safe_mean([g.avg_cost_usd      for g in group]),
            "avg_latency_lf_ms":  _safe_mean([g.avg_latency_lf_ms for g in group]),
            "avg_faithfulness":   _safe_mean([g.avg_faithfulness   for g in group]),
            "avg_quality_score":  _safe_mean([g.quality_score      for g in group]),
            "avg_hallucination":  _safe_mean([g.hallucination_rate for g in group]),
            "avg_total_tokens":   _safe_mean([g.avg_total_tokens   for g in group]),
        })

    # ── by_swarm aggregation ─────────────────────────────────────────────────
    swarm_groups: dict[str, list[ConfigMetrics]] = defaultdict(list)
    for c in configs:
        swarm_groups[c.swarm_size].append(c)

    # Canonical swarm order for the bar chart x-axis
    swarm_order = {"solo": 0, "triad": 1, "full": 2}
    by_swarm = []
    for sw, group in sorted(swarm_groups.items(), key=lambda x: swarm_order.get(x[0], 99)):
        by_swarm.append({
            "swarm_size":         sw,
            "n_configs":          len(group),
            "total_runs":         sum(g.n_runs for g in group),
            "avg_cost_usd":       _safe_mean([g.avg_cost_usd      for g in group]),
            "avg_latency_lf_ms":  _safe_mean([g.avg_latency_lf_ms for g in group]),
            "avg_faithfulness":   _safe_mean([g.avg_faithfulness   for g in group]),
            "avg_quality_score":  _safe_mean([g.quality_score      for g in group]),
            "avg_hallucination":  _safe_mean([g.hallucination_rate for g in group]),
            "avg_total_tokens":   _safe_mean([g.avg_total_tokens   for g in group]),
        })

    # ── Scatter plot data (cost vs quality) ──────────────────────────────────
    # All configs are included so the chart always has data points.
    # When Langfuse is unavailable (offline mode), cost_usd is None and the
    # frontend should fall back to latency_local_s on the x-axis.
    scatter_data = [
        {
            "config_id":             c.config_id,
            "swarm_size":            c.swarm_size,
            "model_label":           c.model_label,
            "cost_usd":              c.avg_cost_usd,              # None in offline mode
            "quality_score":         c.quality_score,             # None in offline mode
            "faithfulness":          c.avg_faithfulness,
            "answer_relevancy":      c.avg_answer_relevancy,
            "schema_compliance":     c.avg_schema_compliance,
            "hallucination_rate":    c.hallucination_rate,
            "latency_ms":            c.avg_latency_lf_ms,
            "latency_local_s":       c.avg_latency_local_s,       # always available
            "avg_confidence":        c.avg_confidence,            # always available
            "avg_bullish":           c.avg_bullish,               # always available
            "total_tokens":          c.avg_total_tokens,
            "cost_per_quality_unit": c.cost_per_quality_unit,
            "n_runs":                c.n_runs,
        }
        for c in configs
    ]

    # ── Top individual runs (for the Run Detail table) ────────────────────────
    ok_runs = sorted(
        [r for r in local_runs if r.status == "ok" and r.confidence is not None],
        key=lambda r: r.confidence or 0,
        reverse=True,
    )[:20]
    top_runs = [
        {
            "run_label":   r.run_label,
            "ticker":      r.ticker,
            "prompt_id":   r.prompt_id,
            "swarm_size":  r.swarm_size,
            "model_label": r.model_label,
            "bullish":     r.bullish,
            "confidence":  r.confidence,
            "risk_level":  r.risk_level,
            "latency_s":   r.latency_s,
            "started_at":  r.started_at,
        }
        for r in ok_runs
    ]

    # ── Meta ─────────────────────────────────────────────────────────────────
    ok_total    = sum(1 for r in local_runs if r.status == "ok")
    err_total   = sum(1 for r in local_runs if r.status == "error")
    lf_enriched = sum(1 for c in configs if c.avg_cost_usd is not None or c.avg_latency_lf_ms is not None)
    ph_enriched = sum(1 for c in configs if c.avg_faithfulness is not None
                      or c.avg_schema_compliance is not None)

    return {
        "generated_at":   now,
        "experiment_name": experiment_name,
        "meta": {
            "total_local_runs":     len(local_runs),
            "ok_runs":              ok_total,
            "error_runs":           err_total,
            "unique_configs":       len(configs),
            "langfuse_enriched_configs": lf_enriched,
            "phoenix_enriched_configs":  ph_enriched,
            "quality_weight_schema": _QUALITY_WEIGHTS,
        },
        "by_config":    [asdict(c) for c in configs],
        "by_model":     by_model,
        "by_swarm":     by_swarm,
        "scatter_data": scatter_data,
        "top_runs":     top_runs,
        "conclusions":  build_conclusions(configs),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 8.  PUBLIC ENTRY-POINT (importable by FastAPI /eval/summary)
# ══════════════════════════════════════════════════════════════════════════════

def run_aggregation(
    jsonl_path:     Path | None = None,
    use_langfuse:   bool = True,
    use_phoenix:    bool = True,
    lf_public_key:  str = "",
    lf_secret_key:  str = "",
    lf_host:        str = "http://langfuse:3000",
    phoenix_endpoint: str = "",
    experiment_name: str = EXPERIMENT_NAME,
) -> dict[str, Any]:
    """Core aggregation logic — usable standalone OR from FastAPI.

    Returns the complete dashboard_ready payload dict.
    Raises ValueError if no JSONL file is found.
    """
    t0 = time.monotonic()

    # ── 1. Load local runs ───────────────────────────────────────────────────
    if jsonl_path is None:
        jsonl_path = find_latest_jsonl(_DATA_DIR)
    if jsonl_path is None or not jsonl_path.exists():
        raise ValueError(
            f"No eval_results_*.jsonl found in {_DATA_DIR}. "
            "Run `python scripts/run_eval_matrix.py` first to generate benchmark data."
        )

    log.info("Loading local runs from %s", jsonl_path)
    local_runs = load_local_runs(jsonl_path)

    if not local_runs:
        raise ValueError(f"JSONL file {jsonl_path} is empty or malformed.")

    # Filter to only the target experiment (JSONL may contain multiple)
    exp_runs = [r for r in local_runs if r.experiment_name == experiment_name]
    if not exp_runs:
        log.warning(
            "No runs matching experiment_name=%r in JSONL; using all %d runs.",
            experiment_name, len(local_runs),
        )
        exp_runs = local_runs

    # ── 2. Fetch Langfuse traces ─────────────────────────────────────────────
    lf_traces: dict[str, LangfuseTrace] = {}
    model_costs: dict[str, dict[str, float]] = {}
    if use_langfuse and lf_public_key and lf_secret_key:
        fetcher = LangfuseFetcher(
            public_key=lf_public_key,
            secret_key=lf_secret_key,
            host=lf_host,
        )
        lf_traces = fetcher.fetch_experiment_traces(experiment_name)
        model_costs = fetcher.fetch_model_costs()
        log.info("Langfuse: enriched %d/%d runs", len(lf_traces), len(exp_runs))
    else:
        log.info("Langfuse: skipped (no keys or disabled)")

    # ── 3. Fetch Phoenix evaluations ─────────────────────────────────────────
    phoenix_evals: dict[str, PhoenixEval] = {}
    if use_phoenix and phoenix_endpoint:
        run_ids = [r.run_id for r in exp_runs if r.run_id]
        ph_fetcher = PhoenixFetcher(phoenix_endpoint)
        phoenix_evals = ph_fetcher.fetch_for_runs(run_ids)
        log.info("Phoenix: enriched %d/%d runs", len(phoenix_evals), len(run_ids))
    else:
        log.info("Phoenix: skipped (no endpoint or disabled)")

    # ── 4. Calculate metrics ─────────────────────────────────────────────────
    configs = calculate_config_metrics(exp_runs, lf_traces, phoenix_evals, model_costs)

    # ── 5. Build dashboard payload ───────────────────────────────────────────
    payload = build_dashboard_payload(exp_runs, configs, experiment_name)
    payload["meta"]["aggregation_elapsed_s"] = round(time.monotonic() - t0, 2)
    payload["meta"]["jsonl_source"] = str(jsonl_path)

    log.info(
        "Aggregation complete: %d configs, %d local runs, %.2fs elapsed",
        len(configs), len(exp_runs), time.monotonic() - t0,
    )
    return payload


# ══════════════════════════════════════════════════════════════════════════════
# 9.  CLI ENTRY-POINT
# ══════════════════════════════════════════════════════════════════════════════

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="QST EVAL Phase 3 — Aggregation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--jsonl",
        type=Path,
        default=None,
        metavar="FILE",
        help="Path to eval_results_*.jsonl. Defaults to the latest file in ./data/",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        metavar="FILE",
        help=f"Output JSON path (default: {_DEFAULT_OUTPUT})",
    )
    p.add_argument(
        "--experiment",
        default=EXPERIMENT_NAME,
        metavar="NAME",
        help=f"Experiment name to filter (default: {EXPERIMENT_NAME})",
    )
    p.add_argument("--no-langfuse", action="store_true", help="Skip Langfuse enrichment")
    p.add_argument("--no-phoenix",  action="store_true", help="Skip Phoenix enrichment")
    p.add_argument(
        "--langfuse-host",
        default=os.getenv("AGENTIC_LANGFUSE_HOST", "http://localhost:3000"),
        help="Langfuse host URL",
    )
    p.add_argument(
        "--phoenix-endpoint",
        default=os.getenv("AGENTIC_PHOENIX_ENDPOINT", "http://localhost:6006"),
        help="Arize Phoenix base URL",
    )
    p.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the output JSON (larger file, easier to read)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Read API keys from environment (same vars as the agentic-engine)
    lf_pk = os.getenv("AGENTIC_LANGFUSE_PUBLIC_KEY", "")
    lf_sk = os.getenv("AGENTIC_LANGFUSE_SECRET_KEY", "")

    try:
        payload = run_aggregation(
            jsonl_path       = args.jsonl,
            use_langfuse     = not args.no_langfuse,
            use_phoenix      = not args.no_phoenix,
            lf_public_key    = lf_pk,
            lf_secret_key    = lf_sk,
            lf_host          = args.langfuse_host,
            phoenix_endpoint = args.phoenix_endpoint,
            experiment_name  = args.experiment,
        )
    except ValueError as exc:
        log.error("%s", exc)
        sys.exit(1)

    # ── Write dashboard JSON ─────────────────────────────────────────────────
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        indent = 2 if args.pretty else None
        json.dump(payload, fh, indent=indent, default=str, ensure_ascii=False)
        fh.write("\n")

    meta = payload["meta"]
    print(f"\n[OK] dashboard_ready_data written -> {args.output}")
    print(f"     {meta['total_local_runs']} runs | "
          f"{meta['unique_configs']} configs | "
          f"Langfuse: {meta['langfuse_enriched_configs']} | "
          f"Phoenix: {meta['phoenix_enriched_configs']} | "
          f"elapsed: {meta.get('aggregation_elapsed_s', '?')}s")

    # Print conclusions summary
    conclusions = payload.get("conclusions", {})
    if conclusions.get("best_balanced"):
        bb = conclusions["best_balanced"]
        print(f"\n[BEST BALANCED]  {bb['config_id']}  —  {bb.get('rationale', '')}")
    if conclusions.get("lowest_cost"):
        lc = conclusions["lowest_cost"]
        print(f"[CHEAPEST]       {lc['config_id']}  avg_cost=${lc['value']:.6f}/run")
    if conclusions.get("best_quality"):
        bq = conclusions["best_quality"]
        print(f"[BEST QUALITY]   {bq['config_id']}  quality_score={bq['value']:.4f}")
    print()


if __name__ == "__main__":
    main()
