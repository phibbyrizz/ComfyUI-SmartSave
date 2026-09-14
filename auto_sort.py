import os
import re
import json
import shutil
import time
import argparse
import traceback
from dataclasses import dataclass
from typing import Optional

from PIL import Image
import piexif
import ollama

PROMPT_CACHE = {}
ALIASES = {}

VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
CANONICAL_ROOTS = {"Raw", "Compressed", "Raw Video", "Webm"}
UNSORTED_NAMES = {"unsorted", "misc", "miscellaneous", "none"}
GENERIC_PREFIXES = [
    "dai", "comfyui", "image", "raw", "compressed", "misc", "miscellaneous",
    "temp", "upscale", "unsorted", "selfie", "auto",
]


@dataclass
class Classification:
    subject: str
    source: str
    detail: str = ""
    preferred_index: Optional[int] = None


@dataclass
class PlannedMove:
    src: str
    dst: str
    subject: str
    source: str
    detail: str
    action: str
    preferred_index: Optional[int] = None
    diagnostic: Optional[dict] = None


def load_aliases(script_dir):
    global ALIASES
    ALIASES = {}
    possible_paths = [
        os.path.join(script_dir, "aliases.json"),
        os.path.join(script_dir, "..", "aliases.json"),
    ]
    for path in possible_paths:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    ALIASES = {
                        k.strip().lower().replace(" ", "_"): v.strip()
                        for k, v in data.items()
                    }
                print(f"Loaded {len(ALIASES)} alias mappings from {os.path.abspath(path)}")
                return
            except Exception as e:
                print(f"Warning: Failed to parse {path}: {e}")


def normalize_entity_name(raw_name: str) -> str:
    cleaned = (raw_name or "").strip()
    lookup_key = cleaned.lower().replace(" ", "_")

    if lookup_key in ALIASES:
        return ALIASES[lookup_key]

    safe = re.sub(r"[^\w\s-]", "", cleaned)
    parts = safe.split()
    if not parts or lookup_key in UNSORTED_NAMES:
        return "Unsorted"
    return "_".join(part.capitalize() for part in parts)


def canonical_root_for_file(filename: str) -> Optional[str]:
    lower = filename.lower()
    ext = os.path.splitext(lower)[1]
    if lower.endswith("_workflow.png"):
        return "Raw Video"
    if ext == ".png":
        return "Raw"
    if ext in {".jpg", ".jpeg", ".webp"}:
        return "Compressed"
    if ext in {".mp4", ".mov", ".mkv"}:
        return "Raw Video"
    if ext == ".webm":
        return "Webm"
    return None


def find_output_root(filepath, fallback_root):
    current = os.path.abspath(os.path.dirname(filepath))
    while True:
        if os.path.basename(current).lower() == "output":
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return os.path.abspath(fallback_root)


def find_counterpart_by_timestamp(filepath, target_dir):
    """Find a nearby raw PNG/workflow PNG created at nearly the same time."""
    try:
        file_mtime = os.path.getmtime(filepath)
        output_root = find_output_root(filepath, target_dir)

        closest_match = None
        smallest_diff = 2.0
        for search_subdir in ["Raw", "Raw Video"]:
            search_root = os.path.join(output_root, search_subdir)
            if not os.path.exists(search_root):
                continue
            for root, _, files in os.walk(search_root):
                for name in files:
                    if not name.lower().endswith(".png"):
                        continue
                    raw_path = os.path.join(root, name)
                    if os.path.abspath(raw_path) == os.path.abspath(filepath):
                        continue
                    diff = abs(file_mtime - os.path.getmtime(raw_path))
                    if diff < smallest_diff:
                        smallest_diff = diff
                        closest_match = raw_path
        return closest_match
    except Exception:
        return None

