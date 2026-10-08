"""Run local CUDA cases with fresh history and render the actual bot reports."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "bot/src"), str(ROOT / "target/release")]

CASES = {
    "reported-e3-south": "/sim 255788m44p33s1155z d4m c=7m,7mr E3-1 seat=南 x=2 河=2z,9st/7p/2z/9m P308,169,211,312 500",
    # Existing user reports preserved in tests/test_response_context.py.
    "reported-pon-west-x3": "/sim 112m13558p2236s4z d5p seat=北 x=3 E3-0 river=东:9s,1z,8pt/南:3z,9p,5z/西:1p,3z,5p/北:9m,2z P277,208,264,251 c=pon:5p>6s,pon:5p>8p,pon:5p>1m,pass 50",
    "reported-pon-east-x4": "/sim 112m13558p2236s4z d5p seat=北 x=4 E3-0 river=东:9s,1z,8pt,5p/南:3z,9p,5z/西:1p,3z,2p/北:9m,2z,7z P277,208,264,251 c=pon:5p>6s,pon:5p>8p,pon:5p>1m,pass 50",
    "e2-north-discard": "/sim 255788m44p33s1155z d4m c=7m,7mr E2-1 seat=北 x=2 河=2z,9st/7p,3z/2z,6z/9m P308,169,211,312 100",
    "s4-east-discard": "/sim 255788m44p33s1155z d4m c=7m,7mr S4-1 seat=东 x=2 河=2z/7p/2z/9m P308,169,211,312 100",
    "s4-north-pon": "/sim 123456m55p789s11z d9p c=pon:5p>1z,pass S4-0 seat=北 x=3 河=2z,3z,4z/6z,7z,9m/1p,9p,5p/1s,2s P308,169,211,312 100",
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--case", choices=CASES, action="append")
    ap.add_argument("--runs", type=int)
    ap.add_argument("--font", type=Path,
                    default=Path(os.environ.get("WINDIR", ".")) / "Fonts/msyh.ttc")
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    from parser import parse_sim_command
    from bot import Bot
    from render_png import render_png, candidate_label
    from apps.api.models import RunRequest
    from mortal_app import history_store, service
    import libriichi
    import torch
    from PIL import Image, ImageDraw
    from unittest.mock import patch

    manifest = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "native": str(libriichi.__file__),
        "native_sha256": hashlib.sha256(Path(libriichi.__file__).read_bytes()).hexdigest(),
        "torch": torch.__version__,
        "gpu": torch.cuda.get_device_name(0),
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in [
                "mortal_app/service.py", "mortal_app/call_context.py", "bot/src/parser.py",
                "bot/src/bot.py", "bot/src/model_eval.py", "bot/src/render_png.py",
                "libriichi/src/arena/prefix.rs", "libriichi/src/arena/custom_kyoku.rs",
                "tools/run_pr42_seat_cases.py",
            ]
        },
        "cases": [],
    }
    worker = object.__new__(Bot)
    worker.mortal_cfg = {"model_id": "distill_41b_infer"}
    for name in args.case or CASES:
        command = CASES[name]
        req, error = parse_sim_command(command)
        if error:
            raise ValueError(error)
        req.update(model_id="distill_41b_infer", batch_size=1000, rayon_threads=8,
                   engine="python", decision_contract="legacy_amp_v1", seed=42)
        if args.runs is not None:
            req["runs"] = args.runs
        # Exercise the same schema serialization as the HTTP worker boundary.
        req = RunRequest(**req).model_dump(mode="json", by_alias=True)
        # Fresh per-case database; never mix with the user's running service.
        db = out / f"{name}.history.db"
        if db.exists():
            raise FileExistsError(f"Use a fresh output directory: {db}")
        history_store._GLOBAL_HISTORY_STORE = history_store.HistoryStore(db)
        os.environ["MORTALSIM_DATA_DIR"] = str(out / "runtime")
        req["replay_of"] = "00000000-0000-0000-0000-000000000042"
        def emit(event):
            if event.get("type") in ("status", "batch_completed"):
                print(name, json.dumps(event, ensure_ascii=False), flush=True)
        result = service.run_analysis(req, emit)
        qp = worker._eval_model_qp(req)
        if not qp:
            raise AssertionError("Actual model Q/P inference is missing")
        for candidate in result["candidates"]:
            sample = candidate["sample"]
            assert sample["completed_games"] == req["runs"] and sample["errors"] == 0, sample
        result_path = out / f"{name}.json"
        result_path.write_text(json.dumps({"command": command, "result": result, "model_qp": qp},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
        best = max(result["candidates"], key=lambda c: c["hanchan"]["dan_pt_ev"]["houou_7"]["value"])
        image = out / f"{name}.png"
        texts = []
        original_text = ImageDraw.ImageDraw.text
        def capture_text(draw, xy, text, *pos, **kw):
            if draw._image.size == (1120, 750):
                texts.append({"text": str(text), "bbox": list(draw.textbbox(xy, text, font=kw.get("font")))})
            return original_text(draw, xy, text, *pos, **kw)
        with patch.object(ImageDraw.ImageDraw, "text", capture_text):
            render_png(result, asset_dir=ROOT / "bot/assets", font_path=args.font,
                       output_path=image, recommended_tile=candidate_label(best, result["candidates"]),
                       theme="emerald", model_qp=qp)
        with Image.open(image) as im:
            im.verify()
        outside = [t for t in texts if t["bbox"][0] < 0 or t["bbox"][1] < 0
                   or t["bbox"][2] > 1120 or t["bbox"][3] > 750]
        (out / f"{name}.render.json").write_text(
            json.dumps({"outside_canvas": outside, "text": texts}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        assert not outside, outside
        record = {"name": name, "command": command, "request": req,
                  "provenance": ("user-reported" if name == "reported-e3-south" else
                                 "existing-user-report" if name.startswith("reported-pon-") else
                                 "constructed-regression"),
                  "resolved_context": result["resolved_context"], "model": result["model"],
                  "image": str(image), "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                  "result": str(result_path),
                  "samples": [c["sample"] for c in result["candidates"]]}
        manifest["cases"].append(record)
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print("COMPLETED", name, record["samples"], flush=True)


if __name__ == "__main__":
    main()
