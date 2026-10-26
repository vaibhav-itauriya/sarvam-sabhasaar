import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
STT_MODEL = os.getenv("STT_MODEL", "saaras:v4")
LLM_MODEL = os.getenv("LLM_MODEL", "sarvam-105b")
TTS_MODEL = os.getenv("TTS_MODEL", "bulbul:v3")
TTS_SPEAKER = os.getenv("TTS_SPEAKER", "shubh")

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
MEETINGS_DIR = DATA_DIR / "meetings"
MEETINGS_DIR.mkdir(parents=True, exist_ok=True)

# sarvam-105b has a 128K token context. Indic text is roughly 2-3 chars per token,
# so keep the full transcript in one call up to this many characters, and fall
# back to chunked summarising / retrieval above it.
FULL_CONTEXT_CHARS = int(os.getenv("FULL_CONTEXT_CHARS", "180000"))
CHUNK_CHARS = int(os.getenv("CHUNK_CHARS", "120000"))

# Languages Bulbul can speak. Questions can come in any of the 22 Saaras languages,
# spoken answers fall back to English text if the language is not in this list.
LANGUAGES = {
    "hi-IN": "हिन्दी",
    "en-IN": "English",
    "bn-IN": "বাংলা",
    "gu-IN": "ગુજરાતી",
    "kn-IN": "ಕನ್ನಡ",
    "ml-IN": "മലയാളം",
    "mr-IN": "मराठी",
    "od-IN": "ଓଡ଼ିଆ",
    "pa-IN": "ਪੰਜਾਬੀ",
    "ta-IN": "தமிழ்",
    "te-IN": "తెలుగు",
}
LANGUAGE_NAMES_EN = {
    "hi-IN": "Hindi", "en-IN": "English", "bn-IN": "Bengali", "gu-IN": "Gujarati",
    "kn-IN": "Kannada", "ml-IN": "Malayalam", "mr-IN": "Marathi", "od-IN": "Odia",
    "pa-IN": "Punjabi", "ta-IN": "Tamil", "te-IN": "Telugu", "ur-IN": "Urdu",
    "as-IN": "Assamese",
}


def has_key() -> bool:
    return bool(SARVAM_API_KEY)