def canonical_index_from_path(filepath):
    """Return the SmartSave generation number embedded in a canonical filename."""
    if not filepath:
        return None
    name = os.path.basename(filepath)
    match = re.match(r"^.+?_(\d+)(?:_workflow)?\.[^.]+$", name, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def nearest_counterpart_candidates(filepath, target_dir, limit=5, max_seconds=60.0):
    """Return nearby raw/workflow files for forensic reporting without moving anything."""
    try:
        file_mtime = os.path.getmtime(filepath)
    except OSError:
        return []

    output_root = find_output_root(filepath, target_dir)
    candidates = []
    for search_subdir in ["Raw", "Raw Video"]:
        search_root = os.path.join(output_root, search_subdir)
        if not os.path.isdir(search_root):
            continue
        for root, _, files in os.walk(search_root):
            for name in files:
                lower = name.lower()
                if not lower.endswith((".png", ".mp4", ".mov", ".mkv")):
                    continue
                path = os.path.join(root, name)
                if os.path.abspath(path) == os.path.abspath(filepath):
                    continue
                try:
                    diff = abs(file_mtime - os.path.getmtime(path))
                except OSError:
                    continue
                if diff <= max_seconds:
                    candidates.append({
                        "path": os.path.relpath(path, target_dir),
                        "seconds_apart": round(diff, 3),
                        "canonical_index": canonical_index_from_path(path),
                    })

    candidates.sort(key=lambda item: (item["seconds_apart"], item["path"].lower()))
    return candidates[:limit]


def unresolved_diagnostic(filepath, target_dir):
    candidates = nearest_counterpart_candidates(filepath, target_dir)
    likely = "no nearby raw/workflow counterpart found"
    if candidates:
        closest = candidates[0]
        if closest["seconds_apart"] < 2.0:
            likely = "nearby counterpart exists but did not yield usable prompt metadata"
        else:
            likely = "nearby files exist, but outside the current 2-second counterpart match window"
    return {
        "likely_failure_stage": likely,
        "nearest_candidates": candidates,
    }


def _extract_prompt_from_png_info(img):
    for target_key in ["user_positive_prompt", "positive_prompt"]:
        if target_key in img.info:
            val = str(img.info[target_key]).strip()
            if len(val) > 2:
                return val, f"PNG metadata key '{target_key}'"

    for key in ["prompt", "parameters", "workflow"]:
        if key not in img.info:
            continue
        raw = img.info[key]
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            data = raw

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
                values = inp.values() if isinstance(inp, dict) else inp if isinstance(inp, list) else []
                for val in values:
                    if not isinstance(val, str):
                        continue
                    stripped = val.strip()
                    if len(stripped) <= 3:
                        continue
                    if stripped.lower().endswith((".safetensors", ".ckpt", ".pt")):
                        continue
                    texts.append(stripped)

            if texts:
                filtered = [t for t in texts if "mannequin" not in t.lower()]
                pool = filtered if filtered else texts
                return max(pool, key=len), f"PNG embedded '{key}' graph/text"
        elif isinstance(raw, str) and raw.strip():
            return raw.strip(), f"PNG metadata key '{key}'"

    return "", ""


def extract_prompt_from_image(filepath, target_dir):
    """Return (prompt, provenance, evidence_path). Never raises for unreadable media."""
    try:
        with Image.open(filepath) as img:
            ext = os.path.splitext(filepath)[1].lower()
            if ext == ".png":
                prompt, source = _extract_prompt_from_png_info(img)
                if prompt:
                    return prompt, source, filepath

            elif ext in [".jpg", ".jpeg"] and "exif" in img.info:
                try:
                    exif_data = piexif.load(img.info["exif"])
                    user_comment = exif_data.get("Exif", {}).get(piexif.ExifIFD.UserComment)
                    if user_comment:
                        if user_comment.startswith(b"UNICODE\x00"):
                            prompt = user_comment[8:].decode("utf-16le", errors="ignore")
                        else:
                            prompt = user_comment.decode("utf-8", errors="ignore")
                        if prompt.strip():
                            return prompt.strip(), "JPEG EXIF UserComment", filepath
                except Exception:
                    pass
    except Exception:
        pass

    raw_match = find_counterpart_by_timestamp(filepath, target_dir)
    if raw_match and os.path.exists(raw_match):
        prompt, source, evidence_path = extract_prompt_from_image(raw_match, target_dir)
        if prompt:
            rel = os.path.relpath(raw_match, target_dir)
            return prompt, f"timestamp-matched counterpart ({rel}; {source})", evidence_path or raw_match

    return "", "", None

def _fallback_name_from_prompt(prompt_text):
    """
    Conservative fallback used only when metadata recovery is already required.
    It supports normal multi-word names and single-word handles/LoRA triggers
    without allowing lowercase action words to become identities.
    """
    if not prompt_text:
        return "Unsorted"

    # Explicit aliases are the strongest local evidence.
    for alias_key, alias_value in sorted(
        ALIASES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        phrase = alias_key.replace("_", " ")
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", prompt_text, re.IGNORECASE):
            return normalize_entity_name(alias_value)

    # Multi-word proper names.
    multiword = re.findall(
        r"\b([A-Z][A-Za-z0-9_'’-]+(?:\s+[A-Z][A-Za-z0-9_'’-]+)+)\b",
        prompt_text,
    )
    ignore_multiword = {
        "Pov Shot", "Shot On", "Direct On", "Golden Hour",
    }
    for candidate in multiword:
        if candidate not in ignore_multiword:
            return normalize_entity_name(candidate)

    # Single-word identity in strong person/action context.
    # Deliberately case-sensitive: this is what prevents "turned" becoming a name.
    actions = (
        "walking|walks|standing|stands|sitting|sits|posing|poses|looking|looks|"
        "smiling|smiles|running|runs|turning|turns|dancing|dances|lying|kneeling|"
        "holding|wearing|playfully|casually|confidently|joyfully|glancing|glances"
    )
    patterns = [
        rf"\b([A-Z][A-Za-z0-9_'’-]{{2,}})\s*,\s*(?:with|who|{actions})\b",
        rf"\b([A-Z][A-Za-z0-9_'’-]{{2,}})\s+(?:{actions})\b",
        r"\b(?:featuring|starring)\s+([A-Z][A-Za-z0-9_'’-]{2,})\b",
        r"\b(?:portrait|photo|image|video)\s+of\s+([A-Z][A-Za-z0-9_'’-]{2,})\b",
    ]
    ignored_single = {
        "A", "An", "The", "Pov", "POV", "Shot", "Style", "Username",
        "Image", "Photo", "Portrait", "Video", "Camera", "Natural",
        "California", "ComfyUI", "Raw", "Unsorted",
    }
    for pattern in patterns:
        match = re.search(pattern, prompt_text)
        if match:
            candidate = match.group(1)
            if candidate not in ignored_single:
                return normalize_entity_name(candidate)

    return "Unsorted"

def query_ollama(prompt_text, model="llama3.2:3b"):
    """Classify prompt text only when a file actually requires recovery."""
    if not prompt_text or not prompt_text.strip():
        return Classification("Unsorted", "no-prompt", "No usable prompt text was found")

    actor_as_char = re.search(
        r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)+)\s+as\s+",
        prompt_text,
    )
    if actor_as_char:
        return Classification(
            normalize_entity_name(actor_as_char.group(1)),
            "regex",
            "Matched '<Person> as <Character>' pattern",
        )

    cache_key = prompt_text.strip()
    if cache_key in PROMPT_CACHE:
        cached = PROMPT_CACHE[cache_key]
        return Classification(cached.subject, "cache", cached.detail)

    system_instruction = (
        "Task: Identify the primary named person, actor, creator, fictional character, "
        "username, handle, or LoRA trigger in this image/video description.\n"
        "Rules:\n"
        "1. Return ONLY the subject name or handle, with no explanation.\n"
        "2. The identity may be one word or multiple words.\n"
        "3. The identity may appear anywhere in the prompt.\n"
        "4. The identity does not need to be famous or known to you.\n"
        "5. Ignore locations, camera terms, clothing, styles, actions, node names, "
        "model filenames, and descriptive words.\n"
        "6. Return 'Unsorted' only when there is no identifiable named subject."
    )

    try:
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f'Prompt: "{prompt_text.strip()}"\nPrimary Name:'},
            ],
            options={"temperature": 0.0},
            keep_alive="10m",
        )
        raw_result = response["message"]["content"].strip()

        refused = any(bad in raw_result.lower() for bad in [
            "cannot", "sorry", "assist", "content", "request", "illegal", "sexual"
        ])

        if refused or raw_result.lower().strip(" .\"'") == "unsorted":
            subject = _fallback_name_from_prompt(prompt_text)
            source = "regex-fallback" if subject != "Unsorted" else "ollama"
            detail = (
                f"Ollama response was refused/filtered: {raw_result[:120]}"
                if refused else f"Ollama returned: {raw_result[:120]}"
            )
            result = Classification(subject, source, detail)
        else:
            cleaned_result = raw_result.split("\n")[0].strip(" .\"'")
            for prefix in [
                "the primary name is", "the name is", "name:", "the person is",
                "the actor is", "the character is", "the creator is",
                "the handle is", "the username is",
            ]:
                if cleaned_result.lower().startswith(prefix):
                    cleaned_result = cleaned_result[len(prefix):].strip(" :.\"")

            parts = re.split(
                r"\s+(?:and|&|with)\s+|,|\+",
                cleaned_result,
                flags=re.IGNORECASE,
            )
            subject = normalize_entity_name(parts[0].strip() if parts else cleaned_result)
            result = Classification(
                subject,
                "ollama",
                f"Ollama returned: {raw_result[:120]}",
            )

    except Exception as exc:
        subject = _fallback_name_from_prompt(prompt_text)
        source = "regex-fallback" if subject != "Unsorted" else "ollama-error"
        result = Classification(subject, source, f"Ollama error: {exc}")

    # Never cache an unresolved result. A later alias/configuration change or a
    # different classifier outcome in the same run must get another chance.
    if result.subject != "Unsorted":
        PROMPT_CACHE[cache_key] = result
    return result

