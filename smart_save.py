import os
import json
import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import folder_paths

class SmartSaveImage:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "images": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "ComfyUI"}),
                "subfolder": ("STRING", {"default": ""}),
                "save_raw_png": ("BOOLEAN", {"default": True}),
                "save_compressed_webp": ("BOOLEAN", {"default": True}),
                "webp_quality": ("INT", {"default": 90, "min": 1, "max": 100, "step": 1}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ()
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    CATEGORY = "image/saving"

    def save_images(self, images, filename_prefix="ComfyUI", subfolder="", save_raw_png=True, save_compressed_webp=True, webp_quality=90, prompt=None, extra_pnginfo=None):
        raw_base = os.path.join(self.output_dir, "Raw", subfolder) if subfolder else os.path.join(self.output_dir, "Raw")
        comp_base = os.path.join(self.output_dir, "Compressed", subfolder) if subfolder else os.path.join(self.output_dir, "Compressed")

        if save_raw_png:
            os.makedirs(raw_base, exist_ok=True)
        if save_compressed_webp:
            os.makedirs(comp_base, exist_ok=True)

        results = list()
        for batch_number, image in enumerate(images):
            i = 255.0 * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            metadata = PngInfo()
            if prompt is not None:
                metadata.add_text("prompt", json.dumps(prompt))
            if extra_pnginfo is not None:
                for x in extra_pnginfo:
                    metadata.add_text(x, json.dumps(extra_pnginfo[x]))

            # Find next free sequential index
            counter = 1
            while True:
                filename = f"{filename_prefix}_{counter:05d}"
                raw_path = os.path.join(raw_base, f"{filename}.png")
                comp_path = os.path.join(comp_base, f"{filename}.webp")
                if not os.path.exists(raw_path) and not os.path.exists(comp_path):
                    break
                counter += 1

            if save_raw_png:
                img.save(raw_path, pnginfo=metadata, compress_level=4)

            if save_compressed_webp:
                img.save(comp_path, format="WEBP", quality=webp_quality, method=6)

            results.append({
                "filename": f"{filename}.png" if save_raw_png else f"{filename}.webp",
                "subfolder": subfolder,
                "type": self.type
            })

        return {"ui": {"images": results}}