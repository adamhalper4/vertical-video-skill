"""
multicam — renders the cameras and cuts them together.

The one-take / N-camera / one-edit model:
  • render_master(base_avatar_id, script, voice_id)   -> master.mp4 (text->TTS, Avatar V)
  • extract_audio(master.mp4)                          -> master.wav (the spine)
  • upload_audio_asset(master.wav)                     -> audio_asset_id
  • render_camera(avatar_id, audio_asset_id)           -> angle_<slug>.mp4 (voice.type:audio, Avatar V)
        every camera lip-syncs the SAME audio => frame-aligned to the master
  • assemble(cut_sheet, camera_paths, master.wav)      -> final.mp4 (hard cuts + ONE audio track)

All renders go through HeyGen /v2/video/generate; the avatar_id is a photo_avatar look in a
twin group, so it renders on Avatar V (JIT base motion via IV first for photo-only looks).
ENV: HEYGEN_API_KEY.
"""
from __future__ import annotations
import os, time, subprocess, tempfile
from pathlib import Path
import httpx

API_BASE = "https://api.heygen.com"
UPLOAD_BASE = "https://upload.heygen.com"


def _key(api_key: str | None) -> str:
    k = api_key or os.environ.get("HEYGEN_API_KEY")
    if not k:
        raise RuntimeError("HEYGEN_API_KEY not set")
    return k


# ---------------------------------------------------------------------------
# Render primitives
# ---------------------------------------------------------------------------
def _voice_block_text(voice_id: str, text: str) -> dict:
    return {"type": "text", "voice_id": voice_id, "input_text": text}


def _voice_block_audio(audio_asset_id: str) -> dict:
    # voice.type "audio" drives lip-sync from a supplied audio asset (no TTS).
    return {"type": "audio", "audio_asset_id": audio_asset_id}


def _generate(avatar_id: str, voice_block: dict, *, api_key: str | None,
              title: str, dimension: tuple[int, int]) -> str:
    """POST /v2/video/generate. avatar_id = photo_avatar look in a twin group => Avatar V."""
    w, h = dimension
    payload = {
        "title": title,
        "video_inputs": [{
            "character": {"type": "avatar", "avatar_id": avatar_id},
            "voice": voice_block,
        }],
        "dimension": {"width": w, "height": h},
    }
    with httpx.Client(timeout=120.0) as c:
        r = c.post(f"{API_BASE}/v2/video/generate",
                   headers={"X-Api-Key": _key(api_key), "Content-Type": "application/json"},
                   json=payload)
        r.raise_for_status()
        data = r.json()
    vid = (data.get("data") or {}).get("video_id") or data.get("video_id")
    if not vid:
        raise RuntimeError(f"no video_id in generate response: {data}")
    return vid


def poll_video(video_id: str, *, api_key: str | None = None, timeout_s: int = 900,
               poll_s: int = 10) -> dict:
    """Poll /v1/video_status.get until completed/failed. Returns the status payload (has video_url)."""
    deadline = time.time() + timeout_s
    with httpx.Client(timeout=60.0) as c:
        while time.time() < deadline:
            r = c.get(f"{API_BASE}/v1/video_status.get",
                      headers={"X-Api-Key": _key(api_key)}, params={"video_id": video_id})
            r.raise_for_status()
            d = (r.json().get("data") or {})
            st = d.get("status")
            if st == "completed":
                return d
            if st in ("failed", "error"):
                raise RuntimeError(f"video {video_id} {st}: {d.get('error')}")
            time.sleep(poll_s)
    raise TimeoutError(f"video {video_id} not complete in {timeout_s}s")


def _download(url: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(httpx.get(url, timeout=300).content)
    return out_path


def render_master(base_avatar_id: str, script: str, voice_id: str, out_path: Path, *,
                  api_key: str | None = None, dimension: tuple[int, int] = (720, 1280)) -> dict:
    """Render the full script on the base angle (text->TTS) and download it."""
    vid = _generate(base_avatar_id, _voice_block_text(voice_id, script),
                    api_key=api_key, title="multiangle-master", dimension=dimension)
    status = poll_video(vid, api_key=api_key)
    _download(status["video_url"], out_path)
    return {"video_id": vid, "path": str(out_path), "status": status}


def render_camera(avatar_id: str, audio_asset_id: str, out_path: Path, *,
                  api_key: str | None = None, dimension: tuple[int, int] = (720, 1280),
                  slug: str = "cam") -> dict:
    """Render the full script on one angle, driven by the master audio (voice.type:audio)."""
    vid = _generate(avatar_id, _voice_block_audio(audio_asset_id),
                    api_key=api_key, title=f"multiangle-{slug}", dimension=dimension)
    status = poll_video(vid, api_key=api_key)
    _download(status["video_url"], out_path)
    return {"video_id": vid, "path": str(out_path), "status": status, "slug": slug}


# ---------------------------------------------------------------------------
# Audio asset
# ---------------------------------------------------------------------------
def extract_audio(video_path: str | Path, out_wav: str | Path) -> Path:
    out_wav = Path(out_wav)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(video_path), "-vn", "-acodec", "pcm_s16le", "-ar", "44100",
                    str(out_wav)], check=True)
    return out_wav