def _filename_subject(filename):
    base = os.path.splitext(filename)[0]
    base = re.sub(r"_workflow$", "", base, flags=re.IGNORECASE)
    base_clean = re.sub(r"(_\d+)+_?.*$", "", base).replace("_", " ").strip()
    return base_clean


def determine_subject(filename, filepath, model, target_dir):
    base_clean = _filename_subject(filename)
    norm = base_clean.lower()
    lookup = norm.replace(" ", "_")
    parent_folder = os.path.basename(os.path.dirname(filepath)).lower()
    in_unsorted_bucket = parent_folder in {"unsorted", "misc", "miscellaneous"}

    if lookup in ALIASES:
        return Classification(
            ALIASES[lookup],
            "alias",
            f"Alias matched filename '{base_clean}'",
        )

    filename_is_generic = (
        in_unsorted_bucket
        or any(norm.startswith(prefix) for prefix in GENERIC_PREFIXES)
        or len(base_clean) <= 2
    )

    # Preserve the production invariant: established canonical names are authoritative.
    # Only audit a narrow suspicious class: a lowercase one-word subject folder/name,
    # such as the accidental SmartSave classification "turned".
    root_name, folder_subject = _canonical_location_parts(filepath, target_dir)
    suspicious_lowercase_subject = (
        root_name in CANONICAL_ROOTS
        and bool(folder_subject)
        and folder_subject == folder_subject.lower()
        and "_" not in folder_subject
        and re.fullmatch(r"[a-z][a-z0-9-]{2,}", folder_subject) is not None
    )

    if not filename_is_generic and not suspicious_lowercase_subject:
        return Classification(
            normalize_entity_name(base_clean),
            "filename",
            f"Used existing filename '{base_clean}'",
        )

    base = os.path.splitext(filename)[0]
    companion_candidates = [
        os.path.join(os.path.dirname(filepath), f"{base}_workflow.png"),
    ]
    prompt = ""
    prompt_source = ""
    evidence_path = None

    for companion in companion_candidates:
        if os.path.exists(companion):
            prompt, prompt_source, evidence_path = extract_prompt_from_image(
                companion, target_dir
            )
            if prompt:
                prompt_source = f"video companion PNG; {prompt_source}"
                evidence_path = evidence_path or companion
                break

    if not prompt:
        prompt, prompt_source, evidence_path = extract_prompt_from_image(
            filepath, target_dir
        )

    if not prompt:
        if suspicious_lowercase_subject and not filename_is_generic:
            return Classification(
                normalize_entity_name(base_clean),
                "filename",
                f"Kept suspicious existing filename '{base_clean}' because no usable prompt metadata was found",
            )
        return Classification(
            "Unsorted",
            "no-prompt",
            "Generic filename and no usable metadata/counterpart prompt",
        )

    result = query_ollama(prompt, model=model)
    detail = prompt_source
    if result.detail:
        detail = f"{prompt_source}; {result.detail}" if prompt_source else result.detail

    # A suspicious existing name is changed only if metadata produces a concrete subject.
    # If recovery is inconclusive, preserve it rather than guessing.
    if suspicious_lowercase_subject and not filename_is_generic and result.subject == "Unsorted":
        return Classification(
            normalize_entity_name(base_clean),
            "filename",
            f"Kept suspicious existing filename '{base_clean}' because prompt audit was inconclusive; {detail}",
        )

    preferred_index = None
    if evidence_path and os.path.abspath(evidence_path) != os.path.abspath(filepath):
        try:
            rel = os.path.relpath(evidence_path, target_dir)
            parts = rel.split(os.sep)
            if (
                len(parts) >= 3
                and parts[0] in CANONICAL_ROOTS
                and parts[1].lower() not in UNSORTED_NAMES
            ):
                preferred_index = canonical_index_from_path(evidence_path)
        except Exception:
            preferred_index = None

    return Classification(
        result.subject,
        result.source,
        detail,
        preferred_index=preferred_index,
    )

