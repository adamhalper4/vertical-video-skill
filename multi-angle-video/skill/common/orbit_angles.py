"""
orbit_angles — background-consistent side-angle generation (the canonical ¾ method).

Why this exists (Adam, 2026-06-09): a real multi-camera rig films ONE physical scene from
several positions. When you cut to the 45° camera, the SAME background must be there, just seen
from the side with correct parallax (window goes oblique, skyline slides, furniture repositions).
Generating each ¾ look as an INDEPENDENT still (the old way) invents a new, similar-but-different
room — so the cut reads as two separate shots, not two cameras in one scene.

The fix: generate the side angle as a CAMERA ORBIT off the HOME frame.
  1. Seedance, home shot as the FIRST frame, prompt the camera to arc to the subject's side while
     LOCKING the background → one continuous shot of one scene → background shifts with true parallax.
  2. The orbit sweeps non-linearly 0°→~90° (it overshoots a fixed "stop at 45"), so we FRAME-PICK:
     extract dense frames, ask Gemini the head/body turn in degrees, keep the ~45° face-readable
     frame. Identity is inherently preserved (it's the same person from the home frame), so no
     ArcFace gate is needed here.

Result: a clean 45° three-quarter whose background is provably the home room from a rotated camera.

ENV: VOLC_*/ARK_API_KEY/SEEDANCE_AES_KEY (Seedance), GEMINI_API_KEY (frame pick). Run under uv py3.11.
"""
from __future__ import annotations
import sys, subprocess, json, re, glob, os
from pathlib import Path

_IMAGEN = Path.home() / ".claude" / "skills" / "av-image-n-looks"
if _IMAGEN.exists() and str(_IMAGEN) not in sys.path:
    sys.path.insert(0, str(_IMAGEN))

# Generic orbit prompt — the FIRST frame supplies the actual scene, so we only need to instruct the
# camera move + "keep the same background". Persona-specific scene description is NOT required.
def orbit_prompt(side: str) -> str:
    return (
        f"The camera arcs smoothly around to the person's {side.upper()}, sweeping from straight-on "
        "toward a side view, while the person stays seated and keeps talking. Keep the EXACT SAME room "
        "and background unchanged — the identical window and everything behind them — only the camera "
        "position moves, so the background slides across the frame with correct perspective and "
        "parallax. Do NOT invent or change the background; only move the camera to the side."
    )

# left orbit (camera to the subject's left) -> the "left_45" look; right orbit -> "right_45".
SIDE_TO_SLUG = {"left": "left_45", "right": "right_45"}
TARGET_DEG = 45
DEG_WINDOW = (38, 58)
ORBIT_DURATION_S = 7


def generate_orbit_clip(home_url: str, side: str, out_clip: str | Path,
                        duration_s: int = ORBIT_DURATION_S, username: str = "orbit") -> Path:
    """Seedance: home frame as first frame, camera orbits to `side`, background locked."""
    from common.image_n import generate_scene_video
    out_clip = Path(out_clip); out_clip.parent.mkdir(parents=True, exist_ok=True)
    generate_scene_video(None, [orbit_prompt(side)], out_clip,
                         look_image_url=home_url, look_image_role="first_frame",
                         aspect_ratio="9:16", duration_s=duration_s, username=username)
    return out_clip


def pick_45(clip: str | Path, work_dir: str | Path, *, gemini_api_key: str | None = None,
            t0: float = 1.0, t1: float = 4.6, step: float = 0.3) -> dict | None:
    """Extract dense frames across the orbit and Gemini-pick the one closest to a clean 45° three-quarter
    (face still readable). Returns {"frame", "deg"} or None."""
    from google import genai
    from PIL import Image
    work_dir = Path(work_dir); work_dir.mkdir(parents=True, exist_ok=True)
    key = gemini_api_key or os.environ["GEMINI_API_KEY"]
    client = genai.Client(api_key=key)
    n = int(round((t1 - t0) / step)) + 1
    times = [round(t0 + step * i, 2) for i in range(n)]
    for t in times:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(clip),
                        "-frames:v", "1", str(work_dir / f"s_{t}.jpg")], check=True)
    best = None
    for f in sorted(glob.glob(str(work_dir / "s_*.jpg"))):
        try:
            r = client.models.generate_content(model="gemini-2.5-pro", contents=[
                "Seated person. Estimate the horizontal head/body turn away from facing the camera. "
                'Reply JSON only: {"turn_deg":<0-90 integer>,"face_readable":true|false}. '
                "0=facing camera, 45=three-quarter, 90=full profile.", Image.open(f)])
            d = json.loads(re.search(r"\{.*\}", r.text, re.S).group(0))
        except Exception:
            d = {}
        deg = d.get("turn_deg", -1)
        if d.get("face_readable") and DEG_WINDOW[0] <= deg <= DEG_WINDOW[1]:
            if best is None or abs(deg - TARGET_DEG) < abs(best["deg"] - TARGET_DEG):
                best = {"frame": f, "deg": deg}
    return best


def generate_side_angle(home_url: str, side: str, out_dir: str | Path, *,
                        gemini_api_key: str | None = None) -> dict | None:
    """End-to-end: orbit clip off the home frame -> pick the ~45° frame. Returns
    {"slug","frame","deg","clip"} or None if no clean 45° frame was found (then re-roll/extend duration).
    The picked frame is the side-angle look; its background is the home room from a rotated camera."""
    out_dir = Path(out_dir); slug = SIDE_TO_SLUG[side]
    clip = generate_orbit_clip(home_url, side, out_dir / slug / "clip.mp4", username=f"orbit_{slug}")
    best = pick_45(clip, out_dir / slug, gemini_api_key=gemini_api_key)
    if not best:
        return None
    picked = out_dir / f"{slug}.jpg"
    subprocess.run(["cp", best["frame"], str(picked)], check=True)
    return {"slug": slug, "frame": str(picked), "deg": best["deg"], "clip": str(clip)}


__all__ = ["orbit_prompt", "generate_orbit_clip", "pick_45", "generate_side_angle",
           "SIDE_TO_SLUG", "TARGET_DEG"]
