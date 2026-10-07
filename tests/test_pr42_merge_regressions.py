"""Cross-branch regressions for physical responses and hanchan reductions."""
from collections import Counter
import random

import pytest

from .test_pon_matrix import TILES, command, parse
from .statistics.test_hanchan import FakeModel, context, row
from mortal_app.call_context import base, response_context, response_events
from mortal_app.service import _merge_hanchan, _summarize_hanchan, normalize_candidate


@pytest.mark.parametrize("round_id", [f"{w}{n}" for w in "ESW" for n in range(1, 5)])
@pytest.mark.parametrize("wind,delta", [(w, d) for w in range(4) for d in (1, 2, 3)])
@pytest.mark.parametrize("x", [2, 4])
def test_generated_source_prefix_crosses_laps_once(round_id, wind, delta, x):
    source = (wind + delta) % 4
    cmd = command(round_id, wind, source, x=x, candidate=f"pon:5p[{'东南西北'[source]}],pass")
    # Remove explicit evidence: exercise the generated prefix with source syntax.
    cmd = cmd[:cmd.index("river=")] + cmd[cmd.index(" c=") + 1:]
    state = random.getstate()
    req = parse(cmd)
    assert random.getstate() == state  # no global RNG contamination
    assert req == parse(cmd)
    ct = response_context(req)
    oya = int(round_id[1]) - 1
    assert ct["target_actor"] == (oya + source) % 4
    assert req["discards"][0]["call_from_seat"] == ct["target_actor"]
    total = (x - 2) * 4 + wind + 1 + delta
    expected = [sum(i % 4 == w for i in range(total)) for w in range(4)]
    assert [len(ct["rivers"][(oya + w) % 4]) for w in range(4)] == expected
    assert len(ct["rivers"][ct["target_seat"]]) == x - 1
    ev = response_events(req, ct)
    assert ev[-1]["actor"] == ct["target_actor"] and ev[-1]["pai"] == "5p"
    visible = ct["hand"] + [req["dora"]] + [e[0] for r in ct["rivers"] for e in r]
    assert max(Counter(map(base, visible)).values()) <= 4
    assert all(visible.count(f"5{s}") <= 3 and visible.count(f"0{s}") <= 1 for s in "mps")
    normalized = normalize_candidate(req["discards"][0])
    assert normalized["call_from_seat"] == ct["target_actor"]
    req["discards"][0] = normalized
    assert response_context(req)["target_actor"] == ct["target_actor"]


@pytest.mark.parametrize("wind,delta", [(w, d) for w in range(4) for d in (1, 2, 3)])
def test_explicit_source_matches_evidence_without_padding(wind, delta):
    source = (wind + delta) % 4
    plain = parse(command("S4", wind, source, x=4))
    req = parse(command("S4", wind, source, x=4, candidate=f"pon:5p[{'东南西北'[source]}],pass"))
    assert req["opponent_rivers"] == plain["opponent_rivers"]
    assert req["target_past_discards"] == plain["target_past_discards"]
    from parser import parse_sim_command
    bad_source = (source + 1) % 4
    if bad_source == wind:
        bad_source = (bad_source + 1) % 4
    bad, error = parse_sim_command(command("S4", wind, source, x=4, candidate=f"pon:5p[{'东南西北'[bad_source]}],pass"))
    assert bad is None and "来源不一致" in error
    req["discards"][0]["call_from_seat"] = (req["discards"][0]["call_from_seat"] + 1) % 4
    with pytest.raises(ValueError, match="来源不一致"):
        response_context(req)


@pytest.mark.parametrize("syntax,source", [("[下家]", 0), ("(对家)", 1), ("（上家）", 2), ("[shimocha]", 0), ("[toimen]", 1), ("[kamicha]", 2)])
def test_relative_source_syntax_and_red_consumption(syntax, source):
    req = parse(command("E3", 3, source, called="5m", pair=["0m", "5m", "5m"],
                        candidate=f"pon:5m{syntax}@05m,pass"))
    assert req["discards"][0]["pon_consumed"] == ["0m", "5m"]
    assert req["discards"][0]["call_from_seat"] == (2 + source) % 4


