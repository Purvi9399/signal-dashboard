"""Every evaluation scenario, as a pytest case. Expectations live in
cops/runtime/evaluation.py SCENARIOS and were committed before the first run."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.runtime import evaluation as E


@pytest.mark.parametrize("sc", E.SCENARIOS, ids=[s["id"] for s in E.SCENARIOS])
def test_scenario(sc):
    view = sc["run"]({})
    failed = [c for c in (E.check(view, e) for e in sc["expect"]) if not c["passed"]]
    assert not failed, failed


def test_every_llm_record_in_the_evaluation_is_simulated():
    view = E.SCENARIOS[[s["id"] for s in E.SCENARIOS].index("E2E")]["run"]({})
    assert all(c["simulated"] in (True, None) for c in view["cops"].values())
