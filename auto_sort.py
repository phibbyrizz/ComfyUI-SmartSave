import os
import re
import json
import shutil
import time
import stat
import subprocess
from pathlib import Path
from PIL import Image
import piexif
import ollama

# ---------------- CONFIGURATION ----------------
# Allow personal overrides via gitignored config_local.py
try:
    import config_local as cfg
    BASE_DIR = getattr(cfg, "BASE_DIR", None)
    CATEGORIES = getattr(cfg, "CATEGORIES", None)
except ImportError:
    BASE_DIR = None
    CATEGORIES = None

# Default ComfyUI output path: <ComfyUI_root>/custom_nodes/<this_repo>/../../output
DEFAULT_COMFY_OUTPUT = Path(__file__).resolve().parent.parent.parent / "output"

if not BASE_DIR:
    if DEFAULT_COMFY_OUTPUT.exists():
        BASE_DIR = DEFAULT_COMFY_OUTPUT
    else:
        BASE_DIR = Path(__file__).resolve().parent / "images"

MODEL_NAME = "llama3.2:3b"
# -----------------------------------------------

prompt_cache = {}

NON_PERSON_PATTERNS = [
    r"\bcandid\b", r"\bshot\b", r"\bsmartphone\b", r"\bthree-quarter\b",
    r"\bmedium-long\b", r"\bover-the-shoulder\b", r"\bblonde woman\b",
    r"\bbrunette woman\b", r"\bphoto\b", r"\bportrait\b", r"\bcinematic\b",
    r"\bdepth of field\b", r"\bphotorealistic\b", r"\blighting\b"
]

def sanitize_and_isolate_first_subject(raw_name: str) -> str:
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

def trace_node_text(node_id: str, graph: dict, visited=None) -> list[str]:
    if visited is None:
        visited = set()
    if node_id in visited or node_id not in graph:
        return []
    visited.add(node_id)

    node = graph[node_id]
    inputs = node.get("inputs", {})
    texts = []

    for k in ["text", "string_a", "string_b", "string", "prompt"]:
        val = inputs.get(k)
        if isinstance(val, str) and val.strip():
            if "bad anatomy" not in val.lower() and "watermark" not in val.lower():
                texts.append(val)
        elif isinstance(val, list) and len(val) == 2:
            texts.extend(trace_node_text(str(val[0]), graph, visited))

    return texts

def extract_active_prompt(data: dict) -> str:
    if not isinstance(data, dict):
        return ""

    target_nodes = [
        nid for nid, n in data.items()
        if isinstance(n, dict) and n.get("class_type") in [
            "KSampler", "KSamplerAdvanced", "FaceDetailer", "SamplerCustom"
        ]
    ]

    for nid in target_nodes:
        pos_link = data[nid].get("inputs", {}).get("positive")
        if isinstance(pos_link, list) and len(pos_link) == 2:
            found = trace_node_text(str(pos_link[0]), data)
            if found:
                return " ".join(found)

    fallback_texts = []
    for nid, node in data.items():
        if isinstance(node, dict) and "inputs" in node:
            for k in ["text", "string_a"]:
                val = node["inputs"].get(k)
                if isinstance(val, str) and len(val) > 3:
                    if "bad anatomy" not in val.lower() and "watermark" not in val.lower():
                        fallback_texts.append(val)
    return " ".join(fallback_texts)

def extract_prompt_from_metadata(file_path: Path) -> str:
    ext = file_path.suffix.lower()

    if ext == ".png":
        try:
            with Image.open(file_path) as img:
                if "prompt" in img.info:
                    return extract_active_prompt(json.loads(img.info["prompt"]))
        except Exception:
            pass

    elif ext in [".jpg", ".jpeg"]:
        try:
            with Image.open(file_path) as img:
                exif_raw = img.info.get("exif")
                if exif_raw:
                    exif_dict = piexif.load(exif_raw)
                    zeroth = exif_dict.get("0th", {})
                    raw_val = zeroth.get(271) or zeroth.get(270)
                    if raw_val:
                        text_str = raw_val.decode("utf-8", errors="ignore") if isinstance(raw_val, bytes) else str(raw_val)

                        if text_str.startswith("Prompt:"):
                            text_str = text_str[7:].strip()
                        elif text_str.startswith("Workflow:"):
                            text_str = text_str[9:].strip()

                        if text_str.startswith("{"):
                            data = json.loads(text_str)
                            return extract_active_prompt(data)
        except Exception:
            pass

    return ""

def identify_subject_with_llm(prompt_text: str) -> str:
    if not prompt_text.strip():
        return "Miscellaneous"

    if prompt_text in prompt_cache:
        return prompt_cache[prompt_text]

    system_instruction = (
        "You are an image library classifier. Identify the SINGLE primary real person, fictional character, "
        "or celebrity explicitly named in the prompt tags.\n\n"
        "Strict Rules:\n"
        "1. Output ONLY ONE individual. Never join multiple names with 'and', '&', or commas.\n"
        "2. If multiple people are named, pick strictly the first person.\n"
        "3. Ignore camera angles (candid, medium shot, wide), styles, and generic descriptors.\n"
        "4. If NO named person or established character exists, return 'Miscellaneous'.\n"
        "5. Respond strictly with JSON: {\"subject\": \"FirstName LastName\"}"
    )

    try:
        client = ollama.Client(timeout=12.0)
        response = client.chat(
            model=MODEL_NAME,
            format="json",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Tags:\n{prompt_text[:1500]}"}
            ],
            options={"temperature": 0.0}
        )
        parsed = json.loads(response["message"]["content"])
        raw_name = parsed.get("subject", "Miscellaneous")
        subject = sanitize_and_isolate_first_subject(raw_name)
    except Exception:
        subject = "Miscellaneous"

    prompt_cache[prompt_text] = subject
    return subject

