import sys, subprocess
from pathlib import Path
sys.path.insert(0, str(Path.home()/".claude/skills/av-image-n-looks"))
from common.image_n import generate_scene_video
HOME="https://www.heygenverse.com/s/e39e5d21-6b1c-422d-9b4a-1a4126ddc522/raw"
P=("Over the first three seconds the camera arcs smoothly around to the person's LEFT and settles at a "
 "45-degree THREE-QUARTER angle, then HOLDS steady there for the rest of the shot. At the hold, the "
 "person is in a clean three-quarter view (face still clearly visible, NOT a full profile), body angled "
 "about 45 degrees, gaze ahead off-camera. It is the SAME ROOM and SAME BACKGROUND the entire time — the "
 "identical floor-to-ceiling window, the same New York City skyline, the same desk and chair — just seen "
 "from the side so the window and skyline shift across the frame with correct perspective. Do not invent "
 "a new background.")
out=Path("outputs/orbit_test/hold.mp4")
print("VIDEO", generate_scene_video(None,[P],out,look_image_url=HOME,look_image_role="first_frame",aspect_ratio="9:16",duration_s=6,username="orbit_hold"))
for t in ["5.5","5.0","4.5"]:
    subprocess.run(["ffmpeg","-y","-loglevel","error","-ss",t,"-i",str(out),"-frames:v","1",f"outputs/orbit_test/hold_{t}.jpg"])
print("DONE")
