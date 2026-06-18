import sys, json
from pathlib import Path
sys.path.insert(0, str(Path.home()/".claude/skills/av-image-n-looks"))
from common.image_n import generate_look_pack

BASE_URL = "https://resource2.heygen.ai/image/1509bc0750fe44059a0d3b946599356a/original.png"
TWIN = "https://resource2.heygen.ai/avatar/v3/dd6d517d0af24181a86f2ca87f3d74dc/half/2.2/preview_video_target.mp4"

res = generate_look_pack(
    Path("outputs/imagen_angles"), scenes=None, mode="angles",
    reference_video_url=TWIN, look_image_url=BASE_URL,
    reference_image="outputs/angles/straight_on.png",
    aspect_ratio="9:16", username="multiangle_adam",
)
print("ARCFACE_RESULT " + json.dumps({"arcface": res["arcface"], "mean": res["mean_arcface"],
                                      "frames": res["frames"], "mode": res["mode"]}))
