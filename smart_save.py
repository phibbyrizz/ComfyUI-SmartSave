import os
import re
import json
import wave
import tempfile
import urllib.request
import subprocess
import shutil

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import piexif
import folder_paths


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ALIAS_FILE = os.path.join(SCRIPT_DIR, "aliases.json")

NAME_ALIASES = {}
if os.path.exists(ALIAS_FILE):
    try:
        with open(ALIAS_FILE, "r", encoding="utf-8") as f:
            loaded_aliases = json.load(f)
            if isinstance(loaded_aliases, dict):
                NAME_ALIASES = loaded_aliases
            else:
                print("[SmartSave] Note: aliases.json must contain a JSON object. Ignoring it.")
    except Exception as e:
        print(f"[SmartSave] Note: Failed reading aliases.json: {e}")


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

MODEL_FILE_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".vae")


GENERIC_SINGLE_WORDS = {
    "a", "an", "and", "the", "this", "that", "with", "without",
    "image", "photo", "portrait", "video", "shot", "scene", "frame",
    "camera", "lens", "closeup", "cinematic", "style", "quality",
    "raw", "comfyui", "unsorted", "temp", "general", "detailed",
    "natural", "silence", "background", "foreground", "soundscape",
    "music", "california", "beach", "ocean", "shoreline", "sunset",
    "golden", "hour", "summer", "winter", "spring", "autumn", "fall",
}

SINGLE_WORD_SUBJECT_ACTIONS = (
    "walking", "walks", "standing", "stands", "sitting", "sits",
    "posing", "poses", "looking", "looks", "smiling", "smiles",
    "running", "runs", "turning", "turns", "dancing", "dances",
    "lying", "kneeling", "holding", "wearing", "playfully",
    "casually", "confidently", "joyfully",
)


def _basic_sanitize_name(name):
    clean = re.sub(r'[\\/*?:"<>|]', "", str(name)).strip()
    clean = clean.replace(" ", "_")
    clean = re.sub(r"_+", "_", clean)
    clean = clean.strip("._ ")
    clean = clean.rstrip(". ")

    reserved = {
        "con", "prn", "aux", "nul",
        "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
        "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
    }
    if clean.lower() in reserved:
        clean = f"_{clean}"

    return clean


def sanitize_folder_name(name):
    clean = _basic_sanitize_name(name)
    if not clean:
        return "Unsorted"

    alias = NAME_ALIASES.get(clean.lower())
    if alias is not None:
        clean = _basic_sanitize_name(alias)

    return clean if clean else "Unsorted"


def is_refusal_or_junk(text):
    if not text:
        return True
    if len(text) > 80:
        return True
    low = text.lower()
    return any(trig in low for trig in REFUSAL_TRIGGERS)


def extract_prompt_text(positive_prompt="", prompt=None):
    target_text = (positive_prompt or "").strip()
    if target_text or not prompt:
        return target_text

    for node_data in prompt.values():
        if not isinstance(node_data, dict):
            continue

        inputs = node_data.get("inputs", {})
        c_type = str(node_data.get("class_type", "")).lower()
        title = str(node_data.get("_meta", {}).get("title", "")).lower()

        if "neg" in c_type or "neg" in title:
            continue
        if not isinstance(inputs, dict):
            continue

        for val in inputs.values():
            if not isinstance(val, str):
                continue

            candidate = val.strip()
            if len(candidate) <= 3:
                continue
            if candidate.lower().endswith(MODEL_FILE_EXTENSIONS):
                continue

            if len(candidate) > len(target_text):
                target_text = candidate

    return target_text



def _is_valid_local_candidate(name):
    cleaned = sanitize_folder_name(name)
    low = cleaned.lower()

    if not cleaned or cleaned == "Unsorted":
        return False
    if low in BLACKLIST_WORDS or low in GENERIC_SINGLE_WORDS:
        return False
    if cleaned.isdigit():
        return False

    return True


