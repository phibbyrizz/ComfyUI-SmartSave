import sys
from pathlib import Path

# Ensure local module path resolution
current_dir = Path(__file__).parent.resolve()
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

from smart_save import SmartSaveImage

NODE_CLASS_MAPPINGS = {
    "SmartSaveImage": SmartSaveImage
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SmartSaveImage": "Smart LLM Save Image"
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

print("\n>>> [ComfyUI-SmartSave] Successfully registered 'Smart LLM Save Image' node <<<\n")