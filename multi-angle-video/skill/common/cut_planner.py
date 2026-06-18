"""
cut_planner — turn a script + the master audio into a timed multi-cam cut sheet.

Flow:
  1. transcribe_words(master_audio)         -> word-level timestamps (Whisper)
  2. plan_cuts(script, angles)              -> [{text, angle, rationale}] via the LLM,
                                               governed by angle_grammar.planner_system_prompt
  3. resolve_timings(cuts, words, script)   -> attach [t_start, t_end] per cut, snapped to pauses
  4. enforce_constraints(cut_sheet)         -> repair min/max shot length + same-angle adjacency
  -> build_cut_sheet(...) runs all four and returns the final cut_sheet.

The cut sheet is the contract Stage E (assembly) consumes:
  [{ "idx", "text", "angle", "rationale", "t_start", "t_end" }, ...]  (contiguous, gap-free)
"""
from __future__ import annotations
import os, re, json, difflib
from pathlib import Path

from common.angle_grammar import (
    planner_system_prompt, select_angles, EDIT_CONSTRAINTS, BASE_ANGLE, HOME, EXCURSIONS,
    ANGLE_ROLES,
)


# ---------------------------------------------------------------------------
# 1. Whisper word timings
# ---------------------------------------------------------------------------
def transcribe_words(audio_path: str | Path, model: str = "base") -> list[dict]:
    """Return [{"word": str, "start": float, "end": float}] from the master audio.

    Uses openai-whisper with word_timestamps=True. `base` is fast + accurate enough for
    cut-boundary snapping; bump to `small`/`medium` for long or accented scripts.
    """
    import whisper  # openai-whisper
    m = whisper.load_model(model)
    res = m.transcribe(str(audio_path), word_timestamps=True)
    words: list[dict] = []
    for seg in res.get("segments", []):
        for w in seg.get("words", []):
            tok = w.get("word", "").strip()
            if tok:
                words.append({"word": tok, "start": float(w["start"]), "end": float(w["end"])})
    if not words:
        raise RuntimeError(f"Whisper returned no word timings for {audio_path}")
    return words


# ---------------------------------------------------------------------------
# 2. LLM cut planning (Gemini — matches the other skills' LLM calls + key)
# ---------------------------------------------------------------------------
def pace_for_duration(dur: float) -> str:
    """Map a video's duration (s) to a pace tier — longer videos cut less often (Adam's rule).
    Thresholds in EDIT_CONSTRAINTS['pace_by_duration']."""
    for thresh, pace in EDIT_CONSTRAINTS["pace_by_duration"]:
        if dur < thresh:
            return pace
    return "balanced"


def plan_cuts(script: str, available_angles: list[str], *,
              gemini_api_key: str | None = None, model: str = "gemini-2.5-pro",
              pace: str = "balanced", max_shot_s: float | None = None,
              min_shot_s: float | None = None) -> list[dict]:
    """Ask the LLM to segment the script into camera cuts per the intent-driven ruleset.

    Returns [{"text", "angle", "intent", "rationale"}] in script order. The planner may only use
    angles in `available_angles`. `pace` ∈ {"social","balanced","explainer"} tunes cut frequency.
    """
    from google import genai
    key = gemini_api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set (see [[reference_gemini_api_key]])")
    client = genai.Client(api_key=key)
    sys_prompt = planner_system_prompt(available_angles, pace=pace, max_shot_s=max_shot_s, min_shot_s=min_shot_s)
    resp = client.models.generate_content(
        model=model,
        contents=f"{sys_prompt}\n\nSCRIPT:\n\"\"\"\n{script.strip()}\n\"\"\"",
    )
    raw = (resp.text or "").strip()
    cuts = _extract_json_array(raw)
    allowed = set(available_angles)
    for c in cuts:
        if c.get("angle") not in allowed:
            # snap an out-of-set angle to the nearest allowed one by framing role
            c["angle"] = _nearest_allowed_angle(c.get("angle", ""), available_angles)
    return cuts


def _extract_json_array(raw: str) -> list[dict]:
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"cut planner returned no JSON array:\n{raw[:500]}")
    return json.loads(raw[start:end + 1])


def _nearest_allowed_angle(angle: str, available: list[str]) -> str:
    if angle in available:
        return angle
    want_size = ANGLE_ROLES.get(angle, {}).get("framing_size")
    for a in available:
        if ANGLE_ROLES.get(a, {}).get("framing_size") == want_size:
            return a
    return BASE_ANGLE if BASE_ANGLE in available else available[0]


# ---------------------------------------------------------------------------
# 3. Resolve text spans -> seconds (align cut spans to the word stream)
# ---------------------------------------------------------------------------
_TOK = re.compile(r"\w+")


def _norm_tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOK.findall(text)]


