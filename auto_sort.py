import os
import re
import json
import shutil
import time
import argparse
from PIL import Image
import piexif
import ollama

PROMPT_CACHE = {}
ALIASES = {}

def load_aliases(script_dir):
    """Loads aliases.json if present in the script directory or parent directory."""
    global ALIASES
    possible_paths = [
        os.path.join(script_dir, "aliases.json"),
        os.path.join(script_dir, "..", "aliases.json"),
    ]
    for path in possible_paths:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Normalize keys for case-insensitive lookup
                    ALIASES = {k.strip().lower().replace(" ", "_"): v.strip() for k, v in data.items()}
                print(f"Loaded {len(ALIASES)} alias mappings from {os.path.abspath(path)}")
                return
            except Exception as e:
                print(f"Warning: Failed to parse {path}: {e}")

def normalize_entity_name(raw_name: str) -> str:
    """Normalizes an extracted subject name to strict FirstName_LastName format."""
    cleaned = raw_name.strip()
    lookup_key = cleaned.lower().replace(" ", "_")
    
    # 1. Alias lookup
    if lookup_key in ALIASES:
        return ALIASES[lookup_key]

    # 2. Strip non-alphanumeric characters (keep hyphens and spaces)
    safe = re.sub(r'[^\w\s-]', '', cleaned)
    
    # 3. Format as Title_Case with underscores
    parts = safe.split()
    if not parts:
        return "Misc"
    return "_".join(part.capitalize() for part in parts)

def extract_prompt_from_image(filepath):
    """Pulls human positive prompt text from PNG chunks or EXIF user comments."""
    try:
        with Image.open(filepath) as img:
            ext = os.path.splitext(filepath)[1].lower()
            if ext == ".png":
                # Check for explicit positive prompt first (saved by SmartSaveImage)
                if "user_positive_prompt" in img.info:
                    val = img.info["user_positive_prompt"].strip()
                    if len(val) > 2:
                        return val

                for key in ["prompt", "parameters", "workflow"]:
                    if key in img.info:
                        raw = img.info[key]
                        data = json.loads(raw) if isinstance(raw, str) else raw
                        if isinstance(data, dict):
                            nodes = data.values() if "nodes" not in data else data["nodes"]
                            texts = []
                            for node in nodes:
                                if not isinstance(node, dict):
                                    continue
                                title = str(node.get("_meta", {}).get("title", "")).lower()
                                c_type = str(node.get("class_type", "")).lower()
                                if "neg" in title or "neg" in c_type:
                                    continue

                                inp = node.get("inputs", {}) if "inputs" in node else node.get("widgets_values", [])
                                if isinstance(inp, dict):
                                    for val in inp.values():
                                        if isinstance(val, str) and len(val.strip()) > 4 and not val.endswith(('.safetensors', '.ckpt', '.pt')):
                                            texts.append(val.strip())
                                elif isinstance(inp, list):
                                    for val in inp:
                                        if isinstance(val, str) and len(val.strip()) > 4 and not val.endswith(('.safetensors', '.ckpt', '.pt')):
                                            texts.append(val.strip())
                            if texts:
                                filtered = [c for c in texts if "mannequin" not in c.lower()]
                                pool = filtered if filtered else texts
                                return max(pool, key=len)
                        elif isinstance(raw, str):
                            return raw
            elif ext in [".jpg", ".jpeg"]:
                if "exif" in img.info:
                    exif_data = piexif.load(img.info["exif"])
                    user_comment = exif_data.get("Exif", {}).get(piexif.ExifIFD.UserComment)
                    if user_comment:
                        return user_comment[8:].decode("utf-16le", errors="ignore") if user_comment.startswith(b"UNICODE\x00") else user_comment.decode("utf-8", errors="ignore")
    except Exception:
        pass
    return ""

def query_ollama(prompt_text, model="llama3.2:3b"):
    if not prompt_text or prompt_text.strip() == "":
        return "Misc"

    short_prompt = prompt_text[:300].strip()
    if short_prompt in PROMPT_CACHE:
        return PROMPT_CACHE[short_prompt]

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
                {"role": "user", "content": f"Extract the full name of the first primary subject from this prompt:\n{short_prompt}"}
            ],
            options={"temperature": 0.0},
            keep_alive="10m"
        )
        raw_result = response["message"]["content"].strip()
        if any(bad in raw_result.lower() for bad in ["cannot", "sorry", "assist", "content", "request", "illegal", "sexual", "primary subject"]):
            subject = "Misc"
        else:
            parts = re.split(r"\s+(?:and|&|with)\s+|,|\+", raw_result, flags=re.IGNORECASE)
            cleaned = parts[0].strip() if parts else raw_result
            subject = normalize_entity_name(cleaned)
    except Exception:
        subject = "Misc"

    PROMPT_CACHE[short_prompt] = subject
    return subject

