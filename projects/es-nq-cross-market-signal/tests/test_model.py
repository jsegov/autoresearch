from __future__ import annotations

import json
from pathlib import Path

from esnq_signal.model import LinearLogitModel, load_models


def test_linear_logit_predict() -> None:
    model = LinearLogitModel(
        feature_order=("x1", "x2"),
        means=(0.0, 0.0),
        stds=(1.0, 1.0),
        weights=(1.0, -1.0),
        bias=0.0,
    )
    high = model.predict_proba({"x1": 2.0, "x2": 0.5})
    low = model.predict_proba({"x1": 0.0, "x2": 2.0})
    assert high > low
    assert 0.0 <= high <= 1.0


def test_load_models_from_json(tmp_path: Path) -> None:
    payload = {
        "models": {
            "ES": {
                "5": {
                    "p_up": {
                        "feature_order": ["x1"],
                        "means": [0.0],
                        "stds": [1.0],
                        "weights": [1.0],
                        "bias": 0.0,
                    }
                }
            }
        }
    }
    path = tmp_path / "signal_models.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_models(path)
    assert "ES" in loaded
    assert 5 in loaded["ES"]
    assert "p_up" in loaded["ES"][5]