def resolve_timings(cuts: list[dict], words: list[dict], script: str,
                    pause_snap_s: float = 0.18) -> list[dict]:
    """Attach [t_start, t_end] to each cut by aligning its token span to the Whisper words.

    Walks the word stream cut-by-cut (cuts are contiguous + in order). Boundaries are
    snapped to the nearest inter-word pause >= pause_snap_s so cuts land in silence, never
    mid-word. The result is gap-free: each cut's t_end is the next cut's t_start.
    """
    n = len(words)
    word_toks = [_norm_tokens(w["word"]) for w in words]
    flat: list[tuple[int, str]] = [(wi, t) for wi, toks in enumerate(word_toks) for t in toks]
    cursor = 0  # index into `flat`
    spans: list[tuple[int, int]] = []  # (first_word_idx, last_word_idx) per cut
    for c in cuts:
        ctoks = _norm_tokens(c.get("text", ""))
        if not ctoks:
            spans.append((words_idx(flat, cursor), words_idx(flat, cursor)))
            continue
        # match this cut's token count starting at cursor (tolerant to tiny ASR diffs)
        take = len(ctoks)
        first_word = words_idx(flat, min(cursor, len(flat) - 1))
        end_pos = min(cursor + take, len(flat)) - 1
        last_word = words_idx(flat, max(end_pos, 0))
        spans.append((first_word, last_word))
        cursor = min(cursor + take, len(flat))

    sheet: list[dict] = []
    for i, (c, (fw, lw)) in enumerate(zip(cuts, spans)):
        t_start = words[fw]["start"] if i > 0 else 0.0
        t_end = words[lw]["end"]
        sheet.append({**c, "idx": i, "t_start": float(t_start), "t_end": float(t_end)})

    # snap each interior boundary to the midpoint of the gap between adjacent words
    for i in range(1, len(sheet)):
        b = sheet[i]["t_start"]
        # find the word pair straddling b
        snapped = b
        for wi in range(1, n):
            gap = words[wi]["start"] - words[wi - 1]["end"]
            if words[wi - 1]["end"] <= b <= words[wi]["start"] + 0.001 and gap >= pause_snap_s:
                snapped = (words[wi - 1]["end"] + words[wi]["start"]) / 2
                break
        sheet[i]["t_start"] = snapped
        sheet[i - 1]["t_end"] = snapped
    # last cut runs to the end of speech
    sheet[-1]["t_end"] = max(sheet[-1]["t_end"], words[-1]["end"])
    return sheet


def words_idx(flat: list[tuple[int, str]], pos: int) -> int:
    if not flat:
        return 0
    pos = max(0, min(pos, len(flat) - 1))
    return flat[pos][0]


