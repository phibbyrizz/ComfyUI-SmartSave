import os
import re
import json
import folder_paths
import ollama
import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

# In-memory session cache to prevent repeated Ollama calls for identical prompts
PROMPT_CACHE = {}

class SmartSaveImage:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"
        self.prefix_append = ""

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
    CATEGORY = "image/saving"

    def clean_subject_name(self, raw_subject):
        """Deterministically strips conjunctions, punctuation, and invalid chars."""
        if not raw_subject:
            return "Misc"

        subject = raw_subject.strip()

        # Hard clamp: Split on conjunctions or separators if the LLM slipped
        delimiters = r"\s+(?:and|&|with)\s+|,|\+"
        parts = re.split(delimiters, subject, flags=re.IGNORECASE)
        if parts:
            subject = parts[0].strip()

        # Strip illegal filesystem characters for Windows/Linux
        subject = re.sub(r'[\\/*?:"<>|]', "", subject)
        
        # Replace remaining whitespace cleanly
        subject = re.sub(r"\s+", "_", subject).strip("._ ")

        return subject if subject else "Misc"

    def query_ollama(self, prompt_text, model):
        """Sends the generation prompt to Ollama with strict single-subject constraints."""
        if prompt_text in PROMPT_CACHE:
            return PROMPT_CACHE[prompt_text]

        system_instruction = (
            "You are a file classifier. Extract the SINGLE primary subject or character name from the prompt.\n"
            "Rules:\n"
            "1. If multiple people, characters, or subjects are listed, pick ONLY the first one mentioned in the text.\n"
            "2. NEVER combine names using words like 'and', 'with', '&', or commas.\n"
            "3. Return ONLY the single name (1 to 3 words maximum), with no explanation, punctuation, quotes, or markdown."
        )

        try:
            response = ollama.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": f"Extract the single primary subject from this prompt:\n{prompt_text}"}
                ],
                options={"temperature": 0.0}
            )
            raw_result = response["message"]["content"].strip()
            subject = self.clean_subject_name(raw_result)
        except Exception as e:
            print(f"[SmartSave Warning] Ollama query failed: {e}. Falling back to 'Misc'.")
            subject = "Misc"

        PROMPT_CACHE[prompt_text] = subject
        return subject

    def save_images(self, images, positive_prompt, format="PNG", subfolder_prefix="", quality=95, ollama_model="llama3.2:3b", prompt=None, extra_pnginfo=None):
        subject_folder = self.query_ollama(positive_prompt, ollama_model)

        # Build subfolder path
        if subfolder_prefix.strip():
            subfolder = os.path.join(subfolder_prefix.strip(), subject_folder)
        else:
            subfolder = subject_folder

        target_dir = os.path.join(self.output_dir, subfolder)
        os.makedirs(target_dir, exist_ok=True)

        ext = format.lower()

        # Sequence counting
        existing_files = os.listdir(target_dir)
        indices = []
        pattern = re.compile(rf"^{re.escape(subject_folder)}_(\d+)\.{ext}$")
        for f in existing_files:
            match = pattern.match(f)
            if match:
                indices.append(int(match.group(1)))
        
        counter = max(indices) + 1 if indices else 1

        results = []
        for image in images:
            i = 255.0 * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            filename = f"{subject_folder}_{counter:04d}.{ext}"
            filepath = os.path.join(target_dir, filename)

            if ext == "png":
                metadata = PngInfo()
                if prompt is not None:
                    metadata.add_text("prompt", json.dumps(prompt))
                if extra_pnginfo is not None:
                    for k, v in extra_pnginfo.items():
                        metadata.add_text(k, json.dumps(v))
                img.save(filepath, pnginfo=metadata, compress_level=4)
            else:
                img.save(filepath, quality=quality)

            results.append({
                "filename": filename,
                "subfolder": subfolder,
                "type": self.type
            })
            counter += 1

        return {"ui": {"images": results}}

NODE_CLASS_MAPPINGS = {
    "SmartSaveImage": SmartSaveImage
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SmartSaveImage": "Smart LLM Save Image"
}