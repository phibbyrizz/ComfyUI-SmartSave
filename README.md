# ComfyUI-SmartSave

**Smart saving and full-library organization for ComfyUI images and videos.**

ComfyUI-SmartSave adds dedicated image and video output nodes that automatically identify the primary person or character in a generation, organize outputs into subject folders, preserve useful workflow/prompt metadata, and create both high-quality masters and smaller sharing copies.

It also includes **Auto-Sort**, a standalone full-library sorter/resorter for cleaning up an existing ComfyUI `output` folder or repairing files that SmartSave could not classify correctly when they were created.

Subject classification runs locally through [Ollama](https://ollama.com/). Auto-Sort is **preview-only by default** so you can review every proposed move before allowing it to reorganize your library.

---

## What SmartSave Does

Instead of letting ComfyUI accumulate thousands of files in one output folder, SmartSave organizes generations automatically:

```text
ComfyUI/output/
├── Raw/
│   └── Subject_Name/
│       ├── Subject_Name_00001.png
│       └── Subject_Name_00002.png
│
├── Compressed/
│   └── Subject_Name/
│       ├── Subject_Name_00001.jpg
│       └── Subject_Name_00002.jpg
│
├── Raw Video/
│   └── Subject_Name/
│       ├── Subject_Name_00001.mp4
│       └── Subject_Name_00001_workflow.png
│
└── Webm/
    └── Subject_Name/
        └── Subject_Name_00001.webm
```

Image and video variants use synchronized generation numbers so related files remain easy to identify.

If SmartSave cannot determine a reliable subject, it uses `Unsorted` rather than forcing a guess.

---

## Features

### Smart Save (Image)

- Saves a high-quality PNG master to `Raw/<Subject>/`
- Optionally saves a compressed JPEG copy to `Compressed/<Subject>/`
- Preserves ComfyUI workflow metadata in PNG files
- Stores the positive prompt in PNG metadata when supplied
- Stores recoverable prompt information in JPEG EXIF metadata
- Keeps PNG/JPEG generation numbers synchronized
- Supports manual subject-folder override
- Supports custom filename prefixes
- Configurable JPEG quality

### Smart Save (Video)

- Saves an H.264 MP4 master to `Raw Video/<Subject>/`
- Optionally saves a VP9 WebM copy to `Webm/<Subject>/`
- Saves a first-frame `_workflow.png` companion containing ComfyUI workflow metadata
- Keeps MP4, WebM, and workflow-PNG generation numbers synchronized
- Supports optional ComfyUI audio input
  - AAC audio for MP4
  - Opus audio for WebM
- Configurable frame rate and WebM CRF
- Supports manual subject-folder override
- Supports custom filename prefixes

### Local Subject Classification

SmartSave uses a local Ollama model to identify the primary named person, actor, creator, fictional character, username, handle, or LoRA trigger from the positive prompt. Identities may be one word or multiple words and may appear anywhere in a long prompt.

Default model:

```text
llama3.2:3b
```

Classification stays on your machine. SmartSave also includes conservative local parsing/fallback behavior for common failure cases, including single-word identities. If there is still not enough evidence, it safely falls back to `Unsorted`.

### Auto-Sort

`auto_sort.py` is the full-library organization and recovery utility.

It can:

- Scan an entire existing ComfyUI output tree
- Organize a large pre-existing/messy ComfyUI library
- Re-scan an already organized SmartSave library while trusting established canonical subject folders by default
- Repair files SmartSave originally placed in `Misc` or `Unsorted`
- Read supported PNG/JPEG metadata
- Recover prompts from related raw/workflow files when possible
- Handle PNG, JPG, JPEG, WebP, MP4, WebM, MOV, and MKV
- Preserve trusted generation numbers across related video files
- Use aliases and local classification fallbacks, including single-word identity recovery
- Avoid overwriting existing destination files
- Leave files safely in `Unsorted` when there is not enough evidence to classify them
- Produce optional JSON decision reports
- Preview all proposed changes without moving anything

Auto-Sort is designed to be safe to run repeatedly: an already-clean library should produce no additional moves.

---

## Requirements

- ComfyUI
- Python packages listed in `requirements.txt`
- [Ollama](https://ollama.com/) for automatic subject classification
- FFmpeg for video output

The video node searches for FFmpeg through `imageio-ffmpeg`, the system `PATH`, and common ComfyUI/portable locations.

---

## Installation

### 1. Install the custom node

Clone this repository into your ComfyUI `custom_nodes` directory:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/phibbyrizz/ComfyUI-SmartSave.git
```

Or download the repository and place the `ComfyUI-SmartSave` folder inside:

```text
ComfyUI/custom_nodes/
```

### 2. Install Python requirements

Install the packages from `requirements.txt` using the same Python environment that runs ComfyUI:

```bash
python -m pip install -r requirements.txt
```

For portable/embedded ComfyUI installations, use that installation's embedded Python rather than a separate system Python.

### 3. Install Ollama

Install Ollama, then pull the default classifier model:

```bash
ollama pull llama3.2:3b
```

Make sure Ollama is running when you want automatic subject classification.

### 4. Restart ComfyUI

After restarting, the nodes should appear as:

- **Smart Save (Image)** under `image/saving`
- **Smart Save (Video)** under `video/saving`

---

## Smart Save (Image)

### Required inputs

| Input | Purpose |
| --- | --- |
| `images` | IMAGE batch to save |
| `filename_prefix` | `auto` uses the detected subject; custom text uses your chosen prefix |
| `save_raw_png` | Save PNG master |
| `save_compressed_jpg` | Save JPEG copy |
| `jpg_quality` | JPEG quality from 1–100 |

### Optional inputs

| Input | Purpose |
| --- | --- |
| `positive_prompt` | Preferred prompt source for subject detection and metadata |
| `subfolder` | Manual subject/folder override; bypasses automatic classification |
| `ollama_model` | Ollama model tag; default `llama3.2:3b` |

When `positive_prompt` is not connected, SmartSave can inspect available ComfyUI prompt data as a fallback.

---

## Smart Save (Video)

### Required inputs

| Input | Purpose |
| --- | --- |
| `images` | IMAGE frame batch |
| `filename_prefix` | `auto` uses the detected subject |
| `frame_rate` | Output playback frame rate |
| `save_raw_mp4` | Save H.264 MP4 master |
| `save_webm` | Save VP9 WebM copy |
| `save_metadata_png` | Save first-frame workflow PNG |
| `webm_crf` | WebM quality control; lower values generally mean higher quality |

### Optional inputs

| Input | Purpose |
| --- | --- |
| `audio` | Optional ComfyUI AUDIO input |
| `positive_prompt` | Preferred prompt source for subject detection |
| `subfolder` | Manual subject/folder override |
| `ollama_model` | Ollama model tag; default `llama3.2:3b` |

For a normal video generation, SmartSave keeps the related files together by generation number:

```text
Raw Video/Subject_Name/Subject_Name_00004.mp4
Raw Video/Subject_Name/Subject_Name_00004_workflow.png
Webm/Subject_Name/Subject_Name_00004.webm
```

---

## Auto-Sort: Clean Up an Existing ComfyUI Library

Auto-Sort is useful even if you have never used SmartSave before.

Point it at an existing ComfyUI output folder and it can inspect supported media, recover available prompt/metadata information, identify subjects, and build the SmartSave folder structure.

It is also the recovery tool for files SmartSave itself could not classify correctly.

### Windows launchers

The repository includes two launchers:

```text
auto_sort_preview.bat
auto_sort_apply.bat
```

**Start with `auto_sort_preview.bat`.**

Preview mode scans the library and shows what Auto-Sort *would* change without moving or renaming anything.

A typical preview summary looks like:

```text
Summary
-------
Keep in place : 1473
Would move    : 4
Skipped       : 0
Unsorted      : 5

DRY RUN ONLY — no files were moved or renamed.
```

Review the proposed moves first.

When the preview looks correct, run:

```text
auto_sort_apply.bat
```

Apply mode performs the planned moves.

Afterward, running preview again is a useful verification step. A clean, fully organized library should normally report:

```text
Would move : 0
```

### Command line

Preview only:

```bash
python auto_sort.py --dir "path/to/ComfyUI/output"
```

Apply changes:

```bash
python auto_sort.py --dir "path/to/ComfyUI/output" --apply
```

Useful options:

```text
--model MODEL     Ollama model tag (default: llama3.2:3b)
--apply           Actually move/rename files
--no-purge        Keep empty directories after apply
--verbose         Show KEEP decisions as well as planned moves
--report PATH     Write a detailed JSON decision report
```

Without `--apply`, Auto-Sort is always non-destructive.

---

## How Auto-Sort Decides Where a File Belongs

Auto-Sort uses multiple sources of evidence rather than depending on a single metadata format.

Depending on the file, it can use:

- Existing meaningful filenames
- Local alias mappings
- PNG prompt/workflow metadata
- JPEG metadata
- Related raw/workflow files
- Timestamp-matched counterparts
- Local prompt parsing
- Ollama subject classification

If there is not enough reliable information, the file remains in `Unsorted`.

This is intentional: **an unresolved file is safer than a confidently misclassified file.**

---

## Optional Local Aliases

You can create a local `aliases.json` in the SmartSave folder to normalize personal shorthand, character variants, or prompt aliases.

Example:

```json
{
  "example_alias": "Canonical_Subject_Name",
  "another_alias": "Another_Subject"
}
```

`aliases.json` is intended as local/personal configuration and is excluded from Git by the repository's `.gitignore`.

---

## Example Workflow

An example workflow is included in:

```text
Examples/basic workflow.json
```

Load it into ComfyUI as a starting point and adapt the prompt/model portion to your own workflow.

---

## Safety and Library Migration

Auto-Sort can reorganize large existing libraries, so the recommended workflow is:

1. Run **preview**
2. Review proposed moves
3. Use **apply** only when the preview is correct
4. Run preview again afterward to confirm the library is stable

Auto-Sort includes collision protection and does not intentionally overwrite an existing destination file.

For irreplaceable libraries, maintaining a separate backup is still recommended before any large-scale file reorganization.

---

## Development and Regression Tests

SmartSave includes an automated regression suite for Auto-Sort.

On Windows:

```text
tests/run regression tests.bat
```

Or run the test file directly:

```bash
python tests/test_auto_sort.py
```

The suite currently contains **15 regression tests** covering:

- Full-tree scanning
- Already-correct canonical file preservation
- PNG metadata recovery
- Alias handling
- Generic-source generation numbering
- Safe unresolved-file behavior
- Video counterpart generation-number preservation
- Collision protection
- Dry-run safety
- Apply/rescan idempotence
- Forensic nearby-counterpart reporting
- Single-word subject recovery
- Repair of suspicious lowercase misclassifications
- Protection against noisy metadata reclassifying trusted canonical files
- Prevention of unresolved `Unsorted` results poisoning the in-memory classifier cache

Changes to Auto-Sort should pass the regression suite before release.

---

## Troubleshooting

### Everything goes to `Unsorted`

Check that:

1. Ollama is installed and running
2. The configured model is available
3. Your positive prompt contains a recognizable named person/character
4. `positive_prompt` is connected when your workflow uses custom prompt/preset nodes

Test the default model with:

```bash
ollama run llama3.2:3b
```

### Video encoding fails

Make sure FFmpeg is available. SmartSave checks several common locations, but unusual installations may still require FFmpeg to be available on your system `PATH`.

### Auto-Sort leaves some files in `Unsorted`

That can be correct behavior.

Old or externally generated files may contain no usable prompt metadata and may have no matching raw/workflow counterpart. Auto-Sort intentionally leaves those files unresolved rather than guessing.

Use `--verbose` and/or `--report` when you need more detail about classification decisions.

### A manual folder is preferable for a generation

Use the `subfolder` input on either Smart Save node. A manual subfolder bypasses automatic subject classification for that save.

---

## Privacy

Automatic subject classification uses your locally running Ollama instance at `127.0.0.1`. SmartSave does not require a cloud classification API.

---

## Support

If SmartSave saves you time or helps tame a large ComfyUI output library, you can support development on Ko-fi:

**https://ko-fi.com/phibby**

---

## License

See [LICENSE](LICENSE) for license terms.