# ---------------------------------------------------------------------------
# 4. Enforce hard edit constraints (repair, don't just reject)
# ---------------------------------------------------------------------------
def enforce_constraints(sheet: list[dict], available_angles: list[str],
                        max_shot_s: float | None = None, min_shot_s: float | None = None) -> list[dict]:
    """Repair the timed sheet so it satisfies EDIT_CONSTRAINTS.

    - merge shots shorter than min_shot_s into a neighbor
    - split shots longer than max_shot_s (alternate to a complementary angle)
    - eliminate same-angle adjacency (and require a framing-size change between cuts)

    `max_shot_s` overrides the global cap (longer videos get a longer hold → fewer cuts).
    """
    c = EDIT_CONSTRAINTS
    max_shot = max_shot_s if max_shot_s is not None else c["max_shot_s"]
    min_shot = min_shot_s if min_shot_s is not None else c["min_shot_s"]
    chosen = select_angles(available_angles)
    sheet = [dict(s) for s in sheet]

    # 4a. merge too-short shots into the previous (or next) shot
    merged: list[dict] = []
    for s in sheet:
        dur = s["t_end"] - s["t_start"]
        if merged and dur < min_shot:
            merged[-1]["t_end"] = s["t_end"]
            merged[-1]["text"] = (merged[-1]["text"] + " " + s["text"]).strip()
        else:
            merged.append(s)
    # if the very first shot is too short, fold it forward
    if len(merged) > 1 and (merged[0]["t_end"] - merged[0]["t_start"]) < min_shot:
        merged[1]["t_start"] = merged[0]["t_start"]
        merged[1]["text"] = (merged[0]["text"] + " " + merged[1]["text"]).strip()
        merged = merged[1:]

    # 4b. split too-long shots in half, alternating angle
    split: list[dict] = []
    for s in merged:
        dur = s["t_end"] - s["t_start"]
        if dur > max_shot:
            parts = int(dur // max_shot) + 1
            step = dur / parts
            alt = _complement_angle(s["angle"], chosen)
            for p in range(parts):
                split.append({
                    **s,
                    "angle": s["angle"] if p % 2 == 0 else alt,
                    "t_start": s["t_start"] + step * p,
                    "t_end": s["t_start"] + step * (p + 1),
                    "rationale": s.get("rationale", "") + (" (auto-split long shot)" if p else ""),
                })
        else:
            split.append(s)

    # 4c. excursion discipline (home-anchored, no ping-pong, earned/isolated close-ups).
    #     The LLM assigns intent; here we REPAIR violations of the return-home rule.
    last = len(split) - 1
    for i in range(1, len(split)):
        prev, cur = split[i - 1], split[i]
        # never two close-ups in a row
        if cur["angle"] == "close_up" and prev["angle"] == "close_up":
            cur["angle"] = HOME
            continue
        # no excursion straight into another excursion — reset to HOME between them
        # (the closer is allowed to be an excursion, e.g. a CTA close-up after a side beat)
        if c["no_ping_pong"] and cur["angle"] in EXCURSIONS and prev["angle"] in EXCURSIONS and i != last:
            cur["angle"] = HOME

    # 4d. final adjacency cleanup — no two identical angles back to back
    for i in range(1, len(split)):
        if split[i]["angle"] == split[i - 1]["angle"]:
            split[i]["angle"] = _complement_angle(split[i - 1]["angle"], chosen)

    for i, s in enumerate(split):
        s["idx"] = i
    return split


def _complement_angle(angle: str, chosen: list[str]) -> str:
    """De-jump helper. If a repeated angle is an excursion, reset to HOME; if HOME repeats,
    step out to the first available excursion. Keeps the home-anchored rhythm."""
    if angle in EXCURSIONS and HOME in chosen:
        return HOME
    for a in chosen:
        if a in EXCURSIONS and a != angle:
            return a
    for a in chosen:
        if a != angle:
            return a
    return angle


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
_SIDE_ANGLES = {"left_45", "right_45", "side_view"}


def bridge_side_closeups(sheet: list[dict], available_angles: list[str]) -> list[dict]:
    """MASKING CUT (EDIT_CONSTRAINTS['side_enters_via_closeup']): ensure every side-angle reveal is
    entered via a close-up, never straight from a wide shot. The close-up is a crop of the SAME home
    footage, so HOME→close_up is a seamless punch-in and close_up→side hides the background change.

    For each side shot not already preceded by a close_up: carve a `closeup_bridge_s` close-up off
    the tail of the preceding HOME shot (or relabel a short preceding HOME, or steal the side's own
    head if there's no wide shot before it). No-op if close_up isn't in the palette.
    """
    if "close_up" not in available_angles or not EDIT_CONSTRAINTS.get("side_enters_via_closeup"):
        return sheet
    b = float(EDIT_CONSTRAINTS.get("closeup_bridge_s", 1.8))
    min_remain = 1.4
    out: list[dict] = []
    for c in sheet:
        if c["angle"] in _SIDE_ANGLES:
            prev = out[-1] if out else None
            if prev and prev["angle"] == "close_up":
                pass
            elif prev and prev["angle"] == HOME and (prev["t_end"] - prev["t_start"]) >= b + min_remain:
                split = round(prev["t_end"] - b, 2)
                out.append({"angle": "close_up", "t_start": split, "t_end": prev["t_end"],
                            "intent": "mask", "rationale": "masking close-up before the side reveal"})
                prev["t_end"] = split
            elif prev and prev["angle"] == HOME:
                prev["angle"] = "close_up"; prev["rationale"] = "masking close-up before the side reveal"
            elif (c["t_end"] - c["t_start"]) > b + min_remain:
                split = round(c["t_start"] + b, 2)
                out.append({"angle": "close_up", "t_start": c["t_start"], "t_end": split,
                            "intent": "mask", "rationale": "masking close-up before the side reveal"})
                c = dict(c); c["t_start"] = split
        out.append(c)
    for i, c in enumerate(out):
        c["idx"] = i
    return out


def build_cut_sheet(script: str, master_audio: str | Path, available_angles: list[str], *,
                    gemini_api_key: str | None = None, whisper_model: str = "base",
                    pace: str = "auto", out_path: str | Path | None = None) -> list[dict]:
    """End-to-end: transcribe -> plan -> time -> enforce -> mask side reveals with a close-up bridge.
    pace='auto' (default) scales cut frequency to the video's DURATION — longer videos cut less
    often (longer avg shot + a longer max-shot cap). Pass an explicit tier to override.
    Writes cut_sheet.json if out_path given."""
    words = transcribe_words(master_audio, model=whisper_model)
    dur = words[-1]["end"] if words else 0.0
    if pace == "auto":
        pace = pace_for_duration(dur)
    max_shot = EDIT_CONSTRAINTS["pace_max_shot_s"].get(pace, EDIT_CONSTRAINTS["max_shot_s"])
    min_shot = EDIT_CONSTRAINTS["pace_min_shot_s"].get(pace, EDIT_CONSTRAINTS["min_shot_s"])
    cuts = plan_cuts(script, available_angles, gemini_api_key=gemini_api_key, pace=pace,
                     max_shot_s=max_shot, min_shot_s=min_shot)
    timed = resolve_timings(cuts, words, script)
    final = enforce_constraints(timed, available_angles, max_shot_s=max_shot, min_shot_s=min_shot)
    final = bridge_side_closeups(final, available_angles)
    if out_path:
        Path(out_path).write_text(json.dumps(final, indent=2))
    return final


__all__ = [
    "transcribe_words", "plan_cuts", "resolve_timings", "enforce_constraints", "build_cut_sheet",
]
