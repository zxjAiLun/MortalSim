"""Current wind/turn semantics through parser, API, worker and renderer."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot/src"))
from parser import parse_sim_command
from render_png import _table_snapshot
from apps.api.models import RunRequest
from mortal_app.call_context import seat_coordinates, tiles, validate_draw_rivers
from mortal_app.service import resolve_simulation_context

REPORTED = "/sim 255788m44p33s1155z d4m c=7m,7mr E3-1 seat=南 x=2 河=2z,9st/7p/2z/9m P308,169,211,312 500"


def parse(command):
    req, error = parse_sim_command(command)
    assert error is None, error
    return req


def draw_command(round_id, wind, x):
    excluded = set(tiles("255788m44p33s1155z")) | {"3m"}
    pool = iter(t for suit in "mpsz" for n in range(1, 8 if suit == "z" else 10)
                if (t := f"{n}{suit}") not in excluded)
    rivers = [[next(pool) for _ in range(x - 1 + int(w < wind))] for w in range(4)]
    text = "/".join(",".join(row) for row in rivers)
    command = (f"/sim 255788m44p33s1155z d4m c=7m,7mr {round_id}-1 "
               f"seat={'东南西北'[wind]} x={x} 河={text} P308,169,211,312 1")
    return command, rivers


def test_reported_e3_south_keeps_turn_rivers_scores_and_display():
    req = parse(REPORTED)
    validated = RunRequest(**req).model_dump(mode="json", by_alias=True)
    validate_draw_rivers(validated)
    assert seat_coordinates(validated) == (2, 3, 1)
    assert validated["x"] == 2 and validated["runs"] == 500
    assert [len(r) for r in req["opponent_rivers"]] == [1, 1, 2, 0]
    assert req["target_past_discards"] == [("7p", False, False)]
    assert req["opponent_rivers"][2] == [("2z", False, False), ("9s", True, False)]
    ctx = resolve_simulation_context(validated)
    assert ctx["scores"] == [21100, 31200, 30800, 16900]
    assert _table_snapshot({"config": validated, "resolved_context": ctx})["target_wind"] == 1


@pytest.mark.parametrize("round_id", [f"{w}{n}" for w in "ESW" for n in range(1, 5)])
@pytest.mark.parametrize("wind", range(4))
@pytest.mark.parametrize("x", [1, 2, 4])
def test_all_rounds_seats_and_turns_rotate_exactly_once(round_id, wind, x):
    command, winds = draw_command(round_id, wind, x)
    req = RunRequest(**parse(command)).model_dump(mode="json", by_alias=True)
    validate_draw_rivers(req)
    oya, target, actual_wind = seat_coordinates(req)
    assert oya == int(round_id[1]) - 1 and actual_wind == wind
    ctx = resolve_simulation_context(req)
    for w in range(4):
        pid = (oya + w) % 4
        river = req["target_past_discards"] if pid == target else req["opponent_rivers"][pid]
        assert [e[0] for e in river or []] == winds[w]
        assert ctx["scores"][pid] == [30800, 16900, 21100, 31200][w]
    assert _table_snapshot({"config": req, "resolved_context": ctx})["target_wind"] == wind


@pytest.mark.parametrize("river", [
    "2z/7p/2z/9m",             # missing dealer turn 2
    "2z,9st/7p/2z,6z/9m",     # West's future turn 2
    "2z,9st/7p,6z/2z/9m",     # target already discarded turn 2
])
def test_explicit_full_river_is_not_padded_or_truncated(river):
    req, error = parse_sim_command(REPORTED.replace("2z,9st/7p/2z/9m", river))
    assert req is None and "时序" in error
    assert "东家为庄家" in error


@pytest.mark.parametrize("mutation", ["missing", "future", "unrotated", "duplicate", "shape"])
def test_service_rejects_bad_draw_prefix_before_native_import(monkeypatch, mutation):
    from mortal_app import service
    req = deepcopy(parse(REPORTED))
    if mutation == "missing":
        req["opponent_rivers"][2].pop()
    elif mutation == "future":
        req["opponent_rivers"][0].append(("6z", False, False))
    elif mutation == "unrotated":
        req["target_seat"] = 1
        req["opponent_rivers"] = [[("2z", False, False), ("9s", True, False)], [],
                                  [("2z", False, False)], [("9m", False, False)]]
    elif mutation == "duplicate":
        req["opponent_rivers"][3] = [("7p", False, False)]
    else:
        req["opponent_rivers"] = [[], []]
    def forbidden(*args):
        pytest.fail("Invalid river reached native import")
    monkeypatch.setattr(service, "_prepare_imports", forbidden)
    with pytest.raises(ValueError, match="牌河|时序"):
        service._parse_inputs(req)
