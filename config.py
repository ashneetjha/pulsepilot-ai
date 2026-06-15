import logging
import os
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env from the same directory as this file (flask-api root)
_here = os.path.dirname(__file__)
load_dotenv(os.path.join(_here, ".env"))


def get_gemini_api_key() -> str:
    """Return GEMINI_API_KEY from environment, logging a clear warning if missing.

    Call this before making any Gemini API calls to ensure a value is available.
    """
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        logger.warning("GEMINI_API_KEY is not set — AI features will be unavailable")
    return key


def get_env_var(name: str, default=None):
    return os.environ.get(name, default)
