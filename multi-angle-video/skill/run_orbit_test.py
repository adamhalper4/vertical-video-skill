import sys, subprocess
from pathlib import Path
sys.path.insert(0, str(Path.home()/".claude/skills/av-image-n-looks"))
from common.image_n import generate_scene_video
HOME="https://www.heygenverse.com/s/e39e5d21-6b1c-422d-9b4a-1a4126ddc522/raw"  # Adam straight_on
ORBIT=("the camera slowly arcs about 45 degrees around to the person's left while he stays seated "
 "talking; KEEP THE EXACT SAME ROOM AND BACKGROUND unchanged — the same floor-to-ceiling window, the "
 "same New York City skyline, the same desk and office chair — now seen from the new camera position "
 "with correct perspective and parallax; do NOT invent a new background, only move the camera")
out=Path("outputs/orbit_test/left45_orbit.mp4"); out.parent.mkdir(parents=True,exist_ok=True)
info=generate_scene_video(None,[ORBIT],out,look_image_url=HOME,look_image_role="first_frame",
                          aspect_ratio="9:16",duration_s=4,username="orbit_test")
print("VIDEO", info)
# grab a frame near the end (~45 degrees reached) and the start (should match home)
subprocess.run(["ffmpeg","-y","-loglevel","error","-sseof","-0.25","-i",str(out),"-frames:v","1","outputs/orbit_test/end_45.jpg"])
subprocess.run(["ffmpeg","-y","-loglevel","error","-ss","0.1","-i",str(out),"-frames:v","1","outputs/orbit_test/start_home.jpg"])
print("FRAMES_DONE")
