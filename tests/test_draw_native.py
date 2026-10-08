"""Native traces for draw decisions, including the reported E3 South hand."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "target/release"))
libriichi = pytest.importorskip("libriichi")
if not hasattr(libriichi, "state"):
    pytest.skip("native extension not built", allow_module_level=True)

from .test_pon_native import MaskPolicy
from .test_draw_seat_context import REPORTED, draw_command, parse
from mortal_app.service import _parse_inputs


def kwargs(req):
    req = {**req, "seed": 42, "batch_size": 4, "rayon_threads": 2}
    hand, draw, dora, _, _, _, ctx, _, _, prefix = _parse_inputs(req)
    return dict(
        engine=MaskPolicy(), kyoku=ctx["kyoku"], honba=ctx["honba"], kyotaku=ctx["kyotaku"],
        bakaze=ctx["bakaze"], oya=ctx["oya"], scores=ctx["scores"], dora_marker=dora,
        main_haipai=hand, first_tsumo=draw, first_discard="7m", count=1,
        seed_start=(42, 0xDEAD), target_seat=prefix["target_seat"], x=prefix["x"],
        target_past_discards=prefix["target_past_discards"],
        opponent_rivers=prefix["opponent_rivers"],
    )


@pytest.mark.parametrize("round_id", [f"{w}{n}" for w in "ESW" for n in range(1, 5)])
@pytest.mark.parametrize("wind", range(4))
@pytest.mark.parametrize("x", [1, 2])
def test_native_reaches_exact_turn_and_hand(round_id, wind, x, monkeypatch):
    monkeypatch.setenv("MORTAL_TRACE_EVENTS", "1")
    command, _ = draw_command(round_id, wind, x)
    req = parse(REPORTED if round_id == "E3" and wind == 1 and x == 2 else command)
    args = kwargs(req)
    row = libriichi.arena.CustomKyokuRunner().run_many(**args)[0]
    assert not row["result"].get("error"), row["result"]
    events = [json.loads(e) for e in row["trace_events"]]
    start = next(e for e in events if e["type"] == "start_kyoku")
    assert start["oya"] == args["oya"] and start["scores"] == args["scores"]
    discards = [e for e in events if e["type"] == "dahai"]
    total = 4 * (x - 1) + wind
    assert [e["actor"] for e in discards[:total]] == [(args["oya"] + i) % 4 for i in range(total)]
    assert discards[total]["actor"] == req["target_seat"]
    assert discards[total]["pai"] == "7m"
    # Reconstruct the actual target hand just before its forced decision.
    from collections import Counter
    hand = Counter(start["tehais"][req["target_seat"]])
    for e in events[events.index(start) + 1:]:
        if e == discards[total]:
            break
        if e.get("actor") == req["target_seat"]:
            if e["type"] == "tsumo":
                hand[e["pai"]] += 1
            elif e["type"] == "dahai":
                hand[e["pai"]] -= 1
    assert +hand == Counter(args["main_haipai"] + [args["first_tsumo"]])


@pytest.mark.parametrize("mutation", ["missing", "future", "duplicate"])
def test_native_rejects_raw_inconsistent_rivers_without_truncation(mutation):
    args = kwargs(parse(REPORTED))
    if mutation == "missing":
        args["opponent_rivers"][2].pop()
    elif mutation == "future":
        args["opponent_rivers"][0].append(("C", False, False))
    else:
        args["opponent_rivers"][3].append(("7p", False, False))
    with pytest.raises((ValueError, RuntimeError), match="prefix|target river"):
        libriichi.arena.CustomKyokuRunner().run_many(**args)
