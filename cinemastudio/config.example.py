"""Example config. Copy to config.py and fill in your keys.

config.py is gitignored — your real keys will never be committed.

You can also let `cinema setup` create config.py for you interactively.
"""

# ai-auto.io API key (https://ai-auto.io/ → account)
AI_AUTO_API_KEY = ""

# Anthropic API key for script generation (https://console.anthropic.com/)
ANTHROPIC_API_KEY = ""

# Defaults — override via CLI flags
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_RESOLUTION = "4k"           # video resolution
DEFAULT_IMAGE_RESOLUTION = "4k"     # image generation resolution
DEFAULT_SHOT_SECONDS = 15

# Model picks — change here if you want to try alternatives.
# Nano Banana Pro is currently top-tier for photorealism and structured prompts;
# fallbacks include "gtv_seedream_4_5" (best with refs), "gtv_flux_2_pro",
# "gtv_imagen_4_ultra", "gpt-image-2".
VIDEO_MODEL = "seedance_2"
MOODBOARD_IMAGE_MODEL = "nano_banana_pro"
CHARACTER_IMAGE_MODEL = "nano_banana_pro"
KEYFRAME_IMAGE_MODEL = "nano_banana_pro"

# Concurrency for VIP+ plan
VIDEO_CONCURRENCY = 3
IMAGE_CONCURRENCY = 4
