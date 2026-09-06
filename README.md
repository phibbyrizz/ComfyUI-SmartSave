# ComfyUI-SmartSave & Auto-Sort

Dual-purpose image saving and organization toolchain for ComfyUI.  
Routes high-fidelity masters and web-ready compressed previews into structured, subject-dedicated directories using deterministic parsing and local LLM metadata extraction via Ollama.

---

## Key Features

* **Split-Root Architecture:**
  * `.png` masters (with embedded generation workflows and positive prompt chunks) route to `Raw/<Subject>/`.
  * `.webp` or `.jpg` lightweight previews route to `Compressed/<Subject>/`.
* **Smart LLM Subject Parsing:** Analyzes positive prompt metadata using a local Ollama model (`llama3.2:3b`) without tripping conversational refusals or scene guardrails.
* **Format-Aware Duplication Control:** Automatically resolves index collisions without overwriting existing renders (e.g., `ComfyUI_00001.png`, `ComfyUI_00002.png`).
* **Clean Tree Maintenance:** Recursively detects and purges orphaned directories, temporary folders, and lingering cache locks (`Thumbs.db`, `desktop.ini`).
* **Flexible Execution:** Use the **Smart Save Image** custom node directly inside ComfyUI workflows, or run the standalone **`auto_sort.py`** utility for batch processing existing libraries.

---

## Prerequisites

1. **Install Ollama**: Download and run from [ollama.com](https://ollama.com).
2. **Pull the Classifier Model**:
   ```bash
   ollama pull llama3.2:3b
   ```

---

## Installation

1. Navigate to your ComfyUI custom nodes folder:
   ```bash
   cd ComfyUI/custom_nodes
   ```
2. Clone this repository:
   ```bash
   git clone [https://github.com/your-username/ComfyUI-SmartSave.git](https://github.com/your-username/ComfyUI-SmartSave.git)
   ```
3. Install required dependencies into your ComfyUI environment:
   * **Portable / Embedded Python:**
     ```bash
     ..\..\..\python_embeded\python.exe -m pip install -r requirements.txt
     ```
   * **Standard Python Virtual Environment:**
     ```bash
     pip install -r requirements.txt
     ```

---

## Node Usage (In ComfyUI)

Add the **Smart Save Image (Raw/Compressed)** node from `image/saving` in your node browser:

* **`save_raw_png`**: Generates full-resolution `.png` files with embedded metadata inside `Raw/`.
* **`save_compressed_webp`**: Generates web-optimized `.webp` files inside `Compressed/`.
* **`webp_quality`**: Compression quality level (default: `90`).
* **`filename_prefix`**: Sequential filename root (default: `ComfyUI`).
* **`subfolder`**: Optional manual override subfolder.

---

## Standalone Auto-Sort Utility

To organize your existing `/output` library or catch up a backlog of unsorted images:

1. Ensure Ollama is running in the background.
2. Run `auto_sort.py` from your terminal or command prompt:
   ```cmd
   python auto_sort.py --dir "path/to/ComfyUI/output"
   ```
   *(Or double-click `run auto sort.bat` if configured for your portable install).*

### Command-Line Arguments
| Flag | Description | Default |
| :--- | :--- | :--- |
| `--dir` | Path to your target output folder | `../../output` |
| `--model` | Ollama model tag used for subject classification | `llama3.2:3b` |
| `--no-purge` | Disables automated removal of empty subfolders | `False` |

---

## Example Workflow

An import-ready starter setup is provided in `examples/basic_workflow.json`[cite: 1]. Drag and drop it directly onto the ComfyUI workspace to inspect the recommended wiring[cite: 1].

---

## Support

If this node saves you time and disk-cleaning headaches, consider [buying me a coffee on Ko-fi](https://ko-fi.com/phibby)[cite: 1]!