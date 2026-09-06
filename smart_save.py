import os
import re
import json
import torch
import numpy as np
from pathlib import Path
from PIL import Image
import piexif
import folder_paths
import ollama

NON_PERSON_PATTERNS = [
    r"\bcandid\b", r"\bshot\b", r"\bsmartphone\b", r"\bthree-quarter\b",
    r"\bmedium-long\b", r"\bover-the-shoulder\b", r"\bblonde woman\b",
    r"\bbrunette woman\b", r"\bphoto\b", r"\bportrait\b", r"\bcinematic\b",
    r"\bdepth of field\b", r"\bphotorealistic\b", r"\blighting\b"
]

class SmartSaveImage:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"
        self.prompt_cache = {}

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "positive_prompt": ("STRING", {"forceInput": True}),
                "format": (["PNG", "JPG"], {"default": "PNG"}),
                "subfolder_prefix": ("STRING", {"default": "Raw"}),
                "jpg_quality": ("INT", {"default": 95, "min": 1, "max": 100, "step": 1}),
                "ollama_model": ("STRING", {"default": "llama3.2:3b"}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO"
            }
        }

    RETURN_TYPES = ()
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    CATEGORY = "image/saving"

    def sanitize_subject(self, raw_name: str) -> str:
        clean = re.sub(r'[\\/*?:"<>|\n\r\']', "", raw_name).strip()
        clean = re.sub(r"\s+", " ", clean)

        split_match = re.split(r'\s*(?:,|&|\band\b|\bwith\b|\bvs\b|\/)\s*', clean, flags=re.IGNORECASE)
        if split_match:
            clean = split_match[0].strip()

        clean_lower = clean.lower()
        for pattern in NON_PERSON_PATTERNS:
            if re.search(pattern, clean_lower):
                return "Miscellaneous"

        clean = re.sub(r'\b(and|with|the|a|an)\b$', '', clean, flags=re.IGNORECASE).strip()
        words = clean.split()
        if len(words) > 2:
            clean = " ".join(words[:2])

        return clean.title() if len(clean) >= 3 else "Miscellaneous"

    def extract_subject_llm(self, prompt_text: str, model_name: str) -> str:
        if not prompt_text.strip():
            return "Miscellaneous"

        if prompt_text in self.prompt_cache:
            return self.prompt_cache[prompt_text]

        system_instruction = (
            "You are an image library classifier. Identify the SINGLE primary real person, fictional character, "
            "or celebrity explicitly named in the prompt tags.\n\n"
            "Strict Rules:\n"
            "1. Output ONLY ONE individual. Never join multiple names with 'and', '&', or commas.\n"
            "2. If multiple people are named, pick strictly the first person.\n"
            "3. Ignore camera angles, photographic styles, and generic descriptors.\n"
            "4. If NO named person or established character exists, return 'Miscellaneous'.\n"
            "5. Respond strictly with JSON: {\"subject\": \"FirstName LastName\"}"
        )

        try:
            client = ollama.Client(timeout=8.0)
            response = client.chat(
                model=model_name,
                format="json",
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": f"Tags:\n{prompt_text[:1500]}"}
                ],
                options={"temperature": 0.0}
            )
            parsed = json.loads(response["message"]["content"])
            raw_name = parsed.get("subject", "Miscellaneous")
            subject = self.sanitize_subject(raw_name)
        except Exception as e:
            err_str = str(e).lower()
            if "connection" in err_str or "refused" in err_str:
                print(f"\n[SmartSave WARNING] Could not connect to Ollama at http://127.0.0.1:11434.")
                print(f"[SmartSave WARNING] Ensure the Ollama app or service is running. Routing image to 'Miscellaneous'.\n")
            elif "not found" in err_str or "404" in err_str:
                print(f"\n[SmartSave WARNING] Model '{model_name}' was not found in your Ollama library.")
                print(f"[SmartSave WARNING] Run: 'ollama pull {model_name}' in your terminal. Routing image to 'Miscellaneous'.\n")
            else:
                print(f"\n[SmartSave WARNING] LLM classification error: {e}. Routing image to 'Miscellaneous'.\n")
            subject = "Miscellaneous"

        self.prompt_cache[prompt_text] = subject
        return subject

    def save_images(self, images, positive_prompt, format="PNG", subfolder_prefix="Raw", jpg_quality=95, ollama_model="llama3.2:3b", prompt=None, extra_pnginfo=None):
        subject = self.extract_subject_llm(positive_prompt, ollama_model)

        base_path = Path(self.output_dir)
        if subfolder_prefix.strip():
            target_dir = base_path / subfolder_prefix.strip() / subject
        else:
            target_dir = base_path / subject

        target_dir.mkdir(parents=True, exist_ok=True)

        ext = ".png" if format.upper() == "PNG" else ".jpg"

        existing = list(target_dir.glob(f"{subject}_*{ext}"))
        indices = []
        for f in existing:
            m = re.search(rf"^{re.escape(subject)}_(\d+){re.escape(ext)}$", f.name)
            if m:
                indices.append(int(m.group(1)))
        counter = max(indices) + 1 if indices else 1

        results = []
        for image in images:
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            filename = f"{subject}_{counter:04d}{ext}"
            file_path = target_dir / filename

            if format.upper() == "PNG":
                metadata = None
                if prompt is not None or extra_pnginfo is not None:
                    from PIL.PngImagePlugin import PngInfo
                    metadata = PngInfo()
                    if prompt is not None:
                        metadata.add_text("prompt", json.dumps(prompt))
                    if extra_pnginfo is not None:
                        for k, v in extra_pnginfo.items():
                            metadata.add_text(k, json.dumps(v))
                img.save(str(file_path), pnginfo=metadata, compress_level=4)
            else:
                exif_bytes = b""
                if prompt is not None:
                    try:
                        dumped_prompt = json.dumps(prompt)
                        exif_dict = {"0th": {piexif.ImageIFD.Make: f"Prompt:{dumped_prompt}"}}
                        exif_bytes = piexif.dump(exif_dict)
                    except Exception:
                        pass
                if exif_bytes:
                    img.save(str(file_path), quality=jpg_quality, exif=exif_bytes)
                else:
                    img.save(str(file_path), quality=jpg_quality)

            results.append({"filename": filename, "subfolder": str(target_dir.relative_to(base_path)), "type": self.type})
            counter += 1

        return {"ui": {"images": results}}