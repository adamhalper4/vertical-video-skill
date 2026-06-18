"""
orchestrate — av-multi-angle-video end-to-end driver.

Stages (see SKILL.md):
  A  angle set (generate-or-reuse, keyed by look_id)
  C  master render (base angle, text->TTS) + extract/upload master audio
  B  cut plan (Whisper timing on master audio + LLM/ruleset segmentation)
  D  camera renders (other angles, voice.type:audio = master audio)
  E  assemble (ffmpeg hard cuts + one master audio track)
  F  ship (save artifacts; persist a new angle set)

Registration boundary: turning an angle FRAME into a renderable photo_avatar look (and, for a
photo-only look, attaching a video base so Avatar V renders) is done by the agent via the HeyGen
MCP. When the look's angle set isn't fully registered yet, run() CHECKPOINTS (exit 3) with exactly
which frames to register; the agent registers them (recording each via angle_set.record_angle_look)
and re-runs. Once an angle set exists, the whole pipeline runs unattended (the common case).

Run:
  uv run --python 3.11 --with insightface --with onnxruntime --with numpy --with pillow \
    --with google-genai --with httpx --with openai-whisper -- \
    python -m common.orchestrate --look-id <id> --script script.txt --voice <voice_id> \
      --group-id <gid> --look-image-url <url> --reference-video-url <url> \
      --reference-image ref.jpg --out outputs/run1
"""
from __future__ import annotations
import argparse, json, sys
from dataclasses import dataclass, field
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from common import angle_set as A          # noqa: E402
from common import cut_planner             # noqa: E402
from common import multicam                # noqa: E402
from common.angle_grammar import select_three_angles, BASE_ANGLE  # noqa: E402


@dataclass
class Config:
    look_id: str
    script: str
    voice_id: str
    out_dir: Path
    group_id: str | None = None
    look_image_url: str | None = None
    reference_video_url: str | None = None   # None => photo-only/virtual look (needs video base)
    reference_image: str | None = None
    dimension: tuple[int, int] = (720, 1280)
    aspect_ratio: str = "9:16"
    whisper_model: str = "base"
    gemini_api_key: str | None = None
    artifacts: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stage A — angle set
# ---------------------------------------------------------------------------
def stage_a_angle_set(cfg: Config) -> dict:
    """Reuse the look's angle set if present; otherwise generate frames and CHECKPOINT for
    registration. Returns {"ready": bool, "renderable": [slugs], "need_registration": [...]}.
    """
    ready = A.renderable_angles(cfg.look_id)
    if len(ready) >= 3:
        print(f"[A] reusing angle set for {cfg.look_id}: {ready}")
        return {"ready": True, "renderable": ready, "need_registration": []}

    print(f"[A] no (complete) angle set for {cfg.look_id} — generating angle frames…")
    if not cfg.look_image_url or not cfg.reference_image:
        raise RuntimeError("first-time angle generation needs --look-image-url and --reference-image")
    gen = A.generate_angle_frames(
        cfg.look_id, cfg.look_image_url,
        reference_video_url=cfg.reference_video_url, reference_image=cfg.reference_image,
        out_dir=cfg.out_dir / "angles", aspect_ratio=cfg.aspect_ratio,
    )
    need = [{"slug": s, "frame_path": gen["frames"][s], "arcface": gen["arcface"].get(s)}
            for s in gen["passed"] if not A.get_angle_set(cfg.look_id)["angles"].get(s, {}).get("avatar_id")]
    base_needed = A.ensure_video_base_needed(cfg.reference_video_url)
    print(f"[A] CHECKPOINT — register these {len(need)} frames as photo_avatar looks in group "
          f"{cfg.group_id} via HeyGen MCP, then call angle_set.record_angle_look(...) for each:")
    for n in need:
        print(f"      {n['slug']:12s} arc={n['arcface']}  {n['frame_path']}")
    if base_needed:
        print("[A] NOTE: photo-only/virtual look — attach a video base (IV JIT base motion / "
              "cross_ref twin clip) so Avatar V renders. See reference_avatar_v_cross_ref_upload_pipeline.")
    return {"ready": False, "renderable": ready, "need_registration": need,
            "video_base_needed": base_needed, "mean_arcface": gen["mean_arcface"]}


# ---------------------------------------------------------------------------
# Stage C — master render + audio
# ---------------------------------------------------------------------------
def stage_c_master(cfg: Config, base_avatar_id: str) -> dict:
    print(f"[C] master render on base angle ({BASE_ANGLE}) text->TTS…")
    m = multicam.render_master(base_avatar_id, cfg.script, cfg.voice_id,
                               cfg.out_dir / "master.mp4", dimension=cfg.dimension)
    wav = multicam.extract_audio(cfg.out_dir / "master.mp4", cfg.out_dir / "master.wav")
    asset = multicam.upload_audio_asset(wav)
    print(f"[C] master audio asset = {asset}")
    return {"master_path": m["path"], "master_audio": str(wav), "audio_asset_id": asset,
            "video_id": m["video_id"]}


