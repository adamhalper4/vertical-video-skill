import sys, subprocess
from pathlib import Path
sys.path.insert(0, str(Path.home()/".claude/skills/av-image-n-looks"))
from common.image_n import generate_scene_video
HOME="https://www.heygenverse.com/s/e39e5d21-6b1c-422d-9b4a-1a4126ddc522/raw"
ORBIT=("The camera makes a clear, decisive arc around to the person's LEFT and ENDS at a true "
 "45-degree three-quarter side angle: by the final second the person is seen in three-quarter view, "
 "his body and face angled about 45 degrees away from the lens, looking ahead off-camera. Throughout, "
 "it is the SAME ROOM and SAME BACKGROUND — the identical floor-to-ceiling window, the same New York "
 "City skyline, the same desk and office chair — simply viewed from the new camera position, so the "
 "window and skyline slide across the frame with strong, correct perspective and parallax. Do NOT "
 "change or invent the background; only move the camera to the side.")
out=Path("outputs/orbit_test/left45_orbit2.mp4")
info=generate_scene_video(None,[ORBIT],out,look_image_url=HOME,look_image_role="first_frame",
                          aspect_ratio="9:16",duration_s=7,username="orbit_test2")
print("VIDEO",info)
subprocess.run(["ffmpeg","-y","-loglevel","error","-sseof","-0.2","-i",str(out),"-frames:v","1","outputs/orbit_test/v2_end_45.jpg"])
subprocess.run(["ffmpeg","-y","-loglevel","error","-ss","3.5","-i",str(out),"-frames:v","1","outputs/orbit_test/v2_mid.jpg"])
print("DONE")
