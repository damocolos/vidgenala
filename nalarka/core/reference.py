"""Reference library scanning and style/orientation extraction."""
import os
import json
import subprocess
from pathlib import Path
from typing import List, Optional
from PIL import Image


def scan_reference_library(library_dir: str) -> List[str]:
    lib_path = Path(library_dir)
    lib_path.mkdir(parents=True, exist_ok=True)
    valid_exts = (".mp4", ".mov", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".webp")
    return sorted([
        f.name for f in lib_path.iterdir()
        if f.is_file() and f.suffix.lower() in valid_exts and not f.name.startswith(".")
    ])


def analyze_reference_video(ref_file_path: Optional[str], log_fn=None) -> dict:
    default_palette = {
        "cream": "#F2EADA",
        "orange_bg": "#EE8631",
        "green_bg": "#38844C",
        "blue_bg": "#2680C2",
        "dark": "#181818",
        "red_hot": "#EC4C28",
        "yellow_stage": "#F6C23E",
        "blue_soft": "#B3D9EE",
        "blue_tear": "#2680C2",
        "mint_soft": "#B5DEC8",
        "pink_organ": "#F38375",
        "pink_soft": "#F5A3A2",
        "purple_soft": "#C8B6E2",
        "skin": "#F7C6A3",
        "skin_dark": "#E0A37B",
        "white": "#FFFFFF",
        "grey": "#7F7B74",
        "stone": "#6E6A63",
        "stone_dark": "#4A4742"
    }
    default_profile = {
        "has_reference": False,
        "ref_name": "Default Nalarka Flat-Vector Palette",
        "width": 1080,
        "height": 1920,
        "orientation": "portrait",
        "avg_cut_seconds": 2.4,
        "recommended_bpm": 116,
        "palette": default_palette
    }

    if not ref_file_path or not os.path.exists(ref_file_path):
        return default_profile

    ref_path_obj = Path(ref_file_path)
    try:
        ext = ref_path_obj.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            im = Image.open(ref_file_path).convert("RGB")
            ref_w, ref_h = im.size
        else:
            probe_cmd = [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json", ref_file_path
            ]
            probe_json = json.loads(subprocess.check_output(probe_cmd, text=True))
            stream = probe_json.get("streams", [{}])[0]
            ref_w = int(stream.get("width", 1080))
            ref_h = int(stream.get("height", 1920))

        orientation = "portrait" if ref_h >= ref_w else "landscape"
        return {
            "has_reference": True,
            "ref_name": ref_path_obj.name,
            "width": 1080 if orientation == "portrait" else 1920,
            "height": 1920 if orientation == "portrait" else 1080,
            "orientation": orientation,
            "avg_cut_seconds": 2.4,
            "recommended_bpm": 116,
            "palette": default_palette
        }
    except Exception:
        return default_profile