# ---------------------------------------------------------------------------
# Stage B — cut plan
# ---------------------------------------------------------------------------
def stage_b_cut_plan(cfg: Config, available_angles: list[str], master_audio: str) -> list[dict]:
    print(f"[B] cut plan over angles {available_angles}…")
    sheet = cut_planner.build_cut_sheet(
        cfg.script, master_audio, available_angles,
        gemini_api_key=cfg.gemini_api_key, whisper_model=cfg.whisper_model,
        out_path=cfg.out_dir / "cut_sheet.json",
    )
    used = sorted({c["angle"] for c in sheet})
    print(f"[B] {len(sheet)} cuts across {used}")
    return sheet


# ---------------------------------------------------------------------------
# Stage D — camera renders (only the non-base angles actually used)
# ---------------------------------------------------------------------------
def stage_d_cameras(cfg: Config, cut_sheet: list[dict], audio_asset_id: str,
                    master_path: str) -> dict[str, str]:
    aset = A.get_angle_set(cfg.look_id)["angles"]
    used = sorted({c["angle"] for c in cut_sheet})
    camera_paths: dict[str, str] = {}
    for slug in used:
        if slug == BASE_ANGLE:
            camera_paths[slug] = master_path  # base camera = the master clip itself
            continue
        if slug in multicam.ANGLE_DERIVATIONS:
            continue  # derived in assembly (e.g. close_up = digital zoom on straight_on) — no render
        avatar_id = aset[slug]["avatar_id"]
        print(f"[D] camera {slug} (audio-input)…")
        cam = multicam.render_camera(avatar_id, audio_asset_id,
                                     cfg.out_dir / f"angle_{slug}.mp4",
                                     dimension=cfg.dimension, slug=slug)
        camera_paths[slug] = cam["path"]
    return camera_paths


# ---------------------------------------------------------------------------
# Stage E — assemble
# ---------------------------------------------------------------------------
def stage_e_assemble(cfg: Config, cut_sheet: list[dict], camera_paths: dict[str, str],
                     master_audio: str) -> Path:
    print("[E] ffmpeg multi-cam assembly…")
    final = multicam.assemble(cut_sheet, camera_paths, master_audio,
                              cfg.out_dir / "final.mp4", dimension=cfg.dimension)
    print(f"[E] -> {final}")
    return final


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def run(cfg: Config) -> dict:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    a = stage_a_angle_set(cfg)
    if not a["ready"]:
        (cfg.out_dir / "registration_pending.json").write_text(json.dumps(a, indent=2))
        print("[run] stopping at registration checkpoint (exit 3). Register, then re-run.")
        raise SystemExit(3)

    available = select_three_angles(a["renderable"])
    base_avatar = A.get_angle_set(cfg.look_id)["angles"][BASE_ANGLE]["avatar_id"]

    c = stage_c_master(cfg, base_avatar)
    sheet = stage_b_cut_plan(cfg, available, c["master_audio"])
    cams = stage_d_cameras(cfg, sheet, c["audio_asset_id"], c["master_path"])
    final = stage_e_assemble(cfg, sheet, cams, c["master_audio"])

    cfg.artifacts = {"final": str(final), "cut_sheet": str(cfg.out_dir / "cut_sheet.json"),
                     "master": c["master_path"], "cameras": cams, "angles_used": available}
    (cfg.out_dir / "artifacts.json").write_text(json.dumps(cfg.artifacts, indent=2))
    print(f"[F] done -> {final}")
    return cfg.artifacts


def _parse_dim(s: str) -> tuple[int, int]:
    w, h = s.lower().split("x")
    return int(w), int(h)


def main() -> int:
    ap = argparse.ArgumentParser(description="av-multi-angle-video end-to-end")
    ap.add_argument("--look-id", required=True)
    ap.add_argument("--script", required=True, help="path to the script text file")
    ap.add_argument("--voice", required=True, dest="voice_id")
    ap.add_argument("--out", required=True, dest="out_dir")
    ap.add_argument("--group-id", default=None)
    ap.add_argument("--look-image-url", default=None)
    ap.add_argument("--reference-video-url", default=None)
    ap.add_argument("--reference-image", default=None)
    ap.add_argument("--dimension", default="720x1280", type=_parse_dim)
    ap.add_argument("--aspect-ratio", default="9:16")
    ap.add_argument("--whisper-model", default="base")
    args = ap.parse_args()

    script = Path(args.script).read_text().strip() if Path(args.script).exists() else args.script
    cfg = Config(
        look_id=args.look_id, script=script, voice_id=args.voice_id,
        out_dir=Path(args.out_dir), group_id=args.group_id,
        look_image_url=args.look_image_url, reference_video_url=args.reference_video_url,
        reference_image=args.reference_image, dimension=args.dimension,
        aspect_ratio=args.aspect_ratio, whisper_model=args.whisper_model,
    )
    run(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