def extract_local_subject(prompt_text):
    """
    Conservative local fallback used when Ollama cannot identify the subject.

    Priority:
    1. User aliases explicitly present in the prompt.
    2. Multi-word capitalized names.
    3. Single-word names/handles in strong person-action context.
    4. Repeated single-word capitalized names/handles.
    """
    if not prompt_text:
        return None

    # User-configured aliases are the strongest local signal.
    for alias_key in sorted(NAME_ALIASES.keys(), key=len, reverse=True):
        if not alias_key:
            continue

        if re.search(
            rf"(?<!\w){re.escape(alias_key)}(?!\w)",
            prompt_text,
            re.IGNORECASE,
        ):
            resolved = sanitize_folder_name(alias_key)
            if _is_valid_local_candidate(resolved):
                return resolved

    # Multi-word proper names such as "Caitlin Clark".
    multiword_candidates = re.findall(
        r"\b([A-Z][a-zA-Z0-9_'’-]+(?:\s+[A-Z][a-zA-Z0-9_'’-]+)+)\b",
        prompt_text,
    )

    for name in multiword_candidates:
        if _is_valid_local_candidate(name):
            return sanitize_folder_name(name)

    # Single-word identity followed by strong human-action context,
    # e.g. "Soupytime walking..." or "Soupytime, with her...".
    actions = "|".join(
        re.escape(action)
        for action in SINGLE_WORD_SUBJECT_ACTIONS
    )

    context_patterns = [
        rf"\b([A-Z][A-Za-z0-9_'’-]{{2,}})\s*,\s*(?:with|who|{actions})\b",
        rf"\b([A-Z][A-Za-z0-9_'’-]{{2,}})\s+(?:{actions})\b",
        r"\b(?:featuring|starring)\s+([A-Z][A-Za-z0-9_'’-]{2,})\b",
        r"\b(?:portrait|photo|image|video)\s+of\s+([A-Z][A-Za-z0-9_'’-]{2,})\b",
    ]

    for pattern in context_patterns:
        match = re.search(
            pattern,
            prompt_text,
        )

        if match:
            candidate = match.group(1)

            exact = re.search(
                rf"(?<!\w)({re.escape(candidate)})(?!\w)",
                prompt_text,
                re.IGNORECASE,
            )
            if exact:
                candidate = exact.group(1)

            if _is_valid_local_candidate(candidate):
                return sanitize_folder_name(candidate)

    # Repeated single-word title-case handles/names are also a useful signal.
    tokens = re.findall(
        r"\b([A-Z][A-Za-z0-9_'’-]{2,})\b",
        prompt_text,
    )

    counts = {}
    order = []

    for token in tokens:
        low = token.lower()
        if low not in counts:
            counts[low] = 0
            order.append(token)
        counts[low] += 1

    for token in order:
        if (
            counts[token.lower()] >= 2
            and _is_valid_local_candidate(token)
        ):
            return sanitize_folder_name(token)

    return None

