# ComfyUI-SmartSave

Dynamic, LLM-powered output organization for ComfyUI. 
Analyzes generation prompts using a local LLM via Ollama and automatically routes finished images into structured subject folders with sequence indexing.

## Prerequisites

1. **Install Ollama**: Download from [ollama.com](https://ollama.com).
2. **Pull the default model**:
   ```bash
   ollama pull llama3.2:3b
   ```

## 🧹 Auto-Sort & Library Cleanup Tool

Included with the node is a standalone post-processing script (`auto_sort.py` / `run auto sort.bat`) that keeps your image generation library clean, organized, and deduplicated.

### Key Features
* **Metadata Subject Classification:** Reads ComfyUI generation metadata directly from `.png` / `.jpg` files and uses a lightweight local Ollama model (`llama3.2:3b`) to identify the primary subject.
* **Loose Image Sorting:** Scans your output directory (or `Raw`/`Compressed` subfolders) and sorts unsorted images into dedicated, sequential character/subject folders.
* **Smart Folder Consolidator:** Automatically detects combination folders (e.g., `Ariel and Selena` or `Taylor Swift & Selena Gomez`), merges the files into the primary subject's existing folder with zero index collisions, and deletes the leftover empty folders.
* **Portable Launcher:** `run auto sort.bat` automatically detects standalone/embedded ComfyUI Python or your system environment.

### Quick Start
1. Ensure Ollama is running locally with the model installed:
   ```bash
   ollama run llama3.2:3b
   ```
2. Double-click `run auto sort.bat`.
3. To customize default target directories or add folder overrides, copy `config_local.py.example` to `config_local.py` and adjust the paths.

## Example Workflow

An import-ready starter setup is provided in `examples/basic_workflow.json`[cite: 1]. Drag and drop it directly onto the ComfyUI workspace to inspect the recommended wiring[cite: 1].

## Support

If this node saves you time and disk-cleaning headaches, consider [buying me a coffee on Ko-fi](https://ko-fi.com/phibby)[cite: 1]!