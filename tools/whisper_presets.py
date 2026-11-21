"""Quick Whisper preset configurations.

Copy these into src/core/config.py to quickly switch settings.
"""

# ============================================================
# PRESET 1: FAST (Gaming, Quick Commands)
# ============================================================
FAST = """
        self.whisper_model = "base"
        self.whisper_beam_size = 1
        self.whisper_compute_type = "int8"
        # Fast response (~0.5s), decent accuracy (85%)
"""

# ============================================================
# PRESET 2: BALANCED (Default - Recommended)
# ============================================================
BALANCED = """
        self.whisper_model = "small"
        self.whisper_beam_size = 5
        self.whisper_compute_type = "int8"
        # Great accuracy (~92%), reasonable speed (~1.5s)
"""

# ============================================================
# PRESET 3: QUALITY (Dictation, Quiet Environment)
# ============================================================
QUALITY = """
        self.whisper_model = "medium"
        self.whisper_beam_size = 7
        self.whisper_compute_type = "int8"
        # Best accuracy (~95%), slower (~3-4s)
"""

# ============================================================
# PRESET 4: MAXIMUM (Perfect Transcription)
# ============================================================
MAXIMUM = """
        self.whisper_model = "large"
        self.whisper_beam_size = 10
        self.whisper_compute_type = "int8"
        # Maximum accuracy (~98%), very slow (~5-7s)
"""

# ============================================================
# PRESET 5: GPU ACCELERATED (Requires NVIDIA GPU + CUDA)
# ============================================================
GPU = """
        self.whisper_model = "small"         # or "medium", "large"
        self.whisper_device = "cuda"
        self.whisper_compute_type = "float16"
        self.whisper_beam_size = 5
        # 10x faster with GPU! (~0.2s for small model)
"""

# ============================================================
# PRESET 6: MULTILINGUAL (Auto Language Detection)
# ============================================================
MULTILINGUAL = """
        self.whisper_model = "small"
        self.whisper_beam_size = 5
        self.whisper_language = None         # Auto-detect
        self.whisper_compute_type = "int8"
        # Detects and transcribes any language
"""

# ============================================================
# How to Use:
# ============================================================
# 1. Open: src/core/config.py
# 2. Find the "Whisper settings" section
# 3. Replace with your chosen preset
# 4. Save and restart the app

# Example:
# Replace this in config.py:
#   self.whisper_model = "small"
#   self.whisper_beam_size = 5
#
# With the FAST preset:
#   self.whisper_model = "base"
#   self.whisper_beam_size = 1

if __name__ == "__main__":
    print("Whisper Presets Available:\n")
    print("1. FAST       - Quick response (0.5s), good for gaming")
    print("2. BALANCED   - Default (1.5s), recommended for most users")
    print("3. QUALITY    - High accuracy (3-4s), quiet environments")
    print("4. MAXIMUM    - Perfect accuracy (5-7s), when quality matters")
    print("5. GPU        - Super fast (0.2s), requires NVIDIA GPU")
    print("6. MULTILINGUAL - Auto language detection")
    print("\nCurrent setting: BALANCED")
    print("\nTo change: Edit src/core/config.py")