def get_subject_from_ollama(prompt_text, model="llama3.2:3b"):
    if not prompt_text or not prompt_text.strip():
        return "Unsorted"

    # Fast direct match for patterns such as "Margot Robbie as Harley Quinn".
    actor_as_char = re.search(
        r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)+)\s+as\s+",
        prompt_text,
    )
    if actor_as_char:
        return sanitize_folder_name(actor_as_char.group(1))

    url = "http://127.0.0.1:11434/api/generate"

    instruction = (
        "Task: Identify the primary named person, actor, creator, fictional character, "
        "username, handle, or LoRA trigger in this image/video description.\n"
        "Rules:\n"
        "1. Return ONLY the subject name or handle, with no explanation.\n"
        "2. The identity may be one word or multiple words.\n"
        "3. The identity may appear anywhere in a long prompt, not only near the beginning.\n"
        "4. Do NOT require the subject to be famous or known to you. If the prompt clearly "
        "uses a token as the person's identity, return that token exactly.\n"
        "5. Ignore locations, camera terms, clothing, styles, actions, and descriptive words.\n"
        "6. Return 'Unsorted' only when the prompt contains no identifiable named subject."
    )

    payload = {
        "model": model,
        "prompt": f'{instruction}\n\nPrompt: "{prompt_text}"\nPrimary Name:',
        "stream": False,
        "options": {"temperature": 0.0},
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(
                response.read().decode("utf-8")
            )
            subject = result.get(
                "response",
                "",
            ).strip()

        if (
            is_refusal_or_junk(subject)
            or subject.lower() == "unsorted"
        ):
            local_subject = extract_local_subject(
                prompt_text
            )

            if local_subject:
                print(
                    "[SmartSave] Local fallback "
                    f"successfully extracted: {local_subject}"
                )
                return local_subject

            return "Unsorted"

        subject = subject.split("\n")[0].strip(
            " .\"'"
        )

        for prefix in [
            "the primary name is",
            "the name is",
            "name:",
            "the person is",
            "the actor is",
            "the character is",
            "the creator is",
            "the handle is",
            "the username is",
        ]:
            if subject.lower().startswith(prefix):
                subject = subject[
                    len(prefix):
                ].strip(" :.\"")

        cleaned = sanitize_folder_name(
            subject
        )

        if (
            cleaned.lower() in BLACKLIST_WORDS
            or cleaned.lower() in GENERIC_SINGLE_WORDS
            or cleaned.lower() == "unsorted"
        ):
            local_subject = extract_local_subject(
                prompt_text
            )
            return (
                local_subject
                if local_subject
                else "Unsorted"
            )

        return cleaned

    except Exception as e:
        print(
            "[SmartSave] Ollama request "
            f"failed: {e}"
        )

        local_subject = extract_local_subject(
            prompt_text
        )

        return (
            local_subject
            if local_subject
            else "Unsorted"
        )

def get_next_counter(folder_path, prefix, ext):
    os.makedirs(folder_path, exist_ok=True)

    ext = ext.lower().lstrip(".")
    existing = [
        f for f in os.listdir(folder_path)
        if f.lower().endswith(f".{ext}")
    ]

    max_idx = 0
    pattern = re.compile(
        rf"^{re.escape(prefix)}_(\d+)(?:_.*)?\.{re.escape(ext)}$",
        re.IGNORECASE,
    )

    for fname in existing:
        match = pattern.match(fname)
        if match:
            max_idx = max(max_idx, int(match.group(1)))

    return max_idx + 1


def _build_png_metadata(prompt=None, extra_pnginfo=None, positive_prompt=""):
    metadata = PngInfo()

    if prompt is not None:
        metadata.add_text("prompt", json.dumps(prompt))

    if extra_pnginfo is not None:
        for key, value in extra_pnginfo.items():
            metadata.add_text(key, json.dumps(value))

    if positive_prompt:
        metadata.add_text("user_positive_prompt", str(positive_prompt))

    return metadata


# ==========================================
# 1. SMART SAVE IMAGE
# ==========================================
class SmartSaveImage:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "auto"}),
                "save_raw_png": ("BOOLEAN", {"default": True}),
                "save_compressed_jpg": ("BOOLEAN", {"default": True}),
                "jpg_quality": (
                    "INT",
                    {"default": 90, "min": 1, "max": 100, "step": 1},
                ),
            },
            "optional": {
                "positive_prompt": (
                    "STRING",
                    {"forceInput": True, "multiline": True, "default": ""},
                ),
                "subfolder": ("STRING", {"default": ""}),
                "ollama_model": (
                    "STRING",
                    {"default": "llama3.2:3b"},
                ),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
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
        **kwargs,
    ):
        if not save_raw_png and not save_compressed_jpg:
            print(
                "[SmartSave] Image save skipped: "
                "both output formats are disabled."
            )
            return {"ui": {"images": []}}

        target_text = extract_prompt_text(
            positive_prompt=positive_prompt,
            prompt=prompt,
        )


        manual_sub = (subfolder or "").strip()

        if manual_sub:
            folder_name = sanitize_folder_name(manual_sub)
        else:
            folder_name = get_subject_from_ollama(
                target_text,
                model=ollama_model,
            )

        if (
            not folder_name
            or is_refusal_or_junk(folder_name)
            or folder_name.isdigit()
        ):
            folder_name = "Unsorted"


        prefix_value = str(filename_prefix or "").strip()

        if prefix_value.lower() in {"auto", "comfyui", ""}:
            effective_prefix = folder_name
        else:
            effective_prefix = sanitize_folder_name(prefix_value)

        results = []

        raw_dir = os.path.join(
            self.output_dir,
            "Raw",
            folder_name,
        )
        comp_dir = os.path.join(
            self.output_dir,
            "Compressed",
            folder_name,
        )

        for image in images:
            image_np = 255.0 * image.cpu().numpy()
            img = Image.fromarray(
                np.clip(image_np, 0, 255).astype(np.uint8)
            )

            counter = max(
                get_next_counter(
                    raw_dir,
                    effective_prefix,
                    "png",
                ),
                get_next_counter(
                    comp_dir,
                    effective_prefix,
                    "jpg",
                ),
            )

            png_filename = (
                f"{effective_prefix}_{counter:05d}.png"
            )
            jpg_filename = (
                f"{effective_prefix}_{counter:05d}.jpg"
            )

            if save_raw_png:
                os.makedirs(raw_dir, exist_ok=True)

                png_path = os.path.join(
                    raw_dir,
                    png_filename,
                )

                metadata = _build_png_metadata(
                    prompt=prompt,
                    extra_pnginfo=extra_pnginfo,
                    positive_prompt=positive_prompt,
                )

                img.save(
                    png_path,
                    pnginfo=metadata,
                    compress_level=4,
                )

                print(
                    f"[SmartSave] Saved Raw PNG -> "
                    f"{png_path}"
                )

            if save_compressed_jpg:
                os.makedirs(comp_dir, exist_ok=True)

                jpg_path = os.path.join(
                    comp_dir,
                    jpg_filename,
                )

                rgb_img = (
                    img.convert("RGB")
                    if img.mode != "RGB"
                    else img
                )

                comment_content = (
                    positive_prompt.strip()
                    if positive_prompt
                    else target_text
                )

                exif_dict = {"Exif": {}}

                if comment_content:
                    user_comment_bytes = (
                        b"UNICODE\x00"
                        + comment_content.encode("utf-16le")
                    )

                    exif_dict["Exif"][
                        piexif.ExifIFD.UserComment
                    ] = user_comment_bytes

                try:
                    exif_bytes = piexif.dump(exif_dict)

                    rgb_img.save(
                        jpg_path,
                        "JPEG",
                        quality=jpg_quality,
                        optimize=True,
                        exif=exif_bytes,
                    )

                except Exception as ex:
                    print(
                        "[SmartSave] Note: Could not embed "
                        f"JPEG EXIF metadata: {ex}"
                    )

                    rgb_img.save(
                        jpg_path,
                        "JPEG",
                        quality=jpg_quality,
                        optimize=True,
                    )

                print(
                    f"[SmartSave] Saved Compressed JPG -> "
                    f"{jpg_path}"
                )

            if save_raw_png:
                primary_filename = png_filename
                primary_root = "Raw"
            else:
                primary_filename = jpg_filename
                primary_root = "Compressed"

            results.append(
                {
                    "filename": primary_filename,
                    "subfolder": os.path.join(
                        primary_root,
                        folder_name,
                    ),
                    "type": self.type,
                }
            )

        return {
            "ui": {
                "images": results
            }
        }


