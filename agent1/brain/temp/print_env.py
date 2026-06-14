import os
import sys
from pathlib import Path

ROOT = Path("c:/Users/USER/Desktop/agent1")

# Print current env
print("Before load_dotenv:")
print("GEMINI_API_KEY:", os.environ.get("GEMINI_API_KEY"))
print("GOOGLE_API_KEY:", os.environ.get("GOOGLE_API_KEY"))

# Try load_dotenv without override
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    print("\nAfter load_dotenv(ROOT / '.env') WITHOUT override:")
    print("GEMINI_API_KEY:", os.environ.get("GEMINI_API_KEY"))
    print("GOOGLE_API_KEY:", os.environ.get("GOOGLE_API_KEY"))
except Exception as e:
    print("Error loading without override:", e)

# Try load_dotenv with override=True
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
    print("\nAfter load_dotenv(ROOT / '.env', override=True):")
    print("GEMINI_API_KEY:", os.environ.get("GEMINI_API_KEY"))
    print("GOOGLE_API_KEY:", os.environ.get("GOOGLE_API_KEY"))
except Exception as e:
    print("Error loading with override:", e)
