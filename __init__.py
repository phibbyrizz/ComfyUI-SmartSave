import sys
from pathlib import Path

# Ensure local module path resolution
current_dir = Path(__file__).parent.resolve()
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

from smart_save import SmartSaveImage, SmartSaveVideo

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

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

print("\n>>> [ComfyUI-SmartSave] Successfully registered Image and Video SmartSave nodes <<<\n")