def _canonical_location_parts(filepath, target_dir):
    try:
        rel = os.path.relpath(filepath, target_dir)
        parts = rel.split(os.sep)
        if len(parts) >= 3 and parts[0] in CANONICAL_ROOTS:
            return parts[0], parts[1]
    except Exception:
        pass
    return None, None


def is_already_canonical(filepath, target_dir, subject):
    root, folder_subject = _canonical_location_parts(filepath, target_dir)
    if not root:
        return False
    expected_root = canonical_root_for_file(os.path.basename(filepath))
    if root != expected_root or folder_subject.lower() != subject.lower():
        return False

    filename = os.path.basename(filepath)
    if filename.lower().endswith("_workflow.png"):
        return bool(re.match(rf"^{re.escape(subject)}_\d+_workflow\.png$", filename, flags=re.IGNORECASE))

    ext = re.escape(os.path.splitext(filename)[1])
    return bool(re.match(rf"^{re.escape(subject)}_\d+{ext}$", filename, flags=re.IGNORECASE))


def _index_pattern(subject, ext, workflow=False):
    if workflow:
        return re.compile(rf"^{re.escape(subject)}_(\d+)_workflow\.png$", re.IGNORECASE)
    return re.compile(rf"^{re.escape(subject)}_(\d+){re.escape(ext)}$", re.IGNORECASE)


