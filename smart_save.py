import os
import re
import json
import wave
import tempfile
import urllib.request
import urllib.error
import subprocess
import shutil
import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import folder_paths

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ALIAS_FILE = os.path.join(SCRIPT_DIR, "aliases.json")

# Load external user aliases if available; fallback to empty dict
NAME_ALIASES = {}
if os.path.exists(ALIAS_FILE):
    try:
        with open(ALIAS_FILE, "r", encoding="utf-8") as f:
            NAME_ALIASES = json.load(f)
    except Exception as e:
        print(f"[SmartSave] Note: Failed reading aliases.json: {e}")

# Generic prompt tags to ignore as directory names
BLACKLIST_WORDS = {
    "raw", "comfyui", "image", "unsorted", "temp", "cartoon", "doll", 
    "general", "kneeup", "detailed", "hq", "moody", "upscaled", "scale", 
    "portrait", "closeup", "cinematic", "photo", "4k", "8k", "dai"
}

REFUSAL_TRIGGERS = [
    "i cannot", "i can't", "cannot fulfill", "can't fulfill", 
    "explicit content", "illegal substances", "as an ai", "policy", 
    "i am unable", "my safety"
]

def sanitize_folder_name(name):
    clean = re.sub(r'[\\/*?:"<>|]', "", str(name)).strip()
    clean = clean.replace(" ", "_")
    clean = re.sub(r'_+', '_', clean)
    clean = clean.strip("_")
    low = clean.lower()
    if low in NAME_ALIASES:
        return NAME_ALIASES[low]
    return clean if clean else "Unsorted"

def is_refusal_or_junk(text):
    if not text or len(text) > 35:
        return True
    low = text.lower()
    return any(trig in low for trig in REFUSAL_TRIGGERS)

def get_subject_from_ollama(prompt_text, model="llama3.2:3b"):
    if not prompt_text or not prompt_text.strip():
        return "Unsorted"

    url = "http://127.0.0.1:11434/api/generate"
    instruction = (
        "Task: Perform named entity recognition on this text metadata. "
        "Identify and extract ONLY the name of the real or fictional person/character mentioned. "
        "Return just the name, nothing else. If none is found, return 'Unsorted'."
    )

    payload = {
        "model": model,
        "prompt": f"{instruction}\n\nInput Metadata: \"{prompt_text}\"\nExtracted Name:",
        "stream": False,
        "options": {"temperature": 0.0}
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as response:
            result = json.loads(response.read().decode("utf-8"))
            subject = result.get("response", "").strip()
            subject = subject.split("\n")[0].strip(" .\"'")
            
            if is_refusal_or_junk(subject):
                return "Unsorted"
            
            cleaned = sanitize_folder_name(subject)
            if cleaned.lower() in BLACKLIST_WORDS:
                return "Unsorted"
            
            return cleaned
    except Exception as e:
        print(f"[SmartSave] Ollama request failed: {e}")
        return "Unsorted"

def get_next_counter(folder_path, prefix, ext):
    os.makedirs(folder_path, exist_ok=True)
    existing = [f for f in os.listdir(folder_path) if f.lower().endswith(ext)]
    max_idx = 0
    # Matches both standard prefix_00001.ext and suffixed prefix_00001_workflow.ext
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)(?:_.*)?\.{re.escape(ext)}$", re.IGNORECASE)
    for fname in existing:
        match = pattern.match(fname)
        if match:
            max_idx = max(max_idx, int(match.group(1)))
    return max_idx + 1


