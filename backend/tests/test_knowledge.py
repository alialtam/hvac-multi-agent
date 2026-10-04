import json
from pathlib import Path
import app.knowledge  # noqa: F401  (registers the rules fallback)
from app.knowledge.rules import diagnose_rules
from app.knowledge.search import search_knowledge
from app.llm import llm


def ev(sig, hits=(), win=None):
    return {"signals": [{"key": k, "value": 1.0, "baseline": 2.0, "z": z} for k, z in sig.items()],
            "rule_hits": list(hits), "window": win or []}


def test_search_finds_filter_manual():
    assert any("Filter" in r["source"] or "filter" in r["source"].lower()
               for r in search_knowledge("airflow dropped filter blocked", 3))


def test_rules_each_cause():
    cases = {
        "filter_blockage": ev({"airflow_cfm": -15, "power_kw": 5, "supply_temp_c": -4}, ["low_airflow"]),
        "compressor_failure": ev({"power_kw": -12, "coil_dt_c": -13, "supply_temp_c": 13}),
        "refrigerant_leak": ev({"coil_dt_c": -4, "supply_temp_c": 4, "power_kw": -1}),
        "sensor_stuck": ev({}, ["sensor_flatline"]),
        "after_hours_waste": ev({}, ["running_unoccupied"]),
        "unknown": ev({}),
    }
    for cause, e in cases.items():
        assert diagnose_rules(e).cause == cause


def test_rules_on_real_ahu1_event():
    f = Path(__file__).parents[2] / "contracts/sample_events/evt_AHU-1_20260930T100300.json"
    d = diagnose_rules(json.loads(f.read_text()))
    assert d.cause == "compressor_failure" and d.sources


def test_llm_falls_back_to_registered_rules():
    llm.set_provider("rules")
    from app.agents.schemas import Diagnosis
    obj, meta = llm.complete("p", Diagnosis, "diagnosis", context=ev({}, ["sensor_flatline"]))
    assert meta["provider"] == "rules" and obj.cause == "sensor_stuck"


def test_rules_never_read_fault_label():
    src = (Path(__file__).parents[1] / "app/knowledge/rules.py").read_text()
    assert "fault_label" not in src