def _used_indices(dest_folder, subject, ext, reserved_paths, workflow=False):
    used = set()
    pattern = _index_pattern(subject, ext, workflow=workflow)
    if os.path.isdir(dest_folder):
        for name in os.listdir(dest_folder):
            match = pattern.match(name)
            if match:
                used.add(int(match.group(1)))

    for path in reserved_paths:
        if os.path.dirname(path).lower() != os.path.abspath(dest_folder).lower():
            continue
        match = pattern.match(os.path.basename(path))
        if match:
            used.add(int(match.group(1)))
    return used


def next_destination(target_dir, filename, subject, reserved_paths, preferred_index=None):
    root_name = canonical_root_for_file(filename)
    if root_name is None:
        return None

    dest_folder = os.path.abspath(os.path.join(target_dir, root_name, subject))
    workflow = filename.lower().endswith("_workflow.png")
    ext = ".png" if workflow else os.path.splitext(filename)[1].lower()
    used = _used_indices(dest_folder, subject, ext, reserved_paths, workflow=workflow)

    # Preserve the generation number from a known canonical counterpart whenever that
    # exact destination is not already occupied/reserved.
    idx = None
    if preferred_index is not None and preferred_index > 0:
        if preferred_index not in used:
            idx = preferred_index

    if idx is None:
        idx = 1
        while idx in used:
            idx += 1

    if workflow:
        name = f"{subject}_{idx:05d}_workflow.png"
    else:
        name = f"{subject}_{idx:05d}{ext}"
    return os.path.join(dest_folder, name)