def upload_audio_asset(audio_path: str | Path, *, api_key: str | None = None,
                       content_type: str = "audio/wav") -> str:
    """Upload audio to HeyGen and return its asset id (for voice.type:audio renders)."""
    data = Path(audio_path).read_bytes()
    with httpx.Client(timeout=180.0) as c:
        r = c.post(f"{UPLOAD_BASE}/v1/asset", headers={
            "X-Api-Key": _key(api_key), "Content-Type": content_type}, content=data)
        r.raise_for_status()
        d = (r.json().get("data") or r.json())
    aid = d.get("id") or d.get("asset_id")
    if not aid:
        raise RuntimeError(f"no asset id in upload response: {d}")
    return aid


# ---------------------------------------------------------------------------
# Assembly (ffmpeg): hard cuts per the cut sheet + ONE master audio track
# ---------------------------------------------------------------------------
def _video_duration(p: str | Path) -> float:
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(p)]).decode().strip())


# Angles that are DERIVED from another rendered camera rather than rendered separately.
# close_up is a digital zoom-in on the home (straight_on) look — same pixels as home, so identity
# / wardrobe / lighting are guaranteed to match, and there's no separate avatar to drift.
# zoom = scale factor; y_bias in [0,1] = where the crop window sits vertically (0=top) — biased
# up so the eyes stay in the upper third when we punch in.
ANGLE_DERIVATIONS: dict[str, dict] = {
    "close_up": {"source": "straight_on", "zoom": 1.45, "y_bias": 0.16},
}


def _resolve_source(slug: str, camera_paths: dict) -> tuple[str, float, float]:
    """Return (source_path, zoom, y_bias) for a cut angle, resolving derived angles."""
    if slug in camera_paths:
        return str(camera_paths[slug]), 1.0, 0.5
    d = ANGLE_DERIVATIONS.get(slug)
    if d and d["source"] in camera_paths:
        return str(camera_paths[d["source"]]), d["zoom"], d["y_bias"]
    raise KeyError(f"cut sheet references angle '{slug}' with no rendered or derivable camera")


def assemble(cut_sheet: list[dict], camera_paths: dict[str, str | Path],
             master_audio: str | Path, out_path: str | Path, *,
             fps: int = 30, dimension: tuple[int, int] = (720, 1280)) -> Path:
    """Hard-cut between cameras per the cut sheet, lay the single master audio over everything.

    For each cut, slice [t_start, t_end] from the live camera (or, for a DERIVED angle like
    close_up, from its source camera with a digital zoom), re-encode to uniform codec/fps/size,
    concat the slices, then mux master_audio.
    """
    out_path = Path(out_path); out_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = dimension
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        seg_files: list[Path] = []
        for cut in cut_sheet:
            src, zoom, y_bias = _resolve_source(cut["angle"], camera_paths)
            t0, t1 = float(cut["t_start"]), float(cut["t_end"])
            dur = max(0.05, t1 - t0)
            seg = td / f"seg_{cut['idx']:03d}.mp4"
            if zoom and zoom != 1.0:
                # punch in: scale up then crop back to frame, biased vertically by y_bias
                sw, sh = int(round(w * zoom)), int(round(h * zoom))
                cx = (sw - w) // 2
                cy = int(round((sh - h) * y_bias))
                vf = f"scale={sw}:{sh},crop={w}:{h}:{cx}:{cy},fps={fps}"
            else:
                vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                      f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps}")
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", src,
                "-an", "-vf", vf,
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", str(seg),
            ], check=True)
            seg_files.append(seg)

        concat_list = td / "concat.txt"
        concat_list.write_text("".join(f"file '{s}'\n" for s in seg_files))
        video_only = td / "video.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy", str(video_only),
        ], check=True)

        # mux the single master audio; -shortest trims to the (cut) video length
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video_only), "-i", str(master_audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(out_path),
        ], check=True)
    return out_path


__all__ = [
    "render_master", "render_camera", "poll_video",
    "extract_audio", "upload_audio_asset", "assemble",
    "_generate", "_voice_block_text", "_voice_block_audio",
]
