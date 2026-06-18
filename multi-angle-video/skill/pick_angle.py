import os, json, re, glob
from google import genai
from PIL import Image
c=genai.Client(api_key=os.environ["GEMINI_API_KEY"])
best=None
for f in sorted(glob.glob("outputs/orbit_test/scan/*.jpg")):
    r=c.models.generate_content(model="gemini-2.5-pro",contents=[
      "Portrait of a seated man. Estimate his horizontal head/body turn from facing the camera. "
      'Reply JSON only: {"turn_deg": <0-90 integer>, "face_readable": true|false}. '
      "0=facing camera, 45=three-quarter, 90=full profile.", Image.open(f)])
    m=re.search(r"\{.*\}",r.text or "",re.S)
    d=json.loads(m.group(0)) if m else {}
    deg=d.get("turn_deg",-1)
    print(f"{os.path.basename(f)}: {deg}deg readable={d.get('face_readable')}",flush=True)
    # target ~45 (40-55), face readable
    if d.get("face_readable") and 38<=deg<=58:
        if best is None or abs(deg-45)<abs(best[1]-45): best=(f,deg)
print("PICK", best)