# ==========================================
# 1. SMART SAVE IMAGE (Raw & Compressed)
# ==========================================
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
                "filename_prefix": ("STRING", {"default": "auto"}),
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

    def save_images(
        self,
        images,
        filename_prefix="auto",
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
            folder_name = sanitize_folder_name(manual_sub)
        else:
            target_text = positive_prompt.strip()
            if not target_text and prompt:
                for node_id, node_data in prompt.items():
                    inputs = node_data.get("inputs", {})
                    if "text" in inputs and isinstance(inputs["text"], str):
                        target_text += " " + inputs["text"]

            folder_name = get_subject_from_ollama(target_text, model=ollama_model)

        if not folder_name or is_refusal_or_junk(folder_name) or folder_name.isdigit():
            folder_name = "Unsorted"

        if filename_prefix.lower() in ["auto", "comfyui", ""]:
            effective_prefix = folder_name
        else:
            effective_prefix = sanitize_folder_name(filename_prefix)

        results = list()

        for image in images:
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            raw_dir = os.path.join(self.output_dir, "Raw", folder_name)
            comp_dir = os.path.join(self.output_dir, "Compressed", folder_name)

            counter = max(
                get_next_counter(raw_dir, effective_prefix, "png"),
                get_next_counter(comp_dir, effective_prefix, "jpg")
            )

            png_filename = f"{effective_prefix}_{counter:05d}.png"
            jpg_filename = f"{effective_prefix}_{counter:05d}.jpg"

            if save_raw_png:
                os.makedirs(raw_dir, exist_ok=True)
                png_path = os.path.join(raw_dir, png_filename)
                metadata = PngInfo()
                if prompt is not None:
                    metadata.add_text("prompt", json.dumps(prompt))
                if extra_pnginfo is not None:
                    for k, v in extra_pnginfo.items():
                        metadata.add_text(k, json.dumps(v))
                if positive_prompt:
                    metadata.add_text("user_positive_prompt", str(positive_prompt))

                img.save(png_path, pnginfo=metadata, compress_level=4)
                print(f"[SmartSave] Saved Raw PNG -> {png_path}")

            if save_compressed_jpg:
                os.makedirs(comp_dir, exist_ok=True)
                jpg_path = os.path.join(comp_dir, jpg_filename)
                rgb_img = img.convert("RGB") if img.mode != "RGB" else img
                rgb_img.save(jpg_path, "JPEG", quality=jpg_quality, optimize=True)
                print(f"[SmartSave] Saved Compressed JPG -> {jpg_path}")

            primary_filename = png_filename if save_raw_png else jpg_filename
            primary_root = "Raw" if save_raw_png else "Compressed"

            results.append({
                "filename": primary_filename,
                "subfolder": os.path.join(primary_root, folder_name),
                "type": self.type
            })

        return {"ui": {"images": results}}


# ==========================================
# 2. SMART SAVE VIDEO (Raw Video, Webm, Companion PNG & Audio)
# ==========================================
class SmartSaveVideo:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "images": ("IMAGE", ),
                "filename_prefix": ("STRING", {"default": "auto"}),
                "frame_rate": ("INT", {"default": 24, "min": 1, "max": 120, "step": 1}),
                "save_raw_mp4": ("BOOLEAN", {"default": True}),
                "save_webm": ("BOOLEAN", {"default": True}),
                "save_metadata_png": ("BOOLEAN", {"default": True}),
                "webm_crf": ("INT", {"default": 32, "min": 0, "max": 63, "step": 1}),
            },
            "optional": {
                "audio": ("AUDIO", ),
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
    FUNCTION = "save_video"
    OUTPUT_NODE = True
    CATEGORY = "video/saving"

    def _find_ffmpeg(self):
        # 1. Look for imageio_ffmpeg (Bundled with ComfyUI / VideoHelperSuite)
        try:
            import imageio_ffmpeg
            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
            if ffmpeg_path and os.path.isfile(ffmpeg_path):
                return ffmpeg_path
        except Exception:
            pass

        # 2. System PATH check
        ffmpeg_cmd = shutil.which("ffmpeg")
        if ffmpeg_cmd:
            return ffmpeg_cmd

        # 3. Known folder locations
        custom_nodes_path = os.path.dirname(SCRIPT_DIR)
        comfy_root = os.path.dirname(custom_nodes_path)
        base_dir = os.path.dirname(comfy_root)

        candidates = [
            os.path.join(base_dir, "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(base_dir, "ffmpeg.exe"),
            os.path.join(comfy_root, "ffmpeg.exe"),
        ]

        for c in candidates:
            if os.path.isfile(c):
                return c

        return "ffmpeg"

    def _export_temp_audio(self, audio_dict, temp_dir):
        if not audio_dict:
            return None
        try:
            waveform = audio_dict.get("waveform")
            sample_rate = audio_dict.get("sample_rate", 44100)

            if waveform is None:
                return None

            # [Batch, Channels, Samples] -> [Channels, Samples]
            if waveform.dim() == 3:
                waveform = waveform.squeeze(0)

            waveform_np = waveform.cpu().float().numpy()
            waveform_np = np.clip(waveform_np, -1.0, 1.0)
            int16_data = (waveform_np * 32767.0).astype(np.int16)

            # Interleave channels: [Channels, Samples] -> [Samples, Channels]
            if int16_data.ndim == 2:
                num_channels = int16_data.shape[0]
                interleaved = int16_data.T.flatten().tobytes()
            else:
                num_channels = 1
                interleaved = int16_data.tobytes()

            temp_wav = os.path.join(temp_dir, "temp_audio.wav")
            with wave.open(temp_wav, "wb") as wf:
                wf.setnchannels(num_channels)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(interleaved)

            return temp_wav
        except Exception as e:
            print(f"[SmartSave] Failed processing audio: {e}")
            return None

    def save_video(
        self,
        images,
        filename_prefix="auto",
        frame_rate=24,
        save_raw_mp4=True,
        save_webm=True,
        save_metadata_png=True,
        webm_crf=32,
        audio=None,
        positive_prompt="",
        subfolder="",
        ollama_model="llama3.2:3b",
        prompt=None,
        extra_pnginfo=None,
        **kwargs
    ):
        manual_sub = subfolder.strip()

        if manual_sub:
            folder_name = sanitize_folder_name(manual_sub)
        else:
            target_text = positive_prompt.strip()
            if not target_text and prompt:
                for node_id, node_data in prompt.items():
                    inputs = node_data.get("inputs", {})
                    if "text" in inputs and isinstance(inputs["text"], str):
                        target_text += " " + inputs["text"]

            folder_name = get_subject_from_ollama(target_text, model=ollama_model)

        if not folder_name or is_refusal_or_junk(folder_name) or folder_name.isdigit():
            folder_name = "Unsorted"

        if filename_prefix.lower() in ["auto", "comfyui", ""]:
            effective_prefix = folder_name
        else:
            effective_prefix = sanitize_folder_name(filename_prefix)

        raw_video_dir = os.path.join(self.output_dir, "Raw Video", folder_name)
        webm_dir = os.path.join(self.output_dir, "Webm", folder_name)

        counter = max(
            get_next_counter(raw_video_dir, effective_prefix, "mp4"),
            get_next_counter(webm_dir, effective_prefix, "webm"),
            get_next_counter(raw_video_dir, effective_prefix, "png")
        )

        base_filename = f"{effective_prefix}_{counter:05d}"
        mp4_filename = f"{base_filename}.mp4"
        webm_filename = f"{base_filename}.webm"
        png_filename = f"{base_filename}_workflow.png"

        # Convert [B, H, W, C] PyTorch batch tensor to uint8 raw bytes
        frames_np = (255. * images.cpu().numpy()).clip(0, 255).astype(np.uint8)
        _, height, width, _ = frames_np.shape
        raw_bytes = frames_np.tobytes()

        ffmpeg = self._find_ffmpeg()
        print(f"[SmartSave] Using FFmpeg binary: {ffmpeg}")

        # Handle optional audio export to temp WAV
        temp_audio_file = None
        with tempfile.TemporaryDirectory() as tmp_dir:
            if audio is not None:
                temp_audio_file = self._export_temp_audio(audio, tmp_dir)

            # 1. Encode Raw High-Quality MP4 (H.264 + optional AAC)
            if save_raw_mp4:
                os.makedirs(raw_video_dir, exist_ok=True)
                mp4_path = os.path.join(raw_video_dir, mp4_filename)
                
                cmd_mp4 = [
                    ffmpeg, "-y",
                    "-f", "rawvideo",
                    "-vcodec", "rawvideo",
                    "-s", f"{width}x{height}",
                    "-pix_fmt", "rgb24",
                    "-r", str(frame_rate),
                    "-i", "-",
                ]

                if temp_audio_file and os.path.exists(temp_audio_file):
                    cmd_mp4.extend(["-i", temp_audio_file, "-c:a", "aac", "-b:a", "192k", "-shortest"])

                cmd_mp4.extend([
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-crf", "17",
                    "-preset", "medium",
                    mp4_path
                ])
                
                process = subprocess.Popen(cmd_mp4, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = process.communicate(input=raw_bytes)
                if process.returncode != 0:
                    print(f"[SmartSave] FFmpeg MP4 Error: {stderr.decode('utf-8', errors='ignore')}")
                else:
                    print(f"[SmartSave] Saved Raw Video MP4 -> {mp4_path}")

            # 2. Encode Compressed WebM (VP9 + optional Opus)
            if save_webm:
                os.makedirs(webm_dir, exist_ok=True)
                webm_path = os.path.join(webm_dir, webm_filename)
                
                cmd_webm = [
                    ffmpeg, "-y",
                    "-f", "rawvideo",
                    "-vcodec", "rawvideo",
                    "-s", f"{width}x{height}",
                    "-pix_fmt", "rgb24",
                    "-r", str(frame_rate),
                    "-i", "-",
                ]

                if temp_audio_file and os.path.exists(temp_audio_file):
                    cmd_webm.extend(["-i", temp_audio_file, "-c:a", "libopus", "-b:a", "128k", "-shortest"])

                cmd_webm.extend([
                    "-c:v", "libvpx-vp9",
                    "-crf", str(webm_crf),
                    "-b:v", "0",
                    "-pix_fmt", "yuv420p",
                    webm_path
                ])
                
                process = subprocess.Popen(cmd_webm, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = process.communicate(input=raw_bytes)
                if process.returncode != 0:
                    print(f"[SmartSave] FFmpeg WebM Error: {stderr.decode('utf-8', errors='ignore')}")
                else:
                    print(f"[SmartSave] Saved Compressed WebM -> {webm_path}")

        # 3. Save Companion Metadata PNG (First frame with workflow embedded)
        if save_metadata_png:
            os.makedirs(raw_video_dir, exist_ok=True)
            png_path = os.path.join(raw_video_dir, png_filename)
            
            first_frame = Image.fromarray(frames_np[0])
            metadata = PngInfo()
            if prompt is not None:
                metadata.add_text("prompt", json.dumps(prompt))
            if extra_pnginfo is not None:
                for k, v in extra_pnginfo.items():
                    metadata.add_text(k, json.dumps(v))
            if positive_prompt:
                metadata.add_text("user_positive_prompt", str(positive_prompt))

            first_frame.save(png_path, pnginfo=metadata, compress_level=4)
            print(f"[SmartSave] Saved Companion Metadata PNG -> {png_path}")

        return ()


NODE_CLASS_MAPPINGS = {
    "SmartSave": SmartSaveImage,
    "SmartSaveImage": SmartSaveImage,
    "SmartSaveVideo": SmartSaveVideo
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SmartSave": "Smart Save (Image)",
    "SmartSaveImage": "Smart Save (Image)",
    "SmartSaveVideo": "Smart Save (Video)"
}