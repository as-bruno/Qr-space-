"""
visual.py — Image Analysis Agent for Project Brain
====================================================
Analyses any image uploaded by the user and returns a structured
description that downstream AI agents (Design, Content, Code) can
consume directly from the Project Brain asset registry.

Output schema (always returned under 'data'):
    subject         — what the image actually shows
    description     — a rich, agent-usable sentence description
    visual_style    — e.g. "photorealistic", "flat illustration", "abstract"
    mood            — e.g. "calm", "energetic", "professional", "playful"
    dominant_colors — list of up to 5 dominant colour names / hex hints
    keywords        — list of search/generation keywords
    suggested_use   — how this image could be used on a website
                      e.g. "hero background", "team photo", "product shot"
"""

import json
import logging
import os
import re
import sys
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image

# Load local environment variables from .env if present (for standalone runs)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__, override=True).parent / ".env")
except ImportError:
    pass

# ── Logging ───────────────────────────────────────────────────────────────────
logger = logging.getLogger("VisualRecognizer")
if not logger.handlers:
    _log_path = Path(__file__).parent / "brain" / "visual_debug.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(_log_path, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_fh)
    logger.setLevel(logging.INFO)


# ── Analysis prompt ───────────────────────────────────────────────────────────
_ANALYSIS_PROMPT = """
You are an AI image analysis assistant for a website generation platform.
Analyse the provided image and return ONLY a valid JSON object — no markdown, no extra text.

The JSON must match this exact structure:
{
  "subject": "A concise label for the main subject (e.g. 'mountain landscape', 'smiling woman at desk', 'abstract gradient')",
  "description": "One rich sentence (20–40 words) describing the image in enough detail for another AI to recreate or repurpose it for a website.",
  "visual_style": "One of: photorealistic | illustration | flat design | abstract | 3D render | diagram | screenshot | mixed",
  "mood": "One of: professional | calm | energetic | playful | dramatic | minimal | luxurious | friendly | dark | warm",
  "dominant_colors": ["color1", "color2", "color3"],
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
  "suggested_use": "One of: hero background | section background | product shot | team photo | icon | testimonial | blog thumbnail | decorative | logo"
}

Rules:
- Be specific and descriptive — your output will be read by AI code and design agents, not humans.
- dominant_colors should be colour names or hex codes (e.g. "deep navy #1a2e4a").
- keywords must be useful for image search and AI image generation prompts.
- Do not add any fields beyond the seven listed above.
"""