def determine_subject(filename, filepath, generic_prefixes, model):
    base = os.path.splitext(filename)[0]
    base_clean = re.sub(r'(_\d+)+_?.*$', '', base).replace("_", " ").strip()
    norm = base_clean.lower()

    if not any(norm.startswith(p) for p in generic_prefixes) and len(base_clean) > 2:
        return normalize_entity_name(base_clean)

    prompt = extract_prompt_from_image(filepath)
    if prompt:
        return query_ollama(prompt, model=model)

    return "Misc"

def get_next_indexed_path(dest_folder, subject, ext):
    """Finds the next sequential <Subject>_00001.ext index in the destination folder."""
    os.makedirs(dest_folder, exist_ok=True)
    existing_files = os.listdir(dest_folder)
    highest_idx = 0
    pattern = re.compile(rf"^{re.escape(subject)}_(\d+){re.escape(ext)}$", re.IGNORECASE)
    
    for f in existing_files:
        match = pattern.match(f)
        if match:
            idx = int(match.group(1))
            if idx > highest_idx:
                highest_idx = idx
                
    next_idx = highest_idx + 1
    new_filename = f"{subject}_{next_idx:05d}{ext}"
    return os.path.join(dest_folder, new_filename)

def safe_move(src, dst):
    for _ in range(5):
        try:
            shutil.move(src, dst)
            return True
        except PermissionError:
            time.sleep(0.2)
    return False

def prune_empty_dirs(target_dir):
    """Removes empty directories bottom-up in a platform-independent way."""
    for root, dirs, files in os.walk(target_dir, topdown=False):
        for d in dirs:
            dir_path = os.path.join(root, d)
            try:
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
            except OSError:
                pass

def sort_directory(target_dir, model="llama3.2:3b", purge_empty=True):
    raw_dir = os.path.join(target_dir, "Raw")
    compressed_dir = os.path.join(target_dir, "Compressed")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(compressed_dir, exist_ok=True)

    generic_prefixes = ["dai", "comfyui", "image", "raw", "compressed", "misc", "miscellaneous", "temp", "upscale"]
    valid_exts = (".png", ".jpg", ".jpeg", ".webp")

    all_files = []
    for root, _, files in os.walk(target_dir):
        for f in files:
            if f.lower().endswith(valid_exts):
                all_files.append(os.path.join(root, f))

    print(f"Scanning and sorting {len(all_files)} images...")

    for filepath in all_files:
        filename = os.path.basename(filepath)
        ext = os.path.splitext(filename)[1].lower()
        dest_root = raw_dir if ext == ".png" else compressed_dir

        subject = determine_subject(filename, filepath, generic_prefixes, model)
        dest_folder = os.path.join(dest_root, subject)
        
        # Determine strict sequential path (<Subject>_00001.ext)
        dest_path = get_next_indexed_path(dest_folder, subject, ext)

        if os.path.abspath(filepath) == os.path.abspath(dest_path):
            continue

        if safe_move(filepath, dest_path):
            rel_dst = os.path.relpath(dest_path, target_dir)
            print(f"Sorted: {filename} -> {rel_dst}")

    if purge_empty:
        for parent in [raw_dir, compressed_dir, target_dir]:
            if os.path.exists(parent):
                prune_empty_dirs(parent)

    print("Sorting complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ComfyUI Auto-Sorter")
    parser.add_argument("--dir", type=str, default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "output")), help="Path to output directory")
    parser.add_argument("--model", type=str, default="llama3.2:3b", help="Ollama model tag")
    parser.add_argument("--no-purge", action="store_true", help="Skip purging empty directories")
    args = parser.parse_args()

    script_directory = os.path.dirname(os.path.abspath(__file__))
    load_aliases(script_directory)

    sort_directory(args.dir, model=args.model, purge_empty=not args.no_purge)