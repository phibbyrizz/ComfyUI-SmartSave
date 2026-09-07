import os
import re
import json
import urllib.request
import urllib.error
import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import folder_paths

class SmartSaveImage:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"
        self.prefix_append = ""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "images": ("IMAGE", ),
                "filename_prefix": ("STRING", {"default": "ComfyUI"}),
                "save_raw_png": ("BOOLEAN", {"default": True}),
                "save_compressed_jpg": ("BOOLEAN", {"default": True}),
                "jpg_quality": ("INT", {"default": 90, "min": 1, "max": 100, "step": 1}),
            },
            "optional": {
                "positive_prompt": ("STRING", {"forceInput": True, "multiline": True, "default": ""}),
                "subfolder": ("STRING", {"default": ""}),
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

    def _sanitize_folder(self, name):
        clean = re.sub(r'[\\/*?:"<>|]', "", str(name)).strip()
        clean = clean.replace(" ", "_")
        return clean if clean else "Unsorted"

    def _get_subject_from_ollama(self, prompt_text, model="llama3.2:3b"):
        print(f"\n[SmartSave Debug] Incoming Text to Ollama:\n'''{prompt_text}'''\n")

        if not prompt_text or not prompt_text.strip():
            print("[SmartSave Debug] Prompt empty -> routing to Unsorted")
            return "Unsorted"

        url = "http://127.0.0.1:11434/api/generate"
        system_instruction = (
            "You are a strict entity extractor for file organization. "
            "Identify the PRIMARY CHARACTER, PERSON, or MAIN OBJECT in the prompt. "
            "Ignore art style, quality words, and descriptors like 'cartoon', 'realistic', 'anime', 'photo', '3d'. "
            "If a specific character or figure is present, return ONLY their proper name. "
            "Respond ONLY with the name (1-3 words). No punctuation, no quotes, no extra words."
        )

        payload = {
            "model": model,
            "prompt": f"System: {system_instruction}\nPrompt: {prompt_text}\nPrimary Character or Subject:",
            "stream": False,
            "options": {
                "temperature": 0.0
            }
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
                subject = result.get("response", "").strip()
                subject = subject.split("\n")[0].strip(" .\"'")
                folder = self._sanitize_folder(subject) if subject else "Unsorted"
                print(f"[SmartSave] Ollama classified subject as: '{folder}'")
                return folder
        except Exception as e:
            print(f"[SmartSave] Ollama request failed: {e}")
            return "Unsorted"

    def _get_next_counter(self, folder_path, prefix, ext):
        os.makedirs(folder_path, exist_ok=True)
        existing = [f for f in os.listdir(folder_path) if f.startswith(prefix) and f.endswith(ext)]
        max_idx = 0
        pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)\.{re.escape(ext)}$")
        for fname in existing:
            match = pattern.match(fname)
            if match:
                max_idx = max(max_idx, int(match.group(1)))
        return max_idx + 1

    def save_images(
        self,
        images,
        filename_prefix="ComfyUI",
        save_raw_png=True,
        save_compressed_jpg=True,
        jpg_quality=90,
        positive_prompt="",
        subfolder="",
        ollama_model="llama3.2:3b",
        prompt=None,
        extra_pnginfo=None,
        **kwargs
    ):
        manual_sub = subfolder.strip()

        if manual_sub:
            folder_name = self._sanitize_folder(manual_sub)
        else:
            target_text = positive_prompt.strip()
            if not target_text and prompt:
                for node_id, node_data in prompt.items():
                    inputs = node_data.get("inputs", {})
                    if "text" in inputs and isinstance(inputs["text"], str):
                        target_text += " " + inputs["text"]

            folder_name = self._get_subject_from_ollama(target_text, model=ollama_model)

        results = list()

        for image in images:
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            png_filename = None
            jpg_filename = None

            if save_raw_png:
                raw_dir = os.path.join(self.output_dir, "Raw", folder_name)
                counter = self._get_next_counter(raw_dir, filename_prefix, "png")
                png_filename = f"{filename_prefix}_{counter:05d}.png"
                png_path = os.path.join(raw_dir, png_filename)

                print(f"[SmartSave] Saved Raw PNG -> {png_path}")

                metadata = PngInfo()
                if prompt is not None:
                    metadata.add_text("prompt", json.dumps(prompt))
                if extra_pnginfo is not None:
                    for k, v in extra_pnginfo.items():
                        metadata.add_text(k, json.dumps(v))
                if positive_prompt:
                    metadata.add_text("user_positive_prompt", str(positive_prompt))

                img.save(png_path, pnginfo=metadata, compress_level=4)

            if save_compressed_jpg:
                comp_dir = os.path.join(self.output_dir, "Compressed", folder_name)
                counter = self._get_next_counter(comp_dir, filename_prefix, "jpg")
                jpg_filename = f"{filename_prefix}_{counter:05d}.jpg"
                jpg_path = os.path.join(comp_dir, jpg_filename)

                print(f"[SmartSave] Saved Compressed JPG -> {jpg_path}")

                rgb_img = img.convert("RGB") if img.mode != "RGB" else img
                rgb_img.save(jpg_path, "JPEG", quality=jpg_quality, optimize=True)

            primary_filename = png_filename if save_raw_png else (jpg_filename or f"{filename_prefix}.jpg")
            primary_root = "Raw" if save_raw_png else "Compressed"

            results.append({
                "filename": primary_filename,
                "subfolder": os.path.join(primary_root, folder_name),
                "type": self.type
            })

        return {"ui": {"images": results}}