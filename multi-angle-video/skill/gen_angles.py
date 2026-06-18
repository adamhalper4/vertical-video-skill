import os, sys
from google import genai
from google.genai import types
from PIL import Image
import io

c = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
base = Image.open("outputs/angles/straight_on.png")
MODEL = "gemini-3-pro-image"

ANGLES = {
  "close_up": ("Reframe this exact person as a tight HEAD-AND-SHOULDERS CLOSE-UP shot. "
    "Keep the identical face, identity, hairstyle (hair fully visible), clean-shaven, "
    "same navy suit and white shirt, same background and lighting. Camera pushed in close, "
    "eyes in upper third. Vertical 9:16 portrait. Photorealistic. No text, no logos, no captions."),
  "left_45": ("Reframe this exact person at a THREE-QUARTER angle, turned about 45 degrees to his left "
    "(camera sees the right side of his face more). Medium shot, head-and-chest. Keep the identical "
    "face, identity, hairstyle (hair fully visible), clean-shaven, same navy suit and white shirt, "
    "same background and lighting. Vertical 9:16 portrait. Photorealistic. No text, no logos, no captions."),
}

for slug, prompt in ANGLES.items():
    r = c.models.generate_content(model=MODEL, contents=[prompt, base])
    saved = False
    for part in r.candidates[0].content.parts:
        if getattr(part, "inline_data", None) and part.inline_data.data:
            img = Image.open(io.BytesIO(part.inline_data.data)).convert("RGB")
            out = f"outputs/angles/{slug}.png"
            img.save(out)
            print(f"{slug}: saved {img.size} -> {out}")
            saved = True
            break
    if not saved:
        print(f"{slug}: NO IMAGE returned; text={getattr(r,'text','')[:200]}")
print("DONE")
