"""Tests de métricas de skills y del colector (Fase 8)."""

from satellite.metrics.stats import MetricsCollector, SkillMetrics, build_report
from satellite.skills.models import SkillManifest, SkillStats


def make_manifest(skill_id: str, **stats) -> SkillManifest:
    return SkillManifest(
        id=skill_id,
        description="cuenta palabras de un texto",
        entrypoint="main.py",
        stats=SkillStats(**stats),
    )


def test_skill_metrics_savings_and_break_even() -> None:
    metric = SkillMetrics(
        skill_id="count_words",
        uses=10,
        successes=9,
        failures=1,
        creation_tokens=100,
        baseline_tokens_estimate=50,
        call_cost_tokens=10,
    )
    assert metric.savings_per_use == 40
    assert metric.savings == 10 * 40 - 100
    assert metric.break_even_uses == round(100 / 40, 2)


def test_skill_metrics_no_savings_means_no_break_even() -> None:
    metric = SkillMetrics(skill_id="x", creation_tokens=100, baseline_tokens_estimate=5, call_cost_tokens=5)
    assert metric.savings_per_use == 0
    assert metric.break_even_uses is None


def test_skill_metrics_latency() -> None:
    metric = SkillMetrics(skill_id="x", uses=4, total_latency_ms=200)
    assert metric.avg_latency_ms == 50.0


def test_build_report_marks_baseline_as_estimate() -> None:
    report = build_report([make_manifest("count_words", uses=3, creation_tokens=30, first_resolution_tokens=20)])
    assert report.baseline_is_estimate is True
    assert len(report.skills) == 1
    assert report.skills[0].baseline_tokens_estimate == 20
    assert report.total_savings == 3 * 20 - 30


def test_build_report_accepts_dicts() -> None:
    report = build_report([
        {"id": "x", "stats": {"uses": 2, "first_resolution_tokens": 10, "creation_tokens": 5}}
    ])
    assert report.skills[0].skill_id == "x"
    assert report.total_savings == 2 * 10 - 5


def test_collector_counters() -> None:
    collector = MetricsCollector()
    collector.record_search(True)
    collector.record_search(True)
    collector.record_search(False)
    collector.record_duplicate()
    collector.record_agent_tokens("orchestrator", 100)
    collector.record_agent_tokens("orchestrator", 50)
    collector.record_delegation(25)
    report = build_report([], collector)
    assert report.search_hits == 2
    assert report.search_misses == 1
    assert report.search_accuracy == round(2 / 3, 4)
    assert report.duplicates_avoided == 1
    assert report.agent_tokens["orchestrator"] == 150
    assert report.delegation_tokens == 25


def test_report_sorted_by_savings() -> None:
    report = build_report([
        make_manifest("low", uses=1, first_resolution_tokens=10),
        make_manifest("high", uses=100, first_resolution_tokens=10),
    ])
    assert report.skills[0].skill_id == "high"
