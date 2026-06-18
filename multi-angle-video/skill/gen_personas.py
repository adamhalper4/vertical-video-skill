import os, io
from google import genai
from PIL import Image
c = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL="gemini-3-pro-image"
PERSONAS = {
 "p_priya": ("Photorealistic portrait photograph of a confident South Asian woman in her late 30s, "
   "a dermatologist, wearing a clean white medical coat over a soft blue blouse. Bright modern "
   "dermatology clinic softly blurred behind her, natural daylight. Head-and-shoulders medium shot, "
   "she faces the camera with a warm professional expression, hair neatly down and fully visible, "
   "clean clear skin. Vertical 9:16 portrait, sharp, realistic. No text, no logos, no watermarks."),
 "p_marcus": ("Photorealistic portrait photograph of an athletic Black man in his early 30s, a fitness "
   "coach, wearing a fitted charcoal-grey performance t-shirt. Modern gym with equipment softly blurred "
   "behind him, clean lighting. Head-and-shoulders medium shot, he faces the camera with an energetic "
   "confident expression, short hair fully visible, light stubble. Vertical 9:16 portrait, sharp, "
   "realistic. No text, no logos, no watermarks."),
}
for slug, prompt in PERSONAS.items():
    r = c.models.generate_content(model=MODEL, contents=[prompt])
    for part in r.candidates[0].content.parts:
        if getattr(part,"inline_data",None) and part.inline_data.data:
            img = Image.open(io.BytesIO(part.inline_data.data)).convert("RGB")
            out = f"outputs/{slug}/angles/base.png"; img.save(out)
            print(f"{slug}: {img.size} -> {out}"); break
    else:
        print(f"{slug}: NO IMAGE; text={getattr(r,'text','')[:200]}")
print("DONE")
