import os
import sys
import time
import logging
import json
from pathlib import Path
from google import genai
from google.genai import types
from PIL import Image
from google.genai.errors import ClientError, ServerError

# Load local environment variables from .env if present (for standalone runs)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__, override=True).parent / ".env")
except ImportError:
    pass

# ── Logging ───────────────────────────────────────────────────────────────────
logger = logging.getLogger("GeminiGenerator")
if not logger.handlers:
    _log_path = Path(__file__).parent / "brain" / "gemini_debug.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(_log_path, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s"))
    logger.addHandler(_fh)
    logger.setLevel(logging.INFO)

class GeminiGenerator:
    """
    Image generation backend — Gemini Flash (vision/analysis) + Imagen 3 (generation).
    Used by Project Brain's IMAGEN tool-request queue via agent_bridge.py.
    """

    # Neutral system context injected into every generation prompt
    SYSTEM_PROMPT = (
        "You are a world-class AI image generation assistant. "
        "Your purpose is to create stunning, cinematic, extremely high-quality images "
        "that are ready for professional website use. "
        "When given a reference image, analyse and adapt it faithfully while respecting "
        "the user's modification instructions."
    )

    # Default temp folder relative to this file (overridden by caller)
    _DEFAULT_TEMP = Path(__file__).parent / "brain" / "temp"

    def __init__(self, api_key: str, temp_folder: str = ""):
        self.api_key = api_key
        self.temp_folder = temp_folder or str(self._DEFAULT_TEMP)
        Path(self.temp_folder).mkdir(parents=True, exist_ok=True)

        self.client = None
        self.model_name = os.environ.get("GEMINI_IMAGE_MODEL", "imagen-3.0-generate-001")

        if self.api_key:
            self.api_key = self.api_key.strip('"').strip("'")
            try:
                masked = (f"{self.api_key[:4]}...{self.api_key[-4:]}"
                          if len(self.api_key) > 8 else "INVALID")
                self.client = genai.Client(api_key=self.api_key)
                logger.info(f"GeminiGenerator initialised. Key: {masked}")
                valid = self._get_valid_model()
                if valid:
                    self.model_name = valid
            except Exception as e:
                logger.error(f"Failed to initialise Gemini client: {e}")
        else:
            logger.warning("GeminiGenerator: GEMINI_API_KEY is missing.")

    def _prepare_image(self, image_input):
        """
        Validates, optimizes (resizes/compresses), and prepares the image for the Gemini API.
        Accepts file path or PIL Image object.
        """
        try:
            img = None
            if isinstance(image_input, str):
                if os.path.exists(image_input):
                    img = Image.open(image_input)
                    img.load() # Ensure data is loaded into memory
                else:
                    logger.error(f"Reference image path not found: {image_input}")
                    return None
            elif isinstance(image_input, Image.Image):
                img = image_input
            else:
                return None

            # Optimization: Resize if dimensions are excessive to ensure solid connection/speed
            max_dimension = 1536
            if img.width > max_dimension or img.height > max_dimension:
                img.thumbnail((max_dimension, max_dimension))
                logger.info(f"Resized reference image to {img.size} for optimization.")

            # Ensure compatible mode
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            
            return img
        except Exception as e:
            logger.error(f"Error preparing reference image: {e}") # Log the error
            return None

    def _get_valid_model(self):
        """
        Find the best available Imagen model. Priority: Imagen 3 > Imagen 2.
        """
        if not self.client:
            return None
        try:
            logger.info("Searching for Imagen models...")
            all_models = list(self.client.models.list())
            imagen_models = [m.name for m in all_models if "imagen" in m.name.lower()]

            if not imagen_models:
                logger.warning("No Imagen models found. Using env default.")
                return None

            logger.info(f"Found Imagen models: {imagen_models}")
            priorities = [
                "imagen-3.0-generate-001",
                "imagen-3.0-generate-002",
                "imagen-3",
                "imagen-2",
            ]
            for p in priorities:
                for m_name in imagen_models:
                    if m_name.endswith(p):
                        logger.info(f"Selected Imagen model: {m_name}")
                        return m_name
            return imagen_models[0]
        except ClientError as e:
            logger.error(f"Error listing Imagen models: {e}")
            return None

    def _get_vision_model(self):
        """
        Find the best available Gemini vision/text model for prompt analysis.
        Uses the same SDK-agnostic approach as VisualRecognizer.
        """
        if not self.client:
            return "gemini-2.0-flash"
        try:
            all_models = list(self.client.models.list())
            PRIORITIES = [
                "gemini-2.5-flash",
                "gemini-2.5-pro",
                "gemini-2.0-flash",
                "gemini-2.0-flash-exp",
                "gemini-2.0-flash-lite",
                "gemini-1.5-flash-latest",
                "gemini-1.5-flash",
                "gemini-1.5-pro",
            ]

            def _supports_generate(m) -> bool:
                methods = getattr(m, "supported_generation_methods", None)
                if methods is not None:
                    return "generateContent" in methods
                actions = getattr(m, "supported_actions", None)
                if actions is not None:
                    return "generateContent" in actions
                return "imagen" not in m.name.lower()

            available = [m.name for m in all_models if _supports_generate(m)]
            for p in PRIORITIES:
                for m_name in available:
                    if m_name.split("/")[-1] == p:
                        return m_name
            return available[0] if available else "gemini-2.0-flash"
        except Exception:
            return "gemini-2.0-flash"

    def _analyze_image_for_prompt(self, image_input, user_prompt):
        """
        Uses Gemini 1.5 Flash to analyze the reference image and merge it with the user prompt.
        """
        try:
            vision_model = self._get_vision_model()
            # Use 'gemini-1.5-flash' for analysis as it is multimodal
            analysis_prompt = f"You are an expert image generation prompt engineer. Analyze this image in extreme detail (subject, style, lighting, composition). The user wants to generate a NEW image based on this one with the instruction: '{user_prompt}'. If the instruction is generic (e.g. 'similar', 'like this'), focus on describing the image to recreate it. Output ONLY a single, highly detailed, optimized prompt for Imagen 3."
            
            response = self.client.models.generate_content(
                model=vision_model,
                contents=[analysis_prompt, image_input]
            )
            
            if response.text:
                logger.info("Gemini Vision analysis successful.")
                return response.text
            return user_prompt
        except ClientError as e: # Catch specific ClientError
            logger.error(f"Vision analysis failed: {e}")
            return user_prompt

    def generate_image(self, prompt, aspect_ratio="1:1", user_id="anon", reference_image_path=None):
        """
        Generates or modifies an image using Imagen 3 via the Gemini API.
        """
        if not self.client:
            return {"success": False, "error": "Gemini client not initialized. Check API Key."}

        if not prompt or not isinstance(prompt, str):
             logger.error(f"Strict Analysis: Invalid prompt provided by user {user_id}.")
             return {"success": False, "error": "INVALID_PROMPT"}

        logger.info(f"Gemini generating for user {user_id} | Ratio: {aspect_ratio} | Ref Image: {bool(reference_image_path)}")

        try:
            # Map frontend aspect ratios to Imagen supported values
            # Imagen 3 supports: "1:1", "9:16", "16:9", "4:3", "3:4"
            ratio_map = {
                "1:1": "1:1",
                "16:9": "16:9",
                "9:16": "9:16",
                "4:5": "3:4" # Closest match for Social
            }
            imagen_ratio = ratio_map.get(aspect_ratio, "1:1")

            # "Gemini Nano Banana 4K" Branding & System Context:
            # Since Imagen 3 doesn't take a system_instruction parameter like text models,
            # we prepend the context to the prompt and enhance it.
            full_prompt = f"{self.SYSTEM_PROMPT}\n\nUser Request: {prompt}, extremely high quality, cinematic lighting, shot on 35mm lens, highly detailed textures, professional photography"

            image_input = None
            # Strict Analysis: Validate and prepare reference image if provided
            if reference_image_path:
                image_input = self._prepare_image(reference_image_path)
                if image_input:
                    logger.info(f"Using prepared reference image for generation.")
                    # Analyze image with Gemini Flash to get a descriptive prompt
                    analyzed_prompt = self._analyze_image_for_prompt(image_input, prompt)
                    if analyzed_prompt:
                        # Update full_prompt with the analysis
                        full_prompt = f"{self.SYSTEM_PROMPT}\n\nEnhanced Prompt based on Image Analysis: {analyzed_prompt}"
                else:
                    logger.error("Strict Analysis: Failed to validate/prepare reference image.")
                    return {"success": False, "error": "INVALID_REFERENCE_IMAGE"}

            # Retry logic for API call (Handles connection drops/timeouts)
            response = None
            for attempt in range(3):
                try:
                    response = self.client.models.generate_images(
                        model=self.model_name,
                        prompt=full_prompt,
                        config=types.GenerateImagesConfig(
                            number_of_images=1,
                            aspect_ratio=imagen_ratio,
                            output_mime_type="image/png"
                        )
                    )
                    break # Success, exit loop
                except Exception as e:
                    err_msg = str(e).lower()
                    # Retry on connection drops, timeouts, or server errors
                    if attempt < 2 and ("disconnected" in err_msg or "connection" in err_msg or "timeout" in err_msg or "500" in err_msg or "503" in err_msg):
                        logger.warning(f"Gemini API connection issue: {e}. Retrying ({attempt+1}/3)...")
                        time.sleep(2)
                        continue
                    raise e # Re-raise if not retryable or max retries reached

            if not response or not response.generated_images:
                logger.warning("Strict Analysis: Gemini generation returned empty response (likely safety block).")
                return {"success": False, "error": "PROMPT_BLOCKED_SAFETY_OR_EMPTY"}

            timestamp = int(time.time())
            filename = f"gemini_{user_id}_{timestamp}.png"
            filepath = os.path.join(self.temp_folder, filename)

            # Save the first generated image
            generated_image = response.generated_images[0]
            
            # The SDK might return an Image object or raw bytes depending on version
            # Usually it's image.image_bytes or you can use .save() if it's a PIL object
            try:
                # If it's the newest SDK, .image has the data
                with open(filepath, "wb") as f:
                    f.write(generated_image.image.image_bytes)
            except AttributeError:
                # Fallback if it's already a PIL-compatible object
                generated_image.image.save(filepath)

            logger.info(f"Gemini image saved: {filename}")
            return {"success": True, "filename": filename}

        except ClientError as e:
            # 4xx Errors (Bad Request, Safety, Quota)
            error_msg = str(e)
            logger.error(f"Strict Analysis - Client Error: {error_msg}")
            
            if "expired" in error_msg.lower() or "key_invalid" in error_msg.upper():
                logger.critical("CRITICAL: API Key is expired or invalid. Please update .env and RESTART the server.") # Log critical error
            
            if "429" in error_msg or "quota" in error_msg.lower():
                return {"success": False, "error": "API_QUOTA_EXCEEDED"}
            if "safety" in error_msg.lower() or "blocked" in error_msg.lower():
                return {"success": False, "error": "PROMPT_BLOCKED_SAFETY", "critical_stop": True}
            
            return {"success": False, "error": f"GENERATION_CLIENT_ERROR: {error_msg}"}
            
        except ServerError as e:
            # 5xx Errors (Google side)
            logger.error(f"Strict Analysis - Server Error: {e}")
            return {"success": False, "error": "GOOGLE_SERVER_ERROR"}

        except Exception as e: # Catch generic exceptions
            error_msg = str(e)
            logger.error(f"Strict Analysis - Unexpected Error: {error_msg}")
            return {"success": False, "error": "UNEXPECTED_GENERATION_ERROR"}

    def edit_image(self, prompt, reference_image_path, aspect_ratio="1:1", user_id="anon"):
        """
        Alias for generate_image with specialised instructions for modification.
        """
        mod_prompt = (
            f"IMAGE MODIFICATION TASK: {prompt}. "
            "Please modify the provided image according to this request "
            "while maintaining professional quality suitable for a website."
        )
        return self.generate_image(mod_prompt, aspect_ratio, user_id, reference_image_path)


# ─────────────────────────────────────────────────────────────────────────────
# Standalone CLI — for testing outside the Brain
# Usage: python gemini_generator.py "a sunset over the ocean" 16:9
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    _prompt = sys.argv[1] if len(sys.argv) > 1 else "A clean hero banner for a modern SaaS website"
    _ratio  = sys.argv[2] if len(sys.argv) > 2 else "16:9"

    _api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
    if not _api_key:
        print("[ERROR] Set GEMINI_API_KEY in your environment.")
        sys.exit(1)

    gen = GeminiGenerator(api_key=_api_key)
    print(f"[*] Generating: '{_prompt}' @ {_ratio} using {gen.model_name}")
    result = gen.generate_image(prompt=_prompt, aspect_ratio=_ratio, user_id="cli_test")

    if result["success"]:
        print(f"[+] Saved: {result['filename']}")
        print(f"    Path : {gen.temp_folder}/{result['filename']}")
    else:
        print(f"[-] Failed: {result['error']}")
        sys.exit(1)