# ==========================================
# 2. SMART SAVE VIDEO
# ==========================================
class SmartSaveVideo:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "filename_prefix": (
                    "STRING",
                    {"default": "auto"},
                ),
                "frame_rate": (
                    "INT",
                    {
                        "default": 24,
                        "min": 1,
                        "max": 120,
                        "step": 1,
                    },
                ),
                "save_raw_mp4": (
                    "BOOLEAN",
                    {"default": True},
                ),
                "save_webm": (
                    "BOOLEAN",
                    {"default": True},
                ),
                "save_metadata_png": (
                    "BOOLEAN",
                    {"default": True},
                ),
                "webm_crf": (
                    "INT",
                    {
                        "default": 32,
                        "min": 0,
                        "max": 63,
                        "step": 1,
                    },
                ),
            },
            "optional": {
                "audio": ("AUDIO",),
                "positive_prompt": (
                    "STRING",
                    {
                        "forceInput": True,
                        "multiline": True,
                        "default": "",
                    },
                ),
                "subfolder": (
                    "STRING",
                    {"default": ""},
                ),
                "ollama_model": (
                    "STRING",
                    {"default": "llama3.2:3b"},
                ),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    RETURN_TYPES = ()
    FUNCTION = "save_video"
    OUTPUT_NODE = True
    CATEGORY = "video/saving"

    def _find_ffmpeg(self):
        try:
            import imageio_ffmpeg

            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

            if (
                ffmpeg_path
                and os.path.isfile(ffmpeg_path)
            ):
                return ffmpeg_path

        except Exception:
            pass

        ffmpeg_cmd = shutil.which("ffmpeg")

        if ffmpeg_cmd:
            return ffmpeg_cmd

        custom_nodes_path = os.path.dirname(
            SCRIPT_DIR
        )
        comfy_root = os.path.dirname(
            custom_nodes_path
        )
        base_dir = os.path.dirname(
            comfy_root
        )

        candidates = [
            os.path.join(
                base_dir,
                "ffmpeg",
                "bin",
                "ffmpeg.exe",
            ),
            os.path.join(
                base_dir,
                "ffmpeg.exe",
            ),
            os.path.join(
                comfy_root,
                "ffmpeg.exe",
            ),
        ]

        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate

        return None

    def _export_temp_audio(
        self,
        audio_dict,
        temp_dir,
    ):
        if not audio_dict:
            return None

        try:
            waveform = audio_dict.get(
                "waveform"
            )
            sample_rate = int(
                audio_dict.get(
                    "sample_rate",
                    44100,
                )
            )

            if waveform is None:
                return None

            if waveform.dim() == 3:
                waveform = waveform.squeeze(0)

            waveform_np = (
                waveform
                .cpu()
                .float()
                .numpy()
            )

            waveform_np = np.clip(
                waveform_np,
                -1.0,
                1.0,
            )

            int16_data = (
                waveform_np * 32767.0
            ).astype(np.int16)

            if int16_data.ndim == 2:
                num_channels = (
                    int16_data.shape[0]
                )
                interleaved = (
                    int16_data
                    .T
                    .flatten()
                    .tobytes()
                )
            else:
                num_channels = 1
                interleaved = (
                    int16_data.tobytes()
                )

            temp_wav = os.path.join(
                temp_dir,
                "temp_audio.wav",
            )

            with wave.open(
                temp_wav,
                "wb",
            ) as wf:
                wf.setnchannels(
                    num_channels
                )
                wf.setsampwidth(2)
                wf.setframerate(
                    sample_rate
                )
                wf.writeframes(
                    interleaved
                )

            return temp_wav

        except Exception as e:
            print(
                "[SmartSave] Failed "
                f"processing audio: {e}"
            )
            return None

    def _run_ffmpeg(
        self,
        command,
        raw_bytes,
        output_path,
        label,
    ):
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            _, stderr = process.communicate(
                input=raw_bytes
            )

        except FileNotFoundError as exc:
            raise RuntimeError(
                "SmartSave could not launch FFmpeg. "
                "Install FFmpeg or install the "
                "imageio-ffmpeg Python package, "
                "then restart ComfyUI."
            ) from exc

        except Exception:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            raise

        if process.returncode != 0:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass

            error_text = stderr.decode(
                "utf-8",
                errors="ignore",
            ).strip()

            print(
                f"[SmartSave] FFmpeg "
                f"{label} Error: "
                f"{error_text}"
            )

            return False

        return True

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
        **kwargs,
    ):
        if (
            not save_raw_mp4
            and not save_webm
            and not save_metadata_png
        ):
            print(
                "[SmartSave] Video save skipped: "
                "all output formats are disabled."
            )
            return ()

        target_text = extract_prompt_text(
            positive_prompt=positive_prompt,
            prompt=prompt,
        )


        manual_sub = (subfolder or "").strip()

        if manual_sub:
            folder_name = sanitize_folder_name(
                manual_sub
            )
        else:
            folder_name = get_subject_from_ollama(
                target_text,
                model=ollama_model,
            )

        if (
            not folder_name
            or is_refusal_or_junk(folder_name)
            or folder_name.isdigit()
        ):
            folder_name = "Unsorted"


        prefix_value = str(
            filename_prefix or ""
        ).strip()

        if prefix_value.lower() in {
            "auto",
            "comfyui",
            "",
        }:
            effective_prefix = folder_name
        else:
            effective_prefix = (
                sanitize_folder_name(
                    prefix_value
                )
            )

        raw_video_dir = os.path.join(
            self.output_dir,
            "Raw Video",
            folder_name,
        )

        webm_dir = os.path.join(
            self.output_dir,
            "Webm",
            folder_name,
        )

        counter = max(
            get_next_counter(
                raw_video_dir,
                effective_prefix,
                "mp4",
            ),
            get_next_counter(
                webm_dir,
                effective_prefix,
                "webm",
            ),
            get_next_counter(
                raw_video_dir,
                effective_prefix,
                "png",
            ),
        )

        base_filename = (
            f"{effective_prefix}_"
            f"{counter:05d}"
        )

        mp4_filename = (
            f"{base_filename}.mp4"
        )
        webm_filename = (
            f"{base_filename}.webm"
        )
        png_filename = (
            f"{base_filename}_workflow.png"
        )

        frames_np = (
            255.0
            * images.cpu().numpy()
        ).clip(
            0,
            255,
        ).astype(
            np.uint8
        )

        if len(frames_np) == 0:
            raise ValueError(
                "SmartSave Video received "
                "an empty image batch."
            )

        _, height, width, _ = (
            frames_np.shape
        )

        raw_bytes = frames_np.tobytes()

        ffmpeg = None

        if save_raw_mp4 or save_webm:
            ffmpeg = self._find_ffmpeg()

            if not ffmpeg:
                raise RuntimeError(
                    "SmartSave could not find FFmpeg. "
                    "Install FFmpeg or install the "
                    "imageio-ffmpeg Python package, "
                    "then restart ComfyUI."
                )

            print(
                "[SmartSave] Using FFmpeg "
                f"binary: {ffmpeg}"
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_audio_file = None

            if audio is not None:
                temp_audio_file = (
                    self._export_temp_audio(
                        audio,
                        tmp_dir,
                    )
                )

            if save_raw_mp4:
                os.makedirs(
                    raw_video_dir,
                    exist_ok=True,
                )

                mp4_path = os.path.join(
                    raw_video_dir,
                    mp4_filename,
                )

                cmd_mp4 = [
                    ffmpeg,
                    "-y",
                    "-f", "rawvideo",
                    "-vcodec", "rawvideo",
                    "-s", f"{width}x{height}",
                    "-pix_fmt", "rgb24",
                    "-r", str(frame_rate),
                    "-i", "-",
                ]

                if (
                    temp_audio_file
                    and os.path.exists(
                        temp_audio_file
                    )
                ):
                    cmd_mp4.extend(
                        [
                            "-i",
                            temp_audio_file,
                            "-c:a",
                            "aac",
                            "-b:a",
                            "192k",
                            "-shortest",
                        ]
                    )

                cmd_mp4.extend(
                    [
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        "-crf",
                        "17",
                        "-preset",
                        "medium",
                        mp4_path,
                    ]
                )

                if self._run_ffmpeg(
                    cmd_mp4,
                    raw_bytes,
                    mp4_path,
                    "MP4",
                ):
                    print(
                        "[SmartSave] Saved Raw "
                        f"Video MP4 -> {mp4_path}"
                    )

            if save_webm:
                os.makedirs(
                    webm_dir,
                    exist_ok=True,
                )

                webm_path = os.path.join(
                    webm_dir,
                    webm_filename,
                )

                cmd_webm = [
                    ffmpeg,
                    "-y",
                    "-f", "rawvideo",
                    "-vcodec", "rawvideo",
                    "-s", f"{width}x{height}",
                    "-pix_fmt", "rgb24",
                    "-r", str(frame_rate),
                    "-i", "-",
                ]

                if (
                    temp_audio_file
                    and os.path.exists(
                        temp_audio_file
                    )
                ):
                    cmd_webm.extend(
                        [
                            "-i",
                            temp_audio_file,
                            "-c:a",
                            "libopus",
                            "-b:a",
                            "128k",
                            "-shortest",
                        ]
                    )

                cmd_webm.extend(
                    [
                        "-c:v",
                        "libvpx-vp9",
                        "-crf",
                        str(webm_crf),
                        "-b:v",
                        "0",
                        "-pix_fmt",
                        "yuv420p",
                        webm_path,
                    ]
                )

                if self._run_ffmpeg(
                    cmd_webm,
                    raw_bytes,
                    webm_path,
                    "WebM",
                ):
                    print(
                        "[SmartSave] Saved "
                        "Compressed WebM -> "
                        f"{webm_path}"
                    )

        if save_metadata_png:
            os.makedirs(
                raw_video_dir,
                exist_ok=True,
            )

            png_path = os.path.join(
                raw_video_dir,
                png_filename,
            )

            first_frame = Image.fromarray(
                frames_np[0]
            )

            metadata = _build_png_metadata(
                prompt=prompt,
                extra_pnginfo=extra_pnginfo,
                positive_prompt=positive_prompt,
            )

            first_frame.save(
                png_path,
                pnginfo=metadata,
                compress_level=4,
            )

            print(
                "[SmartSave] Saved Companion "
                f"Metadata PNG -> {png_path}"
            )

        return ()


NODE_CLASS_MAPPINGS = {
    "SmartSave": SmartSaveImage,
    "SmartSaveImage": SmartSaveImage,
    "SmartSaveVideo": SmartSaveVideo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SmartSave": "Smart Save (Image)",
    "SmartSaveImage": "Smart Save (Image)",
    "SmartSaveVideo": "Smart Save (Video)",
}
