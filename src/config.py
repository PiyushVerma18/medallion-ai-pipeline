import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://pipeline_user:pipeline_password@localhost:5432/ticket_warehouse"
)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_client = None

def get_gemini_client():
    """Lazily initialize the Gemini client only when an agent needs it."""
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY is not set. Please add it to your .env file."
            )
        from google import genai
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client