class VisualRecognizer:
    """
    Analyses images using the best available Gemini multimodal model.
    Returns structured metadata consumed directly by Project Brain agents.
    """

    # Newest → oldest: always use the best model available on the account
    _MODEL_PRIORITIES = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-2.0-flash-exp",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash",
        "gemini-1.5-pro-latest",
        "gemini-1.5-pro",
        "gemini-pro-vision",
    ]

    def __init__(self, api_key: str = ""):
        """
        Parameters
        ----------
        api_key : str
            Gemini API key. Falls back to GEMINI_API_KEY / GOOGLE_API_KEY env vars.
        """
        self.api_key = (
            api_key.strip()
            or os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GOOGLE_API_KEY", "")
        )
        self.client = None
        self.model_name = None
        self._init_client()

    # ── Initialisation ────────────────────────────────────────────────────────

    def _init_client(self) -> None:
        if not self.api_key:
            logger.error("No API key provided. VisualRecognizer is disabled.")
            return
        try:
            self.client = genai.Client(api_key=self.api_key)
            self.model_name = self._pick_model()
            if not self.model_name:
                logger.critical("No compatible Gemini model found. Check API key permissions.")
                self.client = None
            else:
                logger.info(f"VisualRecognizer ready — model: {self.model_name}")
        except Exception as exc:
            logger.critical(f"Failed to initialise Gemini client: {exc}")
            self.client = None

    def _pick_model(self) -> str | None:
        """
        Returns the best available generateContent-capable model name,
        compatible with both old and new Google GenAI SDK versions.
        """
        try:
            all_models = list(self.client.models.list())

            def _can_generate(m) -> bool:
                # New SDK: supported_generation_methods
                methods = getattr(m, "supported_generation_methods", None)
                if methods is not None:
                    return "generateContent" in methods
                # Old SDK: supported_actions
                actions = getattr(m, "supported_actions", None)
                if actions is not None:
                    return "generateContent" in actions
                # Unknown SDK version: exclude imagen, include everything else
                return "imagen" not in m.name.lower()

            available = [m.name for m in all_models if _can_generate(m)]
            logger.info(f"Found {len(available)} generateContent-capable models.")

            for priority in self._MODEL_PRIORITIES:
                for name in available:
                    if name.split("/")[-1] == priority:
                        return name

            # Fallback to first gemini model found
            gemini = [n for n in available if "gemini" in n.lower()]
            chosen = gemini[0] if gemini else (available[0] if available else None)
            if chosen:
                logger.warning(f"No priority model available. Falling back to: {chosen}")
            return chosen

        except Exception as exc:
            logger.error(f"Model discovery failed: {exc}")
            return None

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze_image(self, image_path: str) -> dict:
        """
        Analyse an image file and return structured metadata.

        Returns
        -------
        dict
            On success:  {"success": True,  "data": { ...7 fields... }}
            On failure:  {"success": False, "error": "<reason>"}
        """
        if not self.client or not self.model_name:
            return {"success": False,
                    "error": "VisualRecognizer not initialised — check API key."}

        if not os.path.exists(image_path):
            return {"success": False, "error": f"File not found: {image_path}"}

        img = None
        try:
            img = Image.open(image_path)

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[_ANALYSIS_PROMPT, img],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=1024,
                    safety_settings=[
                        types.SafetySetting(
                            category="HARM_CATEGORY_HARASSMENT",
                            threshold="BLOCK_ONLY_HIGH"),
                        types.SafetySetting(
                            category="HARM_CATEGORY_HATE_SPEECH",
                            threshold="BLOCK_ONLY_HIGH"),
                        types.SafetySetting(
                            category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            threshold="BLOCK_ONLY_HIGH"),
                        types.SafetySetting(
                            category="HARM_CATEGORY_DANGEROUS_CONTENT",
                            threshold="BLOCK_ONLY_HIGH"),
                    ],
                ),
            )

            # Safety block check
            candidate = response.candidates[0] if response.candidates else None
            if candidate and str(candidate.finish_reason) == "SAFETY":
                reasons = []
                for r in (candidate.safety_ratings or []):
                    if str(r.probability) in ("HIGH", "MEDIUM"):
                        reasons.append(f"{r.category}: {r.probability}")
                feedback = ", ".join(reasons) or "unspecified safety violation"
                return {"success": False,
                        "error": f"Blocked by safety filter: {feedback}",
                        "critical_stop": True}

            # Parse JSON
            raw = response.text.strip()
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                logger.error("No JSON object found in model response.")
                return {"success": False, "error": "No JSON in response"}

            data = json.loads(match.group(0))

            # Normalise keys to lowercase
            data = {k.lower(): v for k, v in data.items()}

            # Guarantee keywords is always a list
            kw = data.get("keywords", [])
            if isinstance(kw, str):
                data["keywords"] = [k.strip() for k in re.split(r"[,\s]+", kw) if k.strip()]
            elif not isinstance(kw, list):
                data["keywords"] = []

            # Guarantee dominant_colors is always a list
            dc = data.get("dominant_colors", [])
            if isinstance(dc, str):
                data["dominant_colors"] = [c.strip() for c in dc.split(",") if c.strip()]
            elif not isinstance(dc, list):
                data["dominant_colors"] = []

            logger.info(
                f"Analysed '{os.path.basename(image_path)}' → "
                f"{data.get('visual_style','?')} / {data.get('mood','?')}"
            )
            return {"success": True, "data": data}

        except json.JSONDecodeError as exc:
            logger.error(f"JSON parse failed: {exc}")
            return {"success": False, "error": f"JSON parse error: {exc}"}

        except Exception as exc:
            logger.error(f"Unexpected error analysing '{image_path}': {exc}")
            return {"success": False, "error": str(exc)}

        finally:
            if img:
                img.close()


# ─────────────────────────────────────────────────────────────────────────────
# Standalone CLI — test outside the Brain
# Usage:  python visual.py <image_path>
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python visual.py <image_path>")
        sys.exit(1)

    recognizer = VisualRecognizer()           # reads key from env automatically
    if not recognizer.client:
        print("[ERROR] Could not initialise — set GEMINI_API_KEY in your environment.")
        sys.exit(1)

    result = recognizer.analyze_image(sys.argv[1])

    if result["success"]:
        print(json.dumps(result["data"], indent=2, ensure_ascii=False))
    else:
        print(f"[FAILED] {result['error']}")
        sys.exit(1)
