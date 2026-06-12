import os
from pathlib import Path

def create_nightmare_pack(dest_root: Path):
    """
    Generates a 'Nightmare' sample pack with complex edge cases for testing.
    """
    dest_root.mkdir(parents=True, exist_ok=True)
    
    # 1. Deep Nesting (Path length and Depth capping test)
    deep_path = dest_root / "Pack_Infinity" / "Sub" / "Folders" / "That" / "Never" / "End" / "Kicks"
    deep_path.mkdir(parents=True, exist_ok=True)
    (deep_path / "long_nested_kick_01.wav").touch()
    
    # 2. HandsOff Folder
    hands_off = dest_root / "My_Precious_Structure"
    hands_off.mkdir(parents=True, exist_ok=True)
    (hands_off / ".unshuffle_preserved").touch()
    (hands_off / "dont_touch_this.wav").touch()
    (hands_off / "nested_dir").mkdir(parents=True, exist_ok=True)
    (hands_off / "nested_dir" / "file.wav").touch()

    # 3. Misleading Keywords (Strong vs Weak)
    misleading = dest_root / "Kicks"
    misleading.mkdir(parents=True, exist_ok=True)
    (misleading / "snare_but_in_kicks_folder.wav").touch()
    
    # 4. Characters and Symbols
    weird = dest_root / "Kit @#$%^&!"
    weird.mkdir(parents=True, exist_ok=True)
    (weird / "sample(01) [v1].wav").touch()
    
    # 5. Duplicate Content (Same size/mtime if possible, but definitely same name/content)
    dup1 = dest_root / "Kit_A" / "Kicks"
    dup2 = dest_root / "Duplicate_Check" / "Kicks"
    dup1.mkdir(parents=True, exist_ok=True)
    dup2.mkdir(parents=True, exist_ok=True)
    
    content = b"fake audio data"
    with open(dup1 / "kick_duplicate.wav", "wb") as f: f.write(content)
    with open(dup2 / "kick_duplicate.wav", "wb") as f: f.write(content)

    # 6. Multi-word & Overlap
    overlap = dest_root / "Full_Drum_Mix_Loops"
    overlap.mkdir(parents=True, exist_ok=True)
    (overlap / "full_mix_loop_120.wav").touch()

    # 7. macOS Ghost Files
    (dest_root / "._ghost_metadata.wav").touch()
    (dest_root / "Kicks" / "._kick_metadata.wav").touch()

    print(f"Nightmare pack created at: {dest_root}")

if __name__ == "__main__":
    root = Path(r"d:\Music\unshuffle\scratch\nightmare_pack")
    create_nightmare_pack(root)
