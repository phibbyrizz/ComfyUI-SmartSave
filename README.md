# ComfyUI-SmartSave & Auto-Sort

Dual-purpose image and video saving and organization toolchain for ComfyUI.  
Routes high-fidelity masters, compressed web previews, and workflow metadata into structured, subject-dedicated directories using deterministic parsing and local LLM metadata extraction via Ollama[cite: 1, 2].

---

## Key Features

* 4-Folder Structured Architecture:
  * Images:
    * High-fidelity .png masters (with embedded generation workflows and metadata) route to Raw/<Subject>/[cite: 1, 2].
    * Lightweight .jpg previews route to Compressed/<Subject>/[cite: 1, 2].
  * Videos:
    * Master .mp4 (H.264 high-bitrate) files route to Raw Video/<Subject>/[cite: 1].
    * Lightweight .webm (VP9 optimized) clips route to Webm/<Subject>/[cite: 1].
    * Companion workflow .png (first frame with embedded generation graph) saves to Raw Video/<Subject>/ for workflow portability[cite: 1].
* Integrated Audio Support: Automatically muxes optional ComfyUI AUDIO streams into MP4 (AAC) and WebM (Opus) files[cite: 1].
* Smart LLM Subject Parsing: Analyzes positive prompt metadata using a local Ollama model (llama3.2:3b) without tripping conversational refusals or scene guardrails[cite: 1, 2].
* External Alias Mapping: Support for aliases.json to map character variants, costumes, or LoRA triggers directly to standardized directory names[cite: 1].
* Collision-Free Sequencing: Synchronized index counters across all target formats to prevent accidental overwrites[cite: 1, 2].
* Flexible Execution: Dedicated nodes for image and video workflows, alongside a standalone auto_sort.py utility for batch organization[cite: 1, 2].

---

## Prerequisites

1. Install Ollama: Download and run from https://ollama.com[cite: 2].
2. Pull the Classifier Model:
   ollama pull llama3.2:3b
3. FFmpeg: Required for video encoding[cite: 1]. The node automatically uses system ffmpeg on your PATH or the embedded binary bundled with ComfyUI-VideoHelperSuite[cite: 1].

---

## Installation

1. Navigate to your ComfyUI custom nodes folder:
   cd ComfyUI/custom_nodes
2. Clone this repository:
   git clone https://github.com/phibbyrizz/ComfyUI-SmartSave.git
3. Restart ComfyUI.

---

## Node Usage

### 1. Smart Save (Image)
Found under image/saving[cite: 1]. Saves raw and compressed image pairs simultaneously[cite: 1].

- images (IMAGE, Required): Image batch input from VAE Decode or upscaler[cite: 1].
- filename_prefix (STRING, default: "auto"): Set to "auto" to use the LLM-extracted subject as prefix[cite: 1].
- save_raw_png (BOOLEAN, default: true): Writes uncompressed PNG with metadata to Raw/<Subject>/[cite: 1].
- save_compressed_jpg (BOOLEAN, default: true): Writes optimized JPG to Compressed/<Subject>/[cite: 1].
- jpg_quality (INT, default: 90): Compression quality (1-100)[cite: 1].
- positive_prompt (STRING, Optional): Connect your positive prompt text to trigger LLM parsing[cite: 1].
- subfolder (STRING, Optional): Explicit folder override (bypasses Ollama when set)[cite: 1].
- ollama_model (STRING, default: "llama3.2:3b"): Target Ollama model tag[cite: 1].

---

### 2. Smart Save (Video)
Found under video/saving[cite: 1]. Encodes video frame batches into archival and web formats simultaneously[cite: 1].

- images (IMAGE, Required): Video frame batch[cite: 1].
- audio (AUDIO, Optional): Waveform input to mux into MP4 (AAC) and WebM (Opus)[cite: 1].
- frame_rate (INT, default: 24): Playback FPS for encoded video files[cite: 1].
- save_raw_mp4 (BOOLEAN, default: true): Saves H.264 master video to Raw Video/<Subject>/[cite: 1].
- save_webm (BOOLEAN, default: true): Saves VP9 compressed clip to Webm/<Subject>/[cite: 1].
- save_metadata_png (BOOLEAN, default: true): Saves first frame PNG with full workflow graph to Raw Video/<Subject>/[cite: 1].
- webm_crf (INT, default: 32): Constant Rate Factor for WebM (lower = higher quality)[cite: 1].
- filename_prefix (STRING, default: "auto"): File naming prefix ("auto" uses subject name)[cite: 1].
- positive_prompt (STRING, Optional): Connect your prompt text for subject extraction[cite: 1].
- subfolder (STRING, Optional): Folder name override[cite: 1].

---

## Standalone Auto-Sort Utility

To organize your existing /output library or catch up a backlog of unsorted generations:

1. Ensure Ollama is running in the background[cite: 2].
2. Run auto_sort.py from your terminal or command prompt[cite: 2]:
   python auto_sort.py --dir "path/to/ComfyUI/output"

Command-Line Arguments:
- --dir: Path to your target output folder (Default: ../../output)[cite: 2]
- --model: Ollama model tag used for subject classification (Default: llama3.2:3b)[cite: 2]
- --no-purge: Disables automated removal of empty subfolders (Default: False)[cite: 2]

---

## Support

If this node saves you time and disk-cleaning headaches, consider buying me a coffee on Ko-fi (https://ko-fi.com/phibby)[cite: 1]!