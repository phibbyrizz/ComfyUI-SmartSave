import os
import re
import json
import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import folder_paths
import ollama

# In-memory session cache to prevent repeated Ollama calls for identical prompts
PROMPT_CACHE = {}

class SmartSaveImage:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "images": ("IMAGE",),
                "positive_prompt": ("STRING", {"forceInput": True}),
                "format": (["PNG", "JPG"], {"default": "PNG"}),
                "subfolder_prefix": ("STRING", {"default": ""}),
                "quality": ("INT", {"default": 95, "min": 1, "max": 100, "step": 1}),
                "ollama_model": ("STRING", {"default": "llama3.2:3b"}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO"
            },
        }

    RETURN_TYPES = ()
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    CATEGORY = "SmartSave"

    def _extract_subject(self, prompt_text: str, model: str) -> str:
        cached = PROMPT_CACHE.get(prompt_text)
        if cached:
            return cached

        system_instruction = (
            "Extract the primary subject or character name from the prompt. "
            "Respond ONLY with the name (1-3 words maximum, title case). "
            "Do not include style terms, lighting, camera angles, or explanations. "
            "If no distinct subject is found, respond with: Miscellaneous"
        )

        try:
            response = ollama.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt_text}
                ],
                options={"temperature": 0.0}
            )
            raw_result = response.get("message", {}).get("content", "").strip()
            
            # Sanitize output: remove invalid filesystem characters
            clean_name = re.sub(r'[\\/*?:"<>|]', '', raw_result).strip()
            clean_name = clean_name.replace(".", "").strip()
            
            if not clean_name:
                clean_name = "Miscellaneous"
                
        except Exception as err:
            print(f"[SmartSave] Ollama extraction failed ({err}), falling back to 'Miscellaneous'")
            clean_name = "Miscellaneous"

        PROMPT_CACHE[prompt_text] = clean_name
        return clean_name

    def _get_next_index(self, folder_path: str, base_filename: str) -> int:
        os.makedirs(folder_path, exist_ok=True)
        max_idx = 0
        pattern = re.compile(rf"^{re.escape(base_filename)}_(\d+)\.(png|jpg|jpeg)$", re.IGNORECASE)

        for filename in os.listdir(folder_path):
            match = pattern.match(filename)
            if match:
                idx = int(match.group(1))
                if idx > max_idx:
                    max_idx = idx

        return max_idx + 1

    def save_images(
        self,
        images,
        positive_prompt: str,
        format: str = "PNG",
        subfolder_prefix: str = "",
        quality: int = 95,
        ollama_model: str = "llama3.2:3b",
        prompt=None,
        extra_pnginfo=None
    ):
        subject = self._extract_subject(positive_prompt, ollama_model)

        prefix = subfolder_prefix.strip().strip("/\\")
        if prefix:
            target_dir = os.path.join(self.output_dir, prefix, subject)
        else:
            target_dir = os.path.join(self.output_dir, subject)

        next_idx = self._get_next_index(target_dir, subject)

        results = []

        for image in images:
            i = 255.0 * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            file_stem = f"{subject}_{next_idx:04d}"

            if format == "PNG":
                filename = f"{file_stem}.png"
                filepath = os.path.join(target_dir, filename)

                metadata = PngInfo()
                if prompt is not None:
                    metadata.add_text("prompt", json.dumps(prompt))
                if extra_pnginfo is not None:
                    for k, v in extra_pnginfo.items():
                        metadata.add_text(k, json.dumps(v))

                img.save(filepath, pnginfo=metadata, compress_level=4)

            else:
                filename = f"{file_stem}.jpg"
                filepath = os.path.join(target_dir, filename)
                
                # Convert RGBA to RGB for JPEG compatibility
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                    
                img.save(filepath, quality=quality, optimize=True)

            subfolder_out = os.path.relpath(target_dir, self.output_dir)
            results.append({
                "filename": filename,
                "subfolder": subfolder_out,
                "type": self.type
            })

            next_idx += 1

        return {"ui": {"images": results}}