def safe_move(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        return False, "destination already exists"
    for _ in range(5):
        try:
            shutil.move(src, dst)
            return True, ""
        except PermissionError:
            time.sleep(0.2)
        except Exception as exc:
            return False, str(exc)
    return False, "file remained locked after retries"


def prune_empty_dirs(target_dir):
    for root, dirs, _ in os.walk(target_dir, topdown=False):
        for name in dirs:
            dir_path = os.path.join(root, name)
            try:
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
            except OSError:
                pass


def collect_media_files(target_dir):
    valid_exts = IMAGE_EXTS + VIDEO_EXTS
    files = []
    for root, dirs, names in os.walk(target_dir):
        # Avoid obvious non-library implementation/cache folders if the sorter is pointed too high.
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "venv"}]
        for name in names:
            if name.lower().endswith(valid_exts):
                files.append(os.path.abspath(os.path.join(root, name)))
    return sorted(files, key=lambda p: p.lower())


def plan_sort(target_dir, model="llama3.2:3b"):
    target_dir = os.path.abspath(target_dir)
    all_files = collect_media_files(target_dir)
    reserved_paths = set()
    plans = []

    print(f"Scanning full tree: {target_dir}")
    print(f"Found {len(all_files)} supported image/video files.\n")

    for filepath in all_files:
        filename = os.path.basename(filepath)
        classification = determine_subject(filename, filepath, model, target_dir)

        if is_already_canonical(filepath, target_dir, classification.subject):
            plans.append(PlannedMove(
                filepath, filepath, classification.subject, classification.source,
                classification.detail, "KEEP",
                preferred_index=classification.preferred_index,
                diagnostic=(unresolved_diagnostic(filepath, target_dir) if classification.subject == "Unsorted" else None),
            ))
            reserved_paths.add(filepath)
            continue

        dest_path = next_destination(
            target_dir, filename, classification.subject, reserved_paths,
            preferred_index=classification.preferred_index,
        )
        if not dest_path:
            plans.append(PlannedMove(
                filepath, filepath, classification.subject, classification.source,
                "Unsupported destination type", "SKIP",
                preferred_index=classification.preferred_index,
                diagnostic=(unresolved_diagnostic(filepath, target_dir) if classification.subject == "Unsorted" else None),
            ))
            continue

        reserved_paths.add(os.path.abspath(dest_path))
        action = "MOVE" if os.path.abspath(filepath) != os.path.abspath(dest_path) else "KEEP"
        plans.append(PlannedMove(
            filepath, os.path.abspath(dest_path), classification.subject,
            classification.source, classification.detail, action,
            preferred_index=classification.preferred_index,
            diagnostic=(unresolved_diagnostic(filepath, target_dir) if classification.subject == "Unsorted" else None),
        ))

    return plans