@pytest.mark.parametrize("wind,source", [(w, s) for w in range(4) for s in range(4) if w != s])
def test_first_turn_source_cannot_be_in_the_future(wind, source):
    from parser import parse_sim_command
    cmd = command(wind=wind, source=source, x=1, candidate=f"pon:5p[{'东南西北'[source]}],pass")
    cmd = cmd[:cmd.index("river=")] + cmd[cmd.index(" c=") + 1:]
    req, error = parse_sim_command(cmd)
    if source < wind:
        assert error is None
        assert sum(map(len, response_context(req)["rivers"])) == source + 1
    else:
        assert req is None and "第1巡" in error


def test_category_sampling_uses_local_weighted_roulette(monkeypatch):
    from parser import _generate_default_rivers, REAL_RIVER_PROBS
    original = random.Random
    seen = []
    class RecordingRandom(original):
        def choices(self, population, weights, *, k):
            seen.append((population, weights))
            return super().choices(population, weights=weights, k=k)
    monkeypatch.setattr(random, "Random", RecordingRandom)
    hand = ["5m"] * 2 + TILES[9:20]
    _generate_default_rivers(hand, 3, 0, 4, "5m", call_from_seat=0, dora_indicator="9s")
    assert seen
    for cats, weights in seen:
        assert all(cat in REAL_RIVER_PROBS[1] for cat in cats)
        assert any(weights == [probs[cat] for cat in cats] for probs in REAL_RIVER_PROBS.values())


def hanchan_blocks():
    model = FakeModel([0.5, 0.25, 0.15, 0.1])
    rows = [row(i, scores) for i, scores in enumerate([
        [31000, 23000, 23000, 23000], [24000, 30000, 23000, 23000],
        [21000, 28000, 30000, 21000], [20000, 30000, 28000, 22000],
    ])]
    ctx = context(bakaze="W", kyoku=12)
    return (_summarize_hanchan(rows[:1], ctx, model),
            _summarize_hanchan(rows[1:], ctx, model),
            _summarize_hanchan(rows, ctx, model))


def test_hanchan_merge_matches_unequal_batch_raw_statistics():
    left, right, expected = hanchan_blocks()
    merged = _merge_hanchan(left, right)
    assert merged["sample"] == expected["sample"]
    assert merged["expected_rank"] == expected["expected_rank"]
    assert merged["rank_rates"] == expected["rank_rates"]
    for metric in ["mleague_pt_ev"]:
        assert merged[metric] == expected[metric]
    assert merged["dan_pt_ev"] == expected["dan_pt_ev"]


@pytest.mark.parametrize("field", ["coordinate_system", "continuation_method"])
def test_hanchan_merge_rejects_semantic_mismatch(field):
    left, right, _ = hanchan_blocks()
    right["merge_state"][field] = "old-semantics"
    with pytest.raises(ValueError, match=field):
        _merge_hanchan(left, right)


def test_hanchan_legacy_top_level_metric_still_merges():
    left, right, expected = hanchan_blocks()
    left["merge_state"].pop("mleague_pt_ev")
    right["merge_state"].pop("mleague_pt_ev")
    assert _merge_hanchan(left, right)["mleague_pt_ev"] == expected["mleague_pt_ev"]


@pytest.mark.parametrize("missing", ["left", "right", "both"])
def test_hanchan_missing_metric_is_unavailable_not_zero(missing):
    left, right, _ = hanchan_blocks()
    for block in ([left, right] if missing == "both" else [left if missing == "left" else right]):
        block.pop("mleague_pt_ev")
        block["merge_state"].pop("mleague_pt_ev")
    merged = _merge_hanchan(left, right)
    assert "mleague_pt_ev" not in merged
    assert "mleague_pt_ev" not in merged["merge_state"]
    assert merged["expected_rank"]["n"] == 4


def test_old_response_history_remains_isolated(tmp_path, monkeypatch):
    from mortal_app.history_store import HistoryStore, CandidateAccumulator, compute_canonical_fingerprint
    import mortal_app.call_context as call_module
    req = parse(command())
    corrected = compute_canonical_fingerprint(req)
    with monkeypatch.context() as patch:
        patch.setattr(call_module, "kind", lambda _: "discard")
        old = compute_canonical_fingerprint(req)
    store = HistoryStore(tmp_path / "history.db")
    acc = CandidateAccumulator("pon:5p", "5p", runs=100, sum_score=9000)
    store.save_reduction(old, req, {"pon:5p": acc})
    assert store.get(old)["accumulators"]["pon:5p"].runs == 100
    assert store.get(corrected) is None
