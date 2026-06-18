#!/usr/bin/env python3
"""
probe_audio_input — confirm the load-bearing capability before any full multi-cam run:
can we render an avatar driven by a SUPPLIED audio asset (voice.type:"audio") on Avatar V?

Engine is always Avatar V (a photo_avatar look in a twin group renders on Avatar V; photo-only
looks get IV JIT base motion first). What we DON'T yet know in a given environment is whether the
audio-input render path works — the whole "one continuous voice, then cut" model depends on every
camera lip-syncing the SAME audio.

Steps:
  1. render a ~1s text->TTS clip on the avatar (confirms basic render + gives us real audio)
  2. extract its audio, upload as an asset
  3. render the SAME avatar driven by that audio asset (voice.type:"audio")
  4. report success/failure + the raw status (inspect for the actual rendered model)
  -> writes <skill_dir>/.engine.json

Usage:
  HEYGEN_API_KEY=... python scripts/probe_audio_input.py --avatar-id <id> --voice <voice_id>
"""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from common import multicam  # noqa: E402

ENGINE_PATH = SKILL_DIR / ".engine.json"
PROBE_LINE = "This is a short multi camera audio input test."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--avatar-id", required=True, help="a photo_avatar look (digital twin) to test")
    ap.add_argument("--voice", required=True, help="the look's cloned voice_id")
    ap.add_argument("--out", default=str(SKILL_DIR / "outputs" / "_probe"))
    args = ap.parse_args()

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    result = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "avatar_id": args.avatar_id, "surface": "rest_v2_generate",
              "engine": "avatar_v"}

    try:
        print("[probe] 1/3 text->TTS render…")
        m = multicam.render_master(args.avatar_id, PROBE_LINE, args.voice,
                                   out / "probe_master.mp4")
        result["text_render_ok"] = True
        result["master_status"] = {k: m["status"].get(k) for k in ("status", "video_url", "duration")}

        print("[probe] 2/3 extract + upload audio…")
        wav = multicam.extract_audio(out / "probe_master.mp4", out / "probe.wav")
        asset = multicam.upload_audio_asset(wav)
        result["audio_asset_id"] = asset

        print("[probe] 3/3 audio-input render (voice.type:audio)…")
        cam = multicam.render_camera(args.avatar_id, asset, out / "probe_audio.mp4", slug="probe")
        result["audio_input_ok"] = True
        result["audio_status"] = {k: cam["status"].get(k) for k in ("status", "video_url", "duration")}
        # full status kept for manual rendered-model inspection (truth = rendered model)
        result["audio_status_full"] = cam["status"]
        print("[probe] PASS — audio-input renders on Avatar V via REST.")
    except Exception as e:
        result.setdefault("audio_input_ok", False)
        result["error"] = repr(e)
        print(f"[probe] audio-input via REST did NOT work: {e!r}")
        print("[probe] -> drive renders through the HeyGen MCP / Shots audio-input surface "
              "instead; record the working surface in .engine.json. Engine stays Avatar V.")

    ENGINE_PATH.write_text(json.dumps(result, indent=2))
    print(f"[probe] wrote {ENGINE_PATH}")
    return 0 if result.get("audio_input_ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