def print_plan(plans, target_dir, verbose=False):
    counts = {"KEEP": 0, "MOVE": 0, "SKIP": 0, "ERROR": 0}
    unsorted = 0

    for item in plans:
        counts[item.action] = counts.get(item.action, 0) + 1
        if item.subject == "Unsorted":
            unsorted += 1

        if item.action == "MOVE" or verbose or item.subject == "Unsorted":
            src = os.path.relpath(item.src, target_dir)
            dst = os.path.relpath(item.dst, target_dir)
            print(f"[{item.action}] {src}")
            if item.action == "MOVE":
                print(f"       -> {dst}")
            print(f"       subject={item.subject} | source={item.source}")
            if item.detail:
                print(f"       reason={item.detail}")
            if item.preferred_index is not None:
                print(f"       counterpart generation={item.preferred_index:05d}")
            if item.subject == "Unsorted" and item.diagnostic:
                print(f"       forensic={item.diagnostic.get('likely_failure_stage', '')}")
                candidates = item.diagnostic.get("nearest_candidates", [])
                if candidates:
                    closest = candidates[0]
                    print(
                        f"       nearest={closest['path']} "
                        f"({closest['seconds_apart']:.3f}s apart)"
                    )

    print("\nSummary")
    print("-------")
    print(f"Keep in place : {counts.get('KEEP', 0)}")
    print(f"Would move    : {counts.get('MOVE', 0)}")
    print(f"Skipped       : {counts.get('SKIP', 0)}")
    print(f"Unsorted      : {unsorted}")


def write_report(plans, target_dir, report_path):
    payload = {
        "target_dir": os.path.abspath(target_dir),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": [
            {
                "action": p.action,
                "source_path": os.path.relpath(p.src, target_dir),
                "destination_path": os.path.relpath(p.dst, target_dir),
                "subject": p.subject,
                "classification_source": p.source,
                "detail": p.detail,
                "preferred_generation_index": p.preferred_index,
                "diagnostic": p.diagnostic,
            }
            for p in plans
        ],
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"Detailed report: {os.path.abspath(report_path)}")


def apply_plan(plans, target_dir, purge_empty=True):
    moved = 0
    failed = 0
    for item in plans:
        if item.action != "MOVE":
            continue
        success, error = safe_move(item.src, item.dst)
        if success:
            moved += 1
            print(f"Moved: {os.path.relpath(item.src, target_dir)} -> {os.path.relpath(item.dst, target_dir)}")
        else:
            failed += 1
            print(f"[ERROR] Could not move {item.src}: {error}")

    if purge_empty:
        prune_empty_dirs(target_dir)

    print(f"\nApplied plan: {moved} moved, {failed} failed.")
    return failed == 0


def sort_directory(target_dir, model="llama3.2:3b", apply=False, purge_empty=True, report_path=None, verbose=False):
    target_dir = os.path.abspath(target_dir)
    if not os.path.isdir(target_dir):
        raise FileNotFoundError(f"Output directory does not exist: {target_dir}")

    plans = plan_sort(target_dir, model=model)
    print_plan(plans, target_dir, verbose=verbose)

    if report_path:
        write_report(plans, target_dir, report_path)

    if not apply:
        print("\nDRY RUN ONLY — no files were moved or renamed.")
        print("Run again with --apply only after reviewing the preview/report.")
        return plans

    print("\nAPPLY MODE — executing the plan above.")
    apply_plan(plans, target_dir, purge_empty=purge_empty)
    return plans


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "SmartSave full-library sorter/resorter. Dry-run is the default; "
            "use --apply to actually move files."
        )
    )
    parser.add_argument(
        "--dir",
        type=str,
        default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "output")),
        help="Path to the ComfyUI output directory",
    )
    parser.add_argument("--model", type=str, default="llama3.2:3b", help="Ollama model tag")
    parser.add_argument("--apply", action="store_true", help="Actually move/rename files. Without this flag, preview only.")
    parser.add_argument("--no-purge", action="store_true", help="Do not remove empty directories after --apply")
    parser.add_argument("--verbose", action="store_true", help="Show KEEP decisions as well as planned moves")
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Optional path for a JSON decision report",
    )
    args = parser.parse_args()

    try:
        script_directory = os.path.dirname(os.path.abspath(__file__))
        load_aliases(script_directory)
        sort_directory(
            args.dir,
            model=args.model,
            apply=args.apply,
            purge_empty=not args.no_purge,
            report_path=args.report,
            verbose=args.verbose,
        )
    except Exception:
        print("\n[ERROR] Auto-Sort encountered an exception:")
        traceback.print_exc()
        raise SystemExit(1)
