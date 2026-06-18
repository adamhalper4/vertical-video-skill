"""
angle_grammar — intent-driven cinematography ruleset for multi-cam cut planning.

Core principle (Adam, 2026-06-08): camera cuts GUIDE ATTENTION — they do not add random
variety. Decide the camera INTENT first (clarity, emphasis, intimacy, contrast, reset), THEN
choose the angle. `straight_on` is HOME; every excursion (a close-up or a side angle) is
purposeful and RETURNS home. No visual ping-pong.

Angle slugs match av-image-n-looks DEFAULT_ANGLES:
  straight_on, close_up, left_45, right_45, full_body, side_view
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Angle roles — each angle has a fixed MEANING (so cuts read as intentional)
# ---------------------------------------------------------------------------
ANGLE_ROLES: dict[str, dict] = {
    "straight_on": {
        "label": "Straight-on (HOME)",
        "framing_size": "medium",
        "intent": "clarity",
        "use_for": "HOME base — clarity, trust, resets, new ideas; the spine of the video. "
                   "Carries the most runtime; you RETURN here after every excursion.",
        "frontal": True,
    },
    "close_up": {
        "label": "Close-up (earned emphasis / intimacy)",
        "framing_size": "close",
        "intent": "emphasis",
        "use_for": "EARNED moments only: a major claim, an emotional peak, an important stat, a "
                   "strong CTA. Use sparingly; never two close-ups in a row.",
        "frontal": True,
    },
    "left_45": {
        "label": "Three-quarter left (REFLECTION)",
        "framing_size": "medium",
        "intent": "reflection",
        "use_for": "Reflection / aside / thinking-aloud. A temporary excursion — return home after.",
        "frontal": True,
    },
    "right_45": {
        "label": "Three-quarter right (CONTRAST)",
        "framing_size": "medium",
        "intent": "contrast",
        "use_for": "Contrast / objection / 'but…' / a shift in thought. A temporary excursion — "
                   "return home after.",
        "frontal": True,
    },
    "full_body": {
        "label": "Wide / full body (establish)",
        "framing_size": "wide",
        "intent": "establish",
        "use_for": "Opening establish, or a big reset between major sections.",
        "frontal": True,
    },
    "side_view": {
        "label": "Profile (rare accent)",
        "framing_size": "medium",
        "intent": "accent",
        "use_for": "Rare reflective / voiceover-feel accent; never for direct address.",
        "frontal": False,
    },
}

BASE_ANGLE = "straight_on"   # HOME — the anchor every excursion returns to
HOME = BASE_ANGLE
EXCURSIONS = {"close_up", "left_45", "right_45", "full_body", "side_view"}  # anything not HOME

# Fixed left/right meaning so a side cut always carries the same signal.
SIDE_MEANING = {"left_45": "reflection", "right_45": "contrast"}

# Engine: ALWAYS Avatar V (quality + no engine mismatch across cuts). By default Avatar V
# re-centers the head/gaze to the lens while talking — correct for HOME + close-up (eye contact),
# but it would undo a side camera.
#
# 3/4 CUTAWAY RECIPE to hold the off-camera gaze on Avatar V (validated 2026-06-08):
#   1. Gaze preset "Looking ahead" → motion prompt = GAZE_LOOKING_AHEAD (HeyGen's own tuned text).
#   2. "More expressive" OFF — the expressive gesturing is what re-frontalizes the head; with it
#      ON the gaze drifts back to the lens mid-clip EVEN with the gaze preset. (API limitation:
#      the "More expressive"/Avatar-V motion-type toggle is NOT exposed on the public
#      create_video_from_avatar — it lives in the Shots V2 UI (app.heygen.com/avatar/avatar-shots,
#      Presenter → Motion). So the gaze-correct 3/4 render currently requires the Shots UI, OR a
#      reference-footage anchor.) The API motionPrompt alone (More expressive implicitly ON) drifts.
#   3. (Most stable) also attach a side-angle REFERENCE FOOTAGE clip of the pose — Shots UI only.
# Audio sync: the Shots Presenter card has an "upload/record" option that accepts an audio file,
# so feed the shared master audio there to keep cuts seam-aligned (don't re-TTS the script).
# FALLBACK that satisfies gaze + audio-sync via the clean API today: Avatar IV low-expressiveness
# (animates the photo literally, preserves the off-camera gaze, takes audioUrl) — at IV quality.
ANGLE_ENGINE = {
    "straight_on": "avatar_v", "close_up": "avatar_v", "full_body": "avatar_v",
    "left_45": "avatar_v", "right_45": "avatar_v", "side_view": "avatar_v",
}

# Custom motion prompt that LOCKS the off-camera gaze for the 3/4 cutaways on Avatar V.
# This is HeyGen's OWN tuned "Looking ahead" Gaze preset (Shots UI → Motion → Gaze tab), which
# the UI injects verbatim into the custom-motion field — so it works the same passed via the API
# `motionPrompt`. Verbose ad-hoc prompts fought themselves and Avatar V re-centered to the lens;
# this concise preset is what holds the eyes aligned with the (turned) body. (Adam, 2026-06-08.)
GAZE_LOOKING_AHEAD = "looking straight ahead, without turning the eyes toward the camera"
ANGLE_MOTION_PROMPT = {
    "left_45": GAZE_LOOKING_AHEAD,
    "right_45": GAZE_LOOKING_AHEAD,
}

# Derived angles — NOT rendered separately. close_up is a digital zoom-in on the home look,
# guaranteeing identity / wardrobe / lighting match (handled in multicam.ANGLE_DERIVATIONS).
DERIVED_ANGLES = {"close_up": "straight_on"}

# NOTE (2026-06-09): 3/4 LOOKS ARE NOW GENERATED VIA common/orbit_angles.py, NOT these independent
# stills. Independent stills invent a NEW background, so a side cut reads as a different room — wrong
# for a multi-cam (the background must be the SAME scene from a rotated camera, with parallax). The
# orbit method renders the home frame as the first frame of a camera-orbit clip (same scene, camera
# moves) and frame-picks the ~45 deg three-quarter. These prompts are kept only as a fallback for
# environments without Seedance camera-orbit access.
#
# Corrected (fallback) look-GENERATION prompts for the 3/4 angles. The default Image-N angle prompts
# say "head turned slightly toward camera", which swivels the face back to the lens; these instead
# force head+eyes+torso aligned at ~45 deg with gaze OFF-camera, and avoid a 90-deg profile.
_GAZE_ALIGNED_34 = (
    "the person's head, eyes, and shoulders are ALL rotated together by about 45 degrees in the "
    "same direction; they look off along their own eyeline toward the front of the room, away from "
    "THIS camera; their gaze follows the direction the body faces; they make NO eye contact with "
    "this lens and do NOT turn the face back toward the camera; a natural candid three-quarter "
    "pose, clearly NOT a full 90-degree profile; head-and-shoulders framing"
)
ANGLE_LOOK_PROMPTS = {
    "left_45":  f"a three-quarter camera angle positioned 45 degrees to the person's left side; {_GAZE_ALIGNED_34}",
    "right_45": f"a three-quarter camera angle positioned 45 degrees to the person's right side; {_GAZE_ALIGNED_34}",
}

# ---------------------------------------------------------------------------
# Intent → angle, and the signal that triggers each intent
# ---------------------------------------------------------------------------
INTENT_TO_ANGLE: dict[str, str] = {
    "clarity": "straight_on", "reset": "straight_on", "new_idea": "straight_on",
    "emphasis": "close_up", "intimacy": "close_up",
    "reflection": "left_45", "aside": "left_45",
    "contrast": "right_45", "objection": "right_45",
}

SIGNAL_GRAMMAR: list[dict] = [
    {"signal": "New idea / new topic / general explanation / a reset", "intent": "clarity",
     "cut_to": "straight_on", "note": "home base; the default the video lives in"},
    {"signal": "Major claim · important stat · emotional peak · strong CTA (EARNED)",
     "intent": "emphasis", "cut_to": "close_up", "note": "sparingly; never back-to-back"},
    {"signal": "Aside · thinking-aloud · reflection", "intent": "reflection",
     "cut_to": "left_45", "note": "temporary — return to straight_on after"},
    {"signal": "Contrast · objection · 'but…' · shift in thought", "intent": "contrast",
     "cut_to": "right_45", "note": "temporary — return to straight_on after"},
    {"signal": "Dense information block", "intent": "hold",
     "cut_to": "HOLD the current angle, then cut on the conclusion", "note": "don't cut mid-density"},
]

# Reliable composition patterns (the planner should lean on these).
PATTERNS: list[str] = [
    "Home → Emphasis → Home",
    "Home → Contrast → Home",
    "Home → Aside → Home",
    "Build → Close-up → Release",
    "Problem → Tension → Solution",
    "Setup → Stat Close-up → Reset",
    "CTA (straight-on or close-up) → Reset",
    "Home → Close-up → Side (masked reveal) → Home",
    "Home → Zoom → Home → Zoom → Side (double-tease, then reveal)",
    "Hold during dense information → cut on the conclusion",
]
DEFAULT_RHYTHM = "straight_on → close_up → side angle → straight_on → close_up → straight_on"

# ---------------------------------------------------------------------------
# Edit constraints — HARD rules the cut sheet must satisfy
# ---------------------------------------------------------------------------
EDIT_CONSTRAINTS: dict = {
    "min_shot_s": 2.5,           # only cut when enough time has passed since the last cut
    "max_shot_s": 8.0,           # holding home for a while is fine
    "max_distinct_angles": 4,    # home + close_up + left_45 + right_45
    "home_anchor": HOME,
    "no_same_angle_adjacent": True,
    "return_home_after_excursion": True,   # excursions are bracketed by HOME (no excursion→excursion)
    "no_back_to_back_close_up": True,
    "side_enters_via_closeup": True,       # MASKING CUT: never cut wide(home)→side directly. Bridge
                                           # HOME → close_up → side. The close-up (a crop of the SAME
                                           # home footage) strips the viewer's background reference at the
                                           # moment of the reveal, so the side clip's regenerated background
                                           # can't be compared → the 2nd-camera illusion holds. close_up→side
                                           # is the ONE allowed excursion→excursion adjacency.
    "closeup_bridge_s": 1.8,               # length of the masking close-up carved in before a side
    "no_ping_pong": True,                  # reset to home before another major visual move
    "cut_on_clause_boundary": True,        # snap to a Whisper pause; never mid-word/clause
    "deliberate_open_and_close": True,
    # Pace by video kind — target average shot length (seconds). Faster for shorts, slower for
    # calm/training explainers. Used as cut-frequency guidance, not a hard rule.
    # PRINCIPLE (Adam): the LONGER the video, the LESS frequent the cuts. pace='auto' (default)
    # picks the tier from duration via cut_planner.pace_for_duration().
    "pace_avg_shot_s": {"social": 3.0, "balanced": 4.5, "explainer": 6.5, "calm": 9.0},
    # The hard max-shot cap scales with pace too, so long videos can actually hold a shot longer
    # (a single hold up to this many seconds) — that's what makes the cuts less frequent.
    "pace_max_shot_s": {"social": 6.0, "balanced": 8.0, "explainer": 11.0, "calm": 15.0},
    # MIN shot also scales with pace — the real "fewer cuts" lever: on long videos a shot must
    # hold longer before the next cut is allowed, so sub-threshold shots merge into their neighbor.
    "pace_min_shot_s": {"social": 2.0, "balanced": 2.5, "explainer": 4.0, "calm": 5.5},
    # duration (s) -> pace tier, for pace='auto'
    "pace_by_duration": [(16, "social"), (30, "balanced"), (50, "explainer"), (10**9, "calm")],
}


def select_angles(available: list[str]) -> list[str]:
    """The working palette: HOME + close_up + both side angles (left=reflection, right=contrast),
    filled with full_body / side_view only if a primary is missing."""
    pref = [BASE_ANGLE, "close_up", "left_45", "right_45", "full_body", "side_view"]
    ordered = [a for a in pref if a in available] + [a for a in available if a not in pref]
    chosen: list[str] = []
    for a in ordered:
        if a not in chosen:
            chosen.append(a)
        if len(chosen) == EDIT_CONSTRAINTS["max_distinct_angles"]:
            break
    return chosen


def select_three_angles(available: list[str]) -> list[str]:  # back-compat alias
    return select_angles(available)


def planner_system_prompt(available_angles: list[str], pace: str = "balanced",
                          max_shot_s: float | None = None, min_shot_s: float | None = None) -> str:
    """Render the intent-driven ruleset into the cut-planner system prompt.

    `pace` ∈ {"social","balanced","explainer","calm"} sets cut frequency (target avg shot length).
    `max_shot_s` overrides the hard cap shown in the prompt (defaults to the pace's cap) — longer
    videos get a longer cap so the cuts are genuinely less frequent.
    """
    roles_lines = [
        f"  - {slug} ({ANGLE_ROLES[slug]['label']}, intent={ANGLE_ROLES[slug]['intent']}): "
        f"{ANGLE_ROLES[slug]['use_for']}"
        for slug in available_angles if slug in ANGLE_ROLES
    ]
    grammar_lines = [f"  - {g['signal']} → {g['cut_to']}  ({g['note']})" for g in SIGNAL_GRAMMAR]
    c = EDIT_CONSTRAINTS
    chosen = select_angles(available_angles)
    avg = c["pace_avg_shot_s"].get(pace, c["pace_avg_shot_s"]["balanced"])
    max_shot = max_shot_s if max_shot_s is not None else c["pace_max_shot_s"].get(pace, c["max_shot_s"])
    min_shot = min_shot_s if min_shot_s is not None else c["pace_min_shot_s"].get(pace, c["min_shot_s"])

    return f"""You are a multi-camera video editor cutting a talking-head script. There is ONE
continuous voice take; you choose which camera angle is live in each moment.

OVERRIDING PRINCIPLE: camera cuts GUIDE ATTENTION — they are not for random variety. Decide the
camera INTENT first (clarity, emphasis, intimacy, contrast, reset), THEN pick the angle. Only cut
when the moment is strong enough AND enough time has passed since the last cut.

ANGLES (you may ONLY use these slugs) — each has a FIXED meaning, keep it consistent:
{chr(10).join(roles_lines)}

'{HOME}' is HOME. It is the spine of the video and carries the most runtime. Every excursion
(a close-up or a side angle) is purposeful and RETURNS to home. left_45 = reflection,
right_45 = contrast — always.

INTENT → ANGLE:
{chr(10).join(grammar_lines)}

RELIABLE PATTERNS to lean on:
{chr(10).join('  - ' + p for p in PATTERNS)}
Default reliable rhythm: {DEFAULT_RHYTHM}

HARD RULES (a plan that breaks one is invalid):
  1. Shots last {min_shot}-{max_shot}s. Target an AVERAGE shot of ~{avg}s for this
     video (pace='{pace}'). Holding home through a dense passage is good — cut on the conclusion.
     Longer videos = fewer, longer-held shots; don't cut just to add variety.
  2. Never the same angle on two adjacent shots.
  3. RETURN HOME after every excursion, and don't ping-pong between excursions — with ONE
     exception: a side angle is ENTERED through a brief close-up (HOME → close_up → side). That
     close_up→side bridge is the masking cut — the tight shot strips the background reference so the
     side camera's background can't be compared and the 2nd-camera illusion holds. Otherwise no
     excursion→excursion (no side→close_up, no left→right); reset to '{HOME}' in between.
  4. close_up is EARNED and SPARING: only major claims / key stats / emotional peaks / strong
     CTAs. Never two close-ups in a row.
  5. Side angles are TEMPORARY (usually a single shot), carry their fixed meaning
     (left=reflection, right=contrast), are ENTERED via a brief close-up (the masking cut), then
     return home. Prefer HOME → close_up → side → HOME (or HOME → zoom → HOME → zoom → side).
  6. Cut only at clause/sentence boundaries — never split a clause across two angles.
  7. Open on home or a wide establish; land the CTA on home or close_up, then it's fine to end.

OUTPUT: a JSON array of cuts in script order. Each cut:
  {{"text": "<exact contiguous span of the script for this shot>",
    "angle": "<one of the available slugs>",
    "intent": "<clarity|emphasis|intimacy|reflection|contrast|reset>",
    "rationale": "<short phrase: the pattern/intent this cut serves>"}}
The concatenation of every "text" span, in order, must equal the full script verbatim
(whitespace-normalized). Do not add, drop, or reword any words. Return ONLY the JSON array.
"""


__all__ = [
    "ANGLE_ROLES", "BASE_ANGLE", "HOME", "EXCURSIONS", "SIDE_MEANING",
    "ANGLE_ENGINE", "ANGLE_MOTION_PROMPT", "DERIVED_ANGLES", "ANGLE_LOOK_PROMPTS",
    "INTENT_TO_ANGLE", "SIGNAL_GRAMMAR", "PATTERNS", "DEFAULT_RHYTHM", "EDIT_CONSTRAINTS",
    "select_angles", "select_three_angles", "planner_system_prompt",
]