def get_next_sequence_number(folder: Path, prefix: str, ext: str) -> int:
    existing_files = list(folder.glob(f"{prefix}_*{ext}"))
    if not existing_files:
        return 1
    indices = []
    for f in existing_files:
        match = re.search(rf"^{re.escape(prefix)}_(\d+){re.escape(ext)}$", f.name)
        if match:
            indices.append(int(match.group(1)))
    return max(indices) + 1 if indices else len(existing_files) + 1

def resolve_matching_folder(cat_dir: Path, target_name: str, current_folder: Path) -> Path:
    target_lower = target_name.lower()
    delimiters = r"_and_|_with_|_&_|\band\b|\bwith\b|&"

    valid_folders = [
        f for f in cat_dir.iterdir()
        if f.is_dir() and f != current_folder and not re.search(delimiters, f.name, flags=re.IGNORECASE)
    ]

    for f in valid_folders:
        if f.name.lower() == target_lower:
            return f

    for f in valid_folders:
        if f.name.lower().startswith(target_lower + " "):
            return f

    return cat_dir / target_name

def handle_remove_readonly(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass

def robust_remove_folder(folder: Path):
    try:
        shutil.rmtree(folder, onerror=handle_remove_readonly)
    except Exception:
        pass

    if folder.exists():
        try:
            subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(folder)], capture_output=True, text=True)
        except Exception:
            pass

    if not folder.exists():
        print(f"Removed empty folder: {folder.name}")
    else:
        print(f"Could not remove {folder.name} (locked by open Explorer window or program)")

def sort_new_images(target_dirs: list[Path]):
    for cat_dir in target_dirs:
        files = sorted([
            f for f in cat_dir.iterdir()
            if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg"]
        ], key=lambda x: x.stat().st_mtime)

        if not files:
            continue

        print(f"Sorting {len(files)} new files in {cat_dir.name}...")

        for file_path in files:
            try:
                prompt = extract_prompt_from_metadata(file_path)
                subject = identify_subject_with_llm(prompt)

                dest_dir = cat_dir / subject
                dest_dir.mkdir(parents=True, exist_ok=True)

                ext = file_path.suffix.lower()
                seq_num = get_next_sequence_number(dest_dir, subject, ext)
                new_filename = f"{subject}_{seq_num:04d}{ext}"
                dest_path = dest_dir / new_filename

                print(f"  {file_path.name} -> {subject}/{new_filename}")
                shutil.move(str(file_path), str(dest_path))

            except Exception as e:
                print(f"  [Error] {file_path.name}: {e}")
                continue

def clean_and_merge_combos(target_dirs: list[Path]):
    delimiters = r"_and_|_with_|_&_|\band\b|\bwith\b|&"
    folders_to_remove = set()

    for cat_dir in target_dirs:
        for subfolder in list(cat_dir.iterdir()):
            if subfolder.is_dir() and re.search(delimiters, subfolder.name, flags=re.IGNORECASE):
                primary = sanitize_and_isolate_first_subject(subfolder.name)
                dest_dir = resolve_matching_folder(cat_dir, primary, subfolder)
                dest_dir.mkdir(parents=True, exist_ok=True)

                print(f"Merging combo folder /{subfolder.name}/ -> /{dest_dir.name}/...")
                for file_path in list(subfolder.iterdir()):
                    if file_path.is_file():
                        if file_path.name.lower() in ["thumbs.db", "desktop.ini"]:
                            continue
                        ext = file_path.suffix.lower()
                        seq_num = get_next_sequence_number(dest_dir, dest_dir.name, ext)
                        new_filename = f"{dest_dir.name}_{seq_num:04d}{ext}"
                        shutil.move(str(file_path), str(dest_dir / new_filename))

                folders_to_remove.add(subfolder)

    time.sleep(0.5)

    for folder in folders_to_remove:
        if not folder.exists():
            continue
        remaining_images = [f for f in folder.iterdir() if f.suffix.lower() in [".png", ".jpg", ".jpeg"]]
        if not remaining_images:
            robust_remove_folder(folder)

def main():
    if not BASE_DIR.exists():
        print(f"Output directory not found: {BASE_DIR}")
        return

    # If the user specified CATEGORIES (e.g. in config_local), check those.
    # Otherwise, default to sorting BASE_DIR directly.
    target_dirs = []
    if CATEGORIES:
        target_dirs = [BASE_DIR / cat for cat in CATEGORIES if (BASE_DIR / cat).exists()]

    if not target_dirs:
        # Also auto-detect Raw/Compressed if present, otherwise just use BASE_DIR
        detected_categories = [BASE_DIR / "Raw", BASE_DIR / "Compressed"]
        target_dirs = [d for d in detected_categories if d.exists()]
        if not target_dirs:
            target_dirs = [BASE_DIR]

    sort_new_images(target_dirs)
    clean_and_merge_combos(target_dirs)

    print("Auto-sort sweep complete.")

if __name__ == "__main__":
    main()