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
DEFAULT_RESOLUTION = "4k"
DEFAULT_SHOT_SECONDS = 15

# Model picks — change here if you want to try alternatives.
VIDEO_MODEL = "seedance_2"
MOODBOARD_IMAGE_MODEL = "gpt-image-2"
CHARACTER_IMAGE_MODEL = "gtv_flux_2_pro"
KEYFRAME_IMAGE_MODEL = "gtv_seedream_4_5"

# Concurrency for VIP+ plan
VIDEO_CONCURRENCY = 3
IMAGE_CONCURRENCY = 4
