"""
angle_set — generate-or-reuse the camera-angle look set for ONE avatar look.

An "angle set" is the ~6 Image-N angle frames of a single look (straight_on, close_up,
left_45, right_45, full_body, side_view), each registered as a renderable photo_avatar
look in the creator's twin group so Avatar V can render it. We generate it ONCE per look
and bank it — angling is the slow/expensive step, so every later multi-angle video on the
same look reuses it.

Division of labor (honest about which calls are deterministic vs agent/MCP-driven):
  • Deterministic, in this module: Seedance angle gen + ArcFace/Gemini gating (via
    av-image-n-looks) and the on-disk index.
  • Agent/MCP-driven, recorded back here: turning a passing frame into a photo_avatar look
    in the group (HeyGen photo-avatar create + add-to-group), and — for a photo-only/virtual
    look with NO video upload — attaching a video base so Avatar V is available
    (IV JIT base motion / cross_ref twin clip; [[reference_avatar_v_cross_ref_upload_pipeline]]).
    The orchestrator registers the look via MCP, then calls record_angle_look(...) to persist
    the returned avatar_id.

Index location: <skill_dir>/angle_sets.json  (keyed by look_id).
"""
from __future__ import annotations
import os, json, sys
from pathlib import Path

# av-image-n-looks lives as a sibling skill; import its angles pipeline.
_IMAGEN = Path.home() / ".claude" / "skills" / "av-image-n-looks"
if _IMAGEN.exists() and str(_IMAGEN) not in sys.path:
    sys.path.insert(0, str(_IMAGEN))

SKILL_DIR = Path(__file__).resolve().parent.parent
INDEX_PATH = SKILL_DIR / "angle_sets.json"

# Which angles we BANK (generate all — same Seedance cost) vs the cap we CUT into a video.
DEFAULT_ANGLE_SLUGS = ["straight_on", "close_up", "left_45", "right_45", "full_body", "side_view"]
ARCFACE_MIN = 0.55  # matches av-image-n-looks identity gate


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------
def load_index() -> dict:
    if INDEX_PATH.exists():
        return json.loads(INDEX_PATH.read_text())
    return {}


def _save_index(idx: dict) -> None:
    INDEX_PATH.write_text(json.dumps(idx, indent=2))


def get_angle_set(look_id: str) -> dict | None:
    """Return the persisted angle set for a look, or None. Shape:
    {"look_id", "group_id", "voice_id", "angles": {slug: {"avatar_id","frame_path","arcface"}}}
    Only entries that have a registered avatar_id are render-ready.
    """
    return load_index().get(look_id)


def renderable_angles(look_id: str) -> list[str]:
    """Slugs in this look's angle set that have a registered avatar_id (can render on Avatar V)."""
    s = get_angle_set(look_id) or {}
    return [slug for slug, a in s.get("angles", {}).items() if a.get("avatar_id")]


def record_angle_look(look_id: str, slug: str, avatar_id: str, *,
                      frame_path: str | None = None, arcface: float | None = None,
                      group_id: str | None = None, voice_id: str | None = None) -> None:
    """Persist that `slug` for `look_id` is now a renderable photo_avatar look (avatar_id).

    Called by the orchestrator AFTER it registers the frame as a photo avatar (MCP) + adds it
    to the group. This is what makes the angle reusable across future videos.
    """
    idx = load_index()
    entry = idx.setdefault(look_id, {"look_id": look_id, "angles": {}})
    if group_id:
        entry["group_id"] = group_id
    if voice_id:
        entry["voice_id"] = voice_id
    entry["angles"][slug] = {
        "avatar_id": avatar_id,
        "frame_path": frame_path or entry["angles"].get(slug, {}).get("frame_path"),
        "arcface": arcface if arcface is not None else entry["angles"].get(slug, {}).get("arcface"),
    }
    _save_index(idx)


# ---------------------------------------------------------------------------
# Generation (deterministic; av-image-n-looks angles mode)
# ---------------------------------------------------------------------------
def generate_angle_frames(look_id: str, look_image_url: str, *,
                          reference_video_url: str | None, reference_image: str | Path,
                          out_dir: str | Path, aspect_ratio: str = "9:16") -> dict:
    """Generate the angle frames for a look via av-image-n-looks angles mode + gate them.

    Returns {"frames": {slug: path}, "arcface": {slug: score}, "passed": [slugs], "mean_arcface"}.
    `reference_video_url` None => photo-only/virtual look (still generates; needs a video base
    attached before Avatar V can render — see ensure_video_base()).
    Frames are written under out_dir; pass them to the orchestrator to register as photo avatars.
    """
    from common.image_n import generate_look_pack  # av-image-n-looks

    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    pack = generate_look_pack(
        out_dir, scenes=None, mode="angles",
        reference_video_url=reference_video_url, look_image_url=look_image_url,
        reference_image=reference_image, aspect_ratio=aspect_ratio,
        username=f"multiangle_{look_id}",
    )
    arc = pack.get("arcface", {})
    frames = {Path(f).stem: f for f in pack.get("frames", [])}
    passed = [slug for slug, sc in arc.items() if sc is not None and sc >= ARCFACE_MIN]
    # close_up can score None (face fills frame past the detector) but identity is intact — keep it.
    for slug, f in frames.items():
        if slug == "close_up" and slug not in passed and arc.get(slug) is None:
            passed.append(slug)
    # stage the frames into the index as not-yet-renderable (no avatar_id until registered)
    idx = load_index()
    entry = idx.setdefault(look_id, {"look_id": look_id, "angles": {}})
    for slug in frames:
        entry["angles"].setdefault(slug, {})
        entry["angles"][slug].update({"frame_path": frames[slug], "arcface": arc.get(slug)})
    _save_index(idx)
    return {"frames": frames, "arcface": arc, "passed": passed,
            "mean_arcface": pack.get("mean_arcface")}


def ensure_video_base_needed(reference_video_url: str | None) -> bool:
    """True when the look is photo-only/virtual (no uploaded video) and therefore needs a
    video base (IV JIT base motion / cross_ref twin clip) before Avatar V can render it.
    A digital-twin look (group already has a video) returns False — Avatar V renders directly.
    """
    return reference_video_url is None


__all__ = [
    "INDEX_PATH", "DEFAULT_ANGLE_SLUGS", "ARCFACE_MIN",
    "load_index", "get_angle_set", "renderable_angles", "record_angle_look",
    "generate_angle_frames", "ensure_video_base_needed",
]
