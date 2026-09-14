from .smart_save import SmartSaveImage, SmartSaveVideo

# "SmartSave" is kept as a legacy alias so older workflows continue to load.
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

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

print("[ComfyUI-SmartSave] Registered Smart Save (Image) and Smart Save (Video)")
