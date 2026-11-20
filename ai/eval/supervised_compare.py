"""Compare the pinned PR #121 agent and the supervised chat on development conversations.

Run from the repository root with python -m ai.eval.supervised_compare. Live runs
require --bedrock, --case, BEDROCK_MOCK=0, a model and a configured guardrail.
Both arms use the same frozen local demand and rules-based course candidates.
Reports contain observations and counts, never messages, reasons or model outputs.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import statistics
import subprocess
import time
from collections import Counter, OrderedDict
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai import bedrock, guardrail, prompts, recommend, recommend_review
from backend import api, chat
from pipeline import embed
from pipeline.aliases import normalize_skill

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "8dcf025b16ca7e50dae00fa8a8027432b7dbf462"
DEV = ROOT / "data/eval/supervised_conversations_dev.json"
DEMAND = ROOT / "data/sample/demand.json"
JOB = "cloud_engineer"


def pinned_agent(directory: Path):
    """Load only the pinned PR's routing code, prompt and tool-turn script."""
    def extract(path: str) -> Path:
        dest = directory / Path(path).name
        try:
            blob = subprocess.check_output(["git", "cat-file", "blob", f"{BASELINE}:{path}"], cwd=ROOT,
                                           stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            raise RuntimeError(f"PR #121 source is unavailable. Fetch the pinned commit with git fetch origin {BASELINE}") from None
        dest.write_bytes(blob)
        return dest

    def module(path: str):
        file = extract(path)
        spec = importlib.util.spec_from_file_location(f"eval_pr121_{file.stem}", file)
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        return loaded

    prompt = module("ai/prompts/agent_v1.py")
    with patch.object(prompts, "agent_v1", prompt, create=True):
        agent = module("ai/agent.py")
    # Both the PR key selector and the shared Bedrock transport resolve this path.
    agent.MOCK_FILE = str(extract("data/sample/mock_bedrock_agent.json").resolve())
    return agent


class BudgetExceeded(BaseException):
    """Propagate through application fallbacks before a paid call is dispatched."""


@dataclass
class CallBudget:
    limit: int
    name: str = "model"
    used: int = 0

    def take(self):
        if self.used >= self.limit:
            raise BudgetExceeded(self.name)
        self.used += 1


class TurnProbe(ExitStack):
    """Observe shared API boundaries without storing original messages or answers."""

    def __init__(self, model_budget=None, guard_budget=None, embed_budget=None):
        super().__init__()
        self.model_budget, self.guard_budget, self.embed_budget = model_budget, guard_budget, embed_budget
        self.targets, self.source, self.gap_order = [], [], []
        self.worker_errors = []
        self.reviewed = []
        self.mock_calls = self.calls = self.tokens = self.guards = self.embeddings = 0
        self.responses_with_usage = 0
        self.stages = Counter()
        self.gap_called = False
        self.input_guard = None

    def watch(self, owner, attribute, wrapper):
        original = getattr(owner, attribute)
        self.enter_context(patch.object(owner, attribute, wrapper(original)))

    def __enter__(self):
        super().__enter__()

        def model(fn):
            is_tool_loop = fn is bedrock._call_tools

            def call(*args, **kwargs):
                stage = "agent" if is_tool_loop else args[5]
                if bedrock.is_mock():
                    self.stages[stage] += 1
                    self.mock_calls += 1
                    return fn(*args, **kwargs)
                if self.model_budget:
                    self.model_budget.take()
                self.calls += 1
                self.stages[stage] += 1
                result = fn(*args, **kwargs)
                usage = result[2] or {}
                self.tokens += usage.get("totalTokens", 0)
                self.responses_with_usage += int("totalTokens" in usage)
                return result
            return call

        def review_stage(fn):
            def call(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except Exception as error:
                    stage = kwargs.get("mock_key")
                    if stage not in {"recommend_reasons", "recommend_reviews", "recommend_corrections"}:
                        stage = "review"
                    self.worker_errors.append(f"{stage}/{type(error).__name__}")
                    raise
            return call

        def courses(fn):
            def call(job, gap_skills, **kwargs):
                self.targets.extend(gap_skills)
                self.reviewed.append(kwargs.get("review") is True)
                failures_before = len(self.worker_errors)
                try:
                    rows = fn(job, gap_skills, **kwargs)
                except Exception as error:
                    if len(self.worker_errors) == failures_before:
                        self.worker_errors.append(f"recommendation/{type(error).__name__}")
                    raise
                for row in rows:
                    self.source.extend({**c, "gap_skill": row["gap_skill"]} for c in row["courses"])
                return rows
            return call

        def gap(fn):
            def call(*args, **kwargs):
                self.gap_called = True
                rows = fn(*args, **kwargs)
                self.gap_order = [row["skill"] for row in rows if not row["owned"]]
                return rows
            return call

        def assess(fn):
            def call(text, source):
                if not bedrock.is_mock() and guardrail._guardrails_enabled(source):
                    if self.guard_budget:
                        self.guard_budget.take()
                    self.guards += 1
                return fn(text, source)
            return call

        def guard(fn):
            def call(text):
                result = fn(text)
                self.input_guard = ("error" if guardrail.is_check_failure(result)
                                    else "blocked" if result.blocked else "pass")
                return result
            return call

        def embedding(fn):
            def call(*args, **kwargs):
                if self.embed_budget:
                    self.embed_budget.take()
                self.embeddings += 1
                return fn(*args, **kwargs)
            return call

        self.watch(bedrock, "_call", model)
        self.watch(bedrock, "_call_tools", model)
        self.watch(api, "get_recommendations", courses)
        self.watch(recommend_review, "converse_json", review_stage)
        self.watch(api, "get_gap", gap)
        self.watch(guardrail, "bedrock_assess", assess)
        self.watch(guardrail, "check_input", guard)
        self.watch(embed, "_titan", embedding)
        return self


@contextmanager
def cold_case():
    """Keep normal within-conversation caching and isolate the arms from each other."""
    from ai import chat as intake
    from backend import levels
    from pipeline import aliases, search

    # These modules cache immutable catalog files, alias indexes and SDK clients.
    # Start each arm at the same boundary, including nested cached wrappers.
    for module in (api, intake, recommend, levels, aliases, search, bedrock, embed):
        for func in vars(module).values():
            if getattr(func, "__module__", None) != module.__name__:
                continue
            while hasattr(func, "cache_clear"):
                func.cache_clear()
                func = func.__wrapped__
    with patch.object(api, "_recommend_cache", OrderedDict()), TemporaryDirectory() as directory:
        with patch.object(embed, "CACHE_FILE", Path(directory) / "embeddings.json"):
            yield


def agent_turn(agent, history: list, message: str, probe: TurnProbe) -> dict:
    tool_courses = []
    recommend_tool = agent.TOOL_FUNCS["recommend_courses"]

    def observed_tool(payload):
        result = recommend_tool(payload)
        for group in result["recommendations"]:
            tool_courses.extend({**c, "gap_skill": group["skill"]} for c in group["courses"])
        return result

    with patch.dict(agent.TOOL_FUNCS, recommend_courses=observed_tool):
        result = agent.chat(message, list(history))
    history.extend([{"role": "user", "content": message}, {"role": "assistant", "content": result["answer"]}])
    calls = result["tool_calls"]
    tools = [c["name"] for c in calls]
    owned = None
    for call in calls:
        if call["ok"] and call["name"] == "get_gap":
            owned = {normalize_skill(s) or s for s in call["input"]["user_skills"]}
    return dict(text=result["answer"], cards=[], tools=tools, owned=owned, decisions=[], tool_courses=tool_courses,
                action=next((name for name in ("recommend_courses", "get_skill_demand", "get_gap")
                             if name in tools), "answer"), issue=result["uncertain_reason"],
                fixture=agent._mock_key(message) if bedrock.is_mock() and probe.input_guard == "pass" else None)


def supervised_turn(state, message: str) -> dict:
    reply = chat.handle_message(state, message, job=JOB, sidebar_skills=[], sidebar_levels={},
                                architecture="supervised")
    return dict(text=reply.text, cards=reply.courses, tools=[], owned={k for k, owned in state.skills.items() if owned},
                decisions=reply.supervisor_decisions, action=reply.action, issue="reply_failed" if reply.failed else None,
                fixture=None)


def card_evidence(cards: list[dict], source: list[dict]) -> bool:
    """Every visible card must match reviewed source fields exactly."""
    fields = ("gap_skill", "title", "url", "match", "reason")
    verified = {tuple(c[k] for k in fields) for c in source}
    return bool(cards) and all(tuple(c.get(k) for k in fields) in verified for c in cards)


def first_visible_gap(text: str, candidates: list[str]) -> str | None:
    """Resolve the first ranked item actually shown to this arm's user."""
    from ai.chat import mentioned_skills

    for line in text.splitlines():
        if re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", line):
            for skill in mentioned_skills(line):
                if skill in candidates:
                    return skill
    return None


def check_turn(expect: dict, actual: dict, p: TurnProbe, first_gap: str | None, method: str) -> dict:
    checks = {}
    if expect.get("no_courses"):
        checks["no_premature_courses"] = not (p.targets or actual["cards"] or "recommend_courses" in actual["tools"])
    if expect.get("gap"):
        checks["gap"] = p.gap_called and (method == "1" or actual["action"] == "gap")
    if "ownership" in expect:
        owned = actual["owned"]
        checks["correction"] = owned is not None and all(
            (skill in owned) == present for skill, present in expect["ownership"].items())
    if expect.get("clarify"):
        # PR #121 returns only text for clarification. Record that proxy explicitly.
        checks["clarify"] = (("clarify" in actual["decisions"] or actual["action"] == "clarify") if method == "3"
                             else bool(re.search(r"(?:어느|어떤|무엇|말씀하신|뜻하신|맞나요)[^?？]{0,90}[?？]",
                                                actual["text"])) and not p.targets)
    targets = list(dict.fromkeys(p.targets))
    for metric in ("explicit_target", "reference_target"):
        if metric in expect:
            checks[metric] = targets == expect[metric]
    if expect.get("first_gap_target"):
        # A reference cannot succeed if the prior answer never delivered a ranked gap.
        checks["first_gap_target"] = bool(first_gap and targets == [first_gap])
    asks_course = any(k in expect for k in ("explicit_target", "reference_target", "first_gap_target"))
    if asks_course:
        checks["reviewed_course_visible"] = bool(actual["cards"] if method == "3"
                                               else any(c["title"] in actual["text"] for c in p.source))
        if method == "3":
            checks["verbatim_reviewed_delivery"] = all(p.reviewed) and card_evidence(actual["cards"], p.source)
        else:
            # The PR strips an unsupported demand sentence for unobserved skills.
            # Compare against the verified courses as its own tool delivered them.
            verified = {(c["gap_skill"], c["title"]) for c in p.source}
            named = [c for c in actual.get("tool_courses", []) if c["title"] in actual["text"]
                     and (c["gap_skill"], c["title"]) in verified]
            checks["verbatim_reviewed_delivery"] = bool(named) and all(
                c["reason"] in actual["text"] for c in named) and all(p.reviewed)
        if p.source:
            checks["verbatim_among_returned"] = checks["verbatim_reviewed_delivery"]
        if expect.get("no_demand_claim"):
            displayed = " ".join([actual["text"], *(c["reason"] for c in actual["cards"])])
            checks["missing_demand_no_zero_claim"] = (
                bool(p.source) and targets == expect["explicit_target"] and not re.search(r"(?<!\d)0\s*(?:건|%)", displayed))
    return checks


def summarize(rows: list[dict], *, mock: bool, method: str) -> dict:
    metrics = {key for row in rows for key in row["checks"]}
    if "verbatim_reviewed_delivery" in metrics:
        metrics.add("verbatim_among_returned")  # 0/0 means that no reviewed source course was returned.
    counts = {k: {"passed": sum(r["checks"].get(k, False) for r in rows),
                  "applicable": sum(k in r["checks"] for r in rows)} for k in sorted(metrics)}
    seconds = [r["seconds"] for r in rows]
    return {
        "checks": counts, "model_accuracy": None if mock else counts,
        "fixture_keys": dict(Counter(r["fixture"] for r in rows if r.get("fixture"))),
        "turns": len(rows),
        "model_stages": dict(sum((Counter(r.get("stages", {})) for r in rows), Counter())),
        "model_attempts": sum(r["calls"] for r in rows),
        "mock_calls": sum(r["mock_calls"] for r in rows),
        "tokens": None if mock or any(r["tokens"] is None for r in rows) else sum(r["tokens"] for r in rows),
        "guardrail_attempts": sum(r.get("guardrails", 0) for r in rows),
        "embedding_attempts": sum(r.get("embeddings", 0) for r in rows),
        "median_turn_seconds": round(statistics.median(seconds), 4) if seconds else None,
        "errors": dict(Counter(r["issue"] for r in rows if r.get("issue"))),
        "interpretation": ("script replay; no model accuracy" if mock and method == "1" else
                           "offline application checks; no model accuracy" if mock else "development observations"),
    }


def run_case(case, method, agent, budgets):
    state = [] if method == "1" else chat.Conversation()
    rows, first_gap = [], None
    for number, turn in enumerate(case["turns"], start=1):
        started = time.perf_counter()
        with TurnProbe(*budgets) as p:
            try:
                actual = (agent_turn(agent, state, turn["text"], p) if method == "1"
                          else supervised_turn(state, turn["text"]))
            except (BudgetExceeded, Exception) as error:
                code = f"budget_{error}" if isinstance(error, BudgetExceeded) else type(error).__name__
                actual = dict(text="", cards=[], tools=[], owned=None, decisions=[], action="failure",
                              issue=code, fixture=None)
        # no_tool is also returned by the baseline for valid in-scope notes.
        broken = (p.input_guard != "pass" or actual["action"] == "failure"
                  or actual["issue"] not in (None, "no_tool"))
        checks = check_turn(turn["expect"], actual, p, first_gap, method)
        if broken:
            # A correctly routed retrieval can fail in the review or final worker.
            route_keys = {"explicit_target", "reference_target", "first_gap_target"}
            checks = {k: v if p.input_guard == "pass" and k in route_keys else False for k, v in checks.items()}
        interrupted = (actual["issue"] or "").startswith("budget_")
        if interrupted:
            checks = {}
        rows.append(dict(case=case["id"], turn=number, action=actual["action"], decisions=actual["decisions"],
                         fixture=actual["fixture"], tools=actual["tools"], targets=p.targets,
                         worker_errors=p.worker_errors, reviewed_source_courses=len(p.source), visible_cards=len(actual["cards"]),
                         issue=actual["issue"] or (p.input_guard if broken else None), input_guard=p.input_guard,
                         calls=p.calls, mock_calls=p.mock_calls, guardrails=p.guards, embeddings=p.embeddings,
                         stages=dict(p.stages), tokens=None if bedrock.is_mock() or p.responses_with_usage != p.calls else p.tokens,
                         seconds=round(time.perf_counter() - started, 4), checks=checks))
        if interrupted:
            break
        if turn["expect"].get("gap"):
            first_gap = first_visible_gap(actual["text"], p.gap_order)
    return rows


def evaluate(cases, agent, budgets, methods=("1", "3")):
    demand = json.loads(DEMAND.read_text(encoding="utf-8"))
    search = recommend.search_courses

    def fixed_demand(job):
        if job != JOB:
            raise ValueError("Development set only supports cloud_engineer")
        return deepcopy(demand)

    rows = {method: [] for method in methods}
    aborted = None
    with (
        patch.object(api, "get_demand", fixed_demand),
        patch.object(recommend, "search_courses", lambda skill, **kwargs: search(skill, mode="rules")),
        patch.object(embed, "_setup", lambda: None),  # CLI uses the explicitly configured environment.
    ):
        for index, case in enumerate(cases):
            for method in (methods if index % 2 == 0 else reversed(methods)):
                with cold_case():
                    rows[method].extend(run_case(case, method, agent, budgets))
                last = rows[method][-1]
                if (last["issue"] or "").startswith("budget_"):
                    aborted = {"case": case["id"], "method": method, "turn": last["turn"], "reason": last["issue"]}
                    break
            if aborted:
                break
    return {k: {"summary": summarize(v, mock=bedrock.is_mock(), method=k), "rows": v}
            for k, v in rows.items()}, aborted


def fingerprints():
    files = [DEV, DEMAND, ROOT / "data/sample/chat_courses.json", ROOT / "data/aliases.json", ROOT / "data/foundation_courses.json",
             ROOT / "data/courses/courses.csv", ROOT / "data/courses/course_subskill.csv",
             ROOT / "pipeline/courses/queries.csv", ROOT / "pipeline/courses/queries_ai_engineer.csv",
             ROOT / "data/required_levels.json", ROOT / "data/skill_areas.json", ROOT / "data/skill_levels.json",
             *[p for area in ("ai", "ai/prompts", "backend", "pipeline") for p in (ROOT / area).glob("*.py")
               if not p.name.startswith("test_")], Path(__file__)]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="Development case ID. Repeat to select more than one.")
    parser.add_argument("--method", choices=("1", "3"), help="Run one architecture in a fresh session.")
    parser.add_argument("--bedrock", action="store_true")
    parser.add_argument("--max-model-calls", type=int, default=24)
    parser.add_argument("--max-guardrail-calls", type=int, default=8)
    parser.add_argument("--max-embedding-calls", type=int, default=0)
    args = parser.parse_args()
    mock = bedrock.is_mock()
    if args.bedrock == mock:
        parser.error("Live runs require both --bedrock and BEDROCK_MOCK=0")
    limits = (args.max_model_calls, args.max_guardrail_calls, args.max_embedding_calls)
    if min(limits) < 0:
        parser.error("Call limits must be nonnegative")
    if not mock and not (args.case and bedrock.current_model() and all(guardrail.guardrail_config())):
        parser.error("Live runs require --case, BEDROCK_MODEL_ID and guardrail ID/version")
    dataset = json.loads(DEV.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    if args.case:
        unknown = set(args.case) - {c["id"] for c in cases}
        if unknown:
            parser.error(f"Unknown development case(s): {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c["id"] in args.case]
    budgets = tuple(CallBudget(n, name) for n, name in zip(limits, ("model", "guardrail", "embedding")))
    overrides = dict(EXPERIMENT_MODELS="", AB_STRUCTURES="", GUARDRAIL_OUTPUT="0", USAGE_LOG="off",
                     AWS_RETRY_MODE="standard", AWS_MAX_ATTEMPTS="1")
    hashes = fingerprints()
    methods = (args.method,) if args.method else ("1", "3")
    with patch.dict(os.environ, overrides), TemporaryDirectory() as directory:
        agent = pinned_agent(Path(directory)) if "1" in methods else None
        result, aborted = evaluate(cases, agent, budgets, methods)
    meta = {
        "mode": "mock" if mock else "bedrock", "case_ids": [c["id"] for c in cases], "split": "development",
        "methods": methods, "baseline_commit": BASELINE,
        "baseline_prompt": agent.PROMPT_VERSION if agent else None, "job": JOB,
        "workspace_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "sha256": hashes, "model": None if mock else "same configured model",
        "guardrail_version": None if mock else "same configured version",
        "guardrail_topic_block": guardrail.topic_block_enabled(), "guardrail_output": False,
        "demand_source": str(DEMAND.relative_to(ROOT)), "course_retrieval": "local rules",
        "budgets": dict(zip(("model", "guardrail", "embedding"),
                            ({"limit": b.limit, "attempted": b.used} for b in budgets))),
        "interrupted": aborted, "result": result,
    }
    out_dir = ROOT / "outputs/eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"supervised_compare_{meta['mode']}_{time.time_ns()}.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for method, report in result.items():
        print(f"Option {method}", json.dumps(report["summary"], ensure_ascii=False))
    print(path)
    return 2 if aborted else 0


if __name__ == "__main__":
    raise SystemExit(main())
