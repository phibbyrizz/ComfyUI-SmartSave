import os
import re
import json
import folder_paths
import ollama
import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import piexif

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
        """Deterministically strips conjunctions, punctuation, and invalid characters."""
        if not raw_subject:
            return "Misc"

        subject = raw_subject.strip()

        # Hard clamp: Split on conjunctions between distinct multiple people
        delimiters = r"\s+(?:and|&|with)\s+|,|\+"
        parts = re.split(delimiters, subject, flags=re.IGNORECASE)
        if parts:
            subject = parts[0].strip()

        # Remove illegal file characters
        subject = re.sub(r'[\\/*?:"<>|]', "", subject)
        
        # Standardize spaces cleanly
        subject = re.sub(r"[\s_]+", " ", subject).strip("._ ")

        return subject.title() if subject else "Misc"

    def resolve_existing_folder(self, base_dir, subject):
        """Matches against existing folders ignoring case, spaces, and underscores.
        Attaches single first-names (e.g. 'Billie') to full names (e.g. 'Billie Eilish')."""
        if not os.path.exists(base_dir):
            return subject

        existing_folders = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]

        def normalize(name):
            return re.sub(r'[\s_]+', '', name).lower()

        norm_subject = normalize(subject)

        # 1. Exact normalized match (e.g., 'billie_eilish' matches 'Billie Eilish')
        for folder in existing_folders:
            if normalize(folder) == norm_subject:
                return folder

        # 2. First-name prefix match (e.g., 'Billie' routes to existing 'Billie Eilish')
        for folder in existing_folders:
            norm_folder = normalize(folder)
            if norm_folder.startswith(norm_subject):
                return folder

        # 3. Default to clean Title Case with spaces
        return subject.replace("_", " ").title()

    def query_ollama(self, prompt_text, model):
        """Extracts the complete name of the first primary subject."""
        if prompt_text in PROMPT_CACHE:
            return PROMPT_CACHE[prompt_text]

        system_instruction = (
            "You are a file naming classifier. Extract the complete name of the FIRST subject or character mentioned in the prompt.\n"
            "Rules:\n"
            "1. Always include BOTH the first and last name if provided (e.g., 'Selena Gomez', not just 'Selena').\n"
            "2. If multiple characters or subjects appear, output ONLY the first one.\n"
            "3. Do NOT include conjunctions like 'and', 'with', or '&'.\n"
            "4. Return ONLY the name (maximum 3 words), with zero extra words, quotes, or punctuation."
        )

        try:
            response = ollama.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": f"Extract the full name of the first primary subject from this prompt:\n{prompt_text}"}
                ],
                options={"temperature": 0.0},
                keep_alive=0  # Frees Ollama from memory immediately after execution
            )
            raw_result = response["message"]["content"].strip()
            subject = self.clean_subject_name(raw_result)
        except Exception as e:
            print(f"[SmartSave Warning] Ollama query failed: {e}. Falling back to 'Misc'.")
            subject = "Misc"

        PROMPT_CACHE[prompt_text] = subject
        return subject

    def save_images(self, images, positive_prompt, format="PNG", subfolder_prefix="", quality=95, ollama_model="llama3.2:3b", prompt=None, extra_pnginfo=None):
        raw_subject = self.query_ollama(positive_prompt, ollama_model)

        # Base parent path
        parent_dir = os.path.join(self.output_dir, subfolder_prefix.strip()) if subfolder_prefix.strip() else self.output_dir

        # Match against existing folders to prevent First vs Full Name splits
        subject_folder = self.resolve_existing_folder(parent_dir, raw_subject)

        if subfolder_prefix.strip():
            target_dir = os.path.join(parent_dir, subject_folder)
            subfolder = os.path.join(subfolder_prefix.strip(), subject_folder)
        else:
            target_dir = os.path.join(self.output_dir, subject_folder)
            subfolder = subject_folder

        os.makedirs(target_dir, exist_ok=True)
        ext = format.lower()

        # Clean base name for filenames (uses underscores for cross-platform compatibility)
        file_base_prefix = subject_folder.replace(" ", "_")

        existing_files = os.listdir(target_dir)
        indices = []
        pattern = re.compile(rf"^{re.escape(file_base_prefix)}_(\d+)\.{ext}$")
        for f in existing_files:
            match = pattern.match(f)
            if match:
                indices.append(int(match.group(1)))
        
        counter = max(indices) + 1 if indices else 1

        results = []
        for image in images:
            i = 255.0 * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            filename = f"{file_base_prefix}_{counter:04d}.{ext}"
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
                metadata = {}
                if prompt is not None:
                    metadata["prompt"] = prompt
                if extra_pnginfo is not None:
                    metadata.update(extra_pnginfo)

                exif_bytes = None
                if metadata:
                    user_comment = b"UNICODE\x00" + json.dumps(metadata).encode("utf-16le")
                    exif_dict = {
                        "0th": {},
                        "Exif": {piexif.ExifIFD.UserComment: user_comment},
                        "GPS": {},
                        "1st": {},
                        "thumbnail": None,
                    }
                    try:
                        exif_bytes = piexif.dump(exif_dict)
                    except Exception as e:
                        print(f"[SmartSave Warning] Failed to pack EXIF metadata: {e}")

                if exif_bytes:
                    img.save(filepath, format="JPEG", quality=quality, exif=exif_bytes)
                else:
                    img.save(filepath, format="JPEG", quality=quality)

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