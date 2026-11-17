"""Checks on the data behind the two COPS pages in the Signal app (cops/signal_pages_export.py)."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collector"))

from cops import dashboard_export as DX          # noqa: E402
from cops import signal_pages_export as SPX      # noqa: E402


@pytest.fixture(scope="module")
def data():
    net0 = DX.build_network()
    dec = SPX.build_decisions(net0["cops"])
    net = SPX.build_network(net0, dec.pop("_all"))
    return net, dec


def _empty(v):
    return v is None or v == "" or v == [] or v == {}


def test_all_18_cops_have_connections(data):
    net, _ = data
    assert len(net["cops"]) == 18
    assert [c["order"] for c in net["cops"]] == list(range(1, 19))
    linked = {e["cop_id"] for e in net["edges"]}
    assert linked == {c["id"] for c in net["cops"]}


def test_edges_are_the_60_in_code_plus_13_declared_inputs(data):
    net, _ = data
    declared = [e for e in net["edges"] if e["cop_id"] in SPX.EMPTY_COPS]
    assert len(net["edges"]) == 73 and len(declared) == 13
    assert sum(1 for m in net["metrics"] if not m["declared_only"]) == 26
    for e in declared:
        for k in ("interpretation", "implemented", "used"):
            assert e[k] == {"missing": SPX.NO_CALC}
    assert "not calculated" not in json.dumps(net).lower()


def test_every_decision_answers_13_questions(data):
    _, dec = data
    assert len(dec["decisions"]) >= 108
    for d in dec["decisions"].values():
        items = [i for st in d["explanation"] for i in st["items"]]
        assert [i[0] for i in items] == list(range(1, 14))
        for n, _, v in items:
            assert not _empty(v), (d["decision_id"], n)
            if isinstance(v, dict) and "missing" in v:
                assert v["missing"], (d["decision_id"], n)


def test_actions_never_shown_as_executed_beyond_sandbox(data):
    _, dec = data
    for d in dec["decisions"].values():
        q12 = d["explanation"][3]["items"][1][2]
        assert q12["status"] in SPX.EXEC_MEANING


def test_activation_cases_are_rerun_and_match(data):
    _, dec = data
    g = next(x for x in dec["groups"] if x["id"] == "activation")
    assert len(g["cases"]) == 5
    acts = [d for d in dec["decisions"].values() if d["case"].startswith("activation:")]
    assert acts and all("re-run" in d["explanation"][0]["items"][0][2]["how_it_ran"] for d in acts)


def test_ground_truth_only_where_recorded(data):
    _, dec = data
    for d in dec["decisions"].values():
        q13 = d["explanation"][3]["items"][2][2]
        if d["case"].startswith(("bench:", "harbor:")):
            assert "against" in q13
        else:
            assert q13 == {"missing": "no independent ground truth recorded for this case"}
