"""
content_agent.py — Page Content Generation Agent for Project Brain
=================================================================
Reads the project context (pages, routes, assets, style memories)
and writes rich, SEO-optimised content blocks with embedded layout
signals for each section of a given page.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from google.genai.errors import ClientError

# Set up paths and load env
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "brain"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

import orchestrator as brain

logger = logging.getLogger("ContentAgent")
if not logger.handlers:
    _log_path = ROOT / "brain" / "gemini_debug.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(_log_path, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s"))
    logger.addHandler(_fh)
    logger.setLevel(logging.INFO)


class ContentAgent:
    """
    Content Agent writes highly structured page copy, assigns assets,
    and includes layout signals for the subsequent UI/Code generation phases.
    """

    _MODEL_PRIORITIES = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]

    def __init__(self, api_key: str = ""):
        self.api_key = (
            api_key.strip()
            or os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GOOGLE_API_KEY", "")
        )
        self.client = None
        self.model_name = "gemini-2.0-flash"
        
        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
                self.model_name = self._pick_model() or "gemini-2.0-flash"
                logger.info(f"ContentAgent initialised — model: {self.model_name}")
            except Exception as e:
                logger.error(f"Failed to initialise Gemini client in ContentAgent: {e}")
        else:
            logger.warning("ContentAgent: No API key found.")

    def _pick_model(self) -> Optional[str]:
        try:
            all_models = list(self.client.models.list())
            available = [m.name for m in all_models if "imagen" not in m.name.lower()]
            for p in self._MODEL_PRIORITIES:
                for name in available:
                    if name.split("/")[-1] == p:
                        return name
            return available[0] if available else None
        except Exception:
            return None

    def generate_page_content(self, page_id: str) -> Dict[str, Any]:
        """
        Gathers context for the target page, calls Gemini to write the copy,
        and saves the resulting content blocks to the Brain.
        """
        if not self.client:
            return {"success": False, "error": "Gemini client not initialised."}

        # 1. Fetch page details
        page = brain.get_entity("page", page_id)
        if not page:
            return {"success": False, "error": f"Page {page_id} not found."}
        
        page_data = page["data"]
        page_name = page_data.get("name", "Untitled Page")
        template = page_data.get("template", "generic")
        route = page_data.get("route", "/")

        # 2. Get scoped context (excluding this page's plan to focus on surroundings)
        context_summary = brain.get_context_summary(max_memories=10, for_page_id=page_id)

        # 3. Compile all assets and asset_ref memories
        assets = brain.list_assets()
        generated_assets = brain.list_generated()
        memories = brain.list_memories()
        
        asset_refs = []
        for m in memories.values():
            if m.get("data", {}).get("key") == "asset_ref":
                asset_refs.append(m["data"]["content"])

        # Create asset context summary
        asset_ctx = []
        for aid, a in assets.items():
            d = a["data"]
            asset_ctx.append(f"- {aid} | file={d.get('original_filename')} | url={d.get('url')} | status={d.get('status')}")
        for gid, g in generated_assets.items():
            d = g["data"]
            asset_ctx.append(f"- {gid} | prompt={d.get('prompt')[:40]} | url={d.get('url')} | status={d.get('status')}")

        asset_summary_text = "\n".join(asset_ctx) if asset_ctx else "(no assets registered yet)"
        asset_refs_text = "\n".join([f"  * {ref}" for ref in asset_refs]) if asset_refs else "  * (no analyzed/generated asset_ref memories yet)"

        # 4. Construct prompt
        system_instruction = (
            "You are a professional web content copywriter and senior SEO strategist.\n"
            "Your job is to write high-quality copy (headings, text body, and CTA button labels) "
            "for website sections. Do not use placeholders or Lorem Ipsum — write real, engaging copy.\n"
            "You must also assign available assets (by ID and URL) to appropriate sections "
            "and decide on structural layout hints (ui_signals) to guide the subsequent UI generation phase."
        )

        user_prompt = f"""
You are writing content for the page: **{page_name}**
- Route: `{route}`
- Template Type: `{template}`

Here is the overall website blueprint and memories:
{context_summary}

Here are the registered image assets in the project:
{asset_summary_text}

Here are the visual description memory traces for those assets:
{asset_refs_text}

Based on this, generate the structured page sections. Return ONLY a valid JSON object matching this structure:
{{
  "sections": [
    {{
      "section_type": "hero | text | gallery | cta | card | nav | footer",
      "payload": {{
        "heading": "Catchy main title",
        "subheading": "Supporting subheading",
        "body": "High-quality paragraph or descriptive text (no lorem ipsum)",
        "cta_text": "Action button text",
        "cta_link": "/about",
        "image_id": "ASSET-0001",
        "image_url": "/assets/hero.webp",
        "ui_signals": {{
          "layout_hint": "two-column-split | centered-hero | card-grid | side-by-side",
          "image_position": "left | right | center | background",
          "theme_accent": "accent-gradient | minimal | dark-bg",
          "alignment": "left | center | right"
        }}
      }}
    }}
  ]
}}

Rules:
1. Write 3-5 sections suitable for the '{template}' template.
2. For button links (`cta_link`), map them to one of the active project routes from the context summary (e.g. use actual routes like /contact, /about, / instead of # or external links unless appropriate).
3. Select appropriate image assets that match the section purpose from the registered assets list. If an image is not analyzed yet, you can still reference its url.
4. Keep the copy aligned with the website theme hints.
"""

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    temperature=0.2
                )
            )
            
            if not response.text:
                return {"success": False, "error": "Empty response from Gemini."}

            content_data = json.loads(response.text)
            sections = content_data.get("sections", [])
            
            created_ids = []
            for i, sec in enumerate(sections):
                sect_type = sec.get("section_type", "text")
                payload = sec.get("payload", {})
                
                content_id = brain.upsert_entity("content", {
                    "page_id": page_id,
                    "section": f"section_{i+1}",
                    "kind": sect_type,
                    "payload": payload,
                    "status": "DRAFT"
                })
                created_ids.append(content_id)

                # Link content ID back to the page
                page_entity = brain.get_entity("page", page_id)
                if page_entity:
                    pdata = page_entity["data"]
                    if content_id not in pdata.get("sections", []):
                        pdata.setdefault("sections", []).append(content_id)
                        brain.upsert_entity("page", pdata, entity_id=page_id)
            
            logger.info(f"Generated {len(created_ids)} content blocks for page {page_id}")
            return {"success": True, "content_ids": created_ids}

        except ClientError as e:
            logger.error(f"ClientError during content generation: {e}")
            return {"success": False, "error": f"Gemini API error: {e}"}
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini response as JSON: {e}\nResponse text: {response.text}")
            return {"success": False, "error": "Invalid JSON returned by Gemini."}
        except Exception as e:
            logger.error(f"Unexpected error in ContentAgent: {e}")
            return {"success": False, "error": str(e)}


if __name__ == "__main__":
    # Small test runner if run directly
    if len(sys.argv) > 1:
        pid = sys.argv[1]
        _api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        agent = ContentAgent(api_key=_api_key)
        print(f"[*] Generating content for {pid}...")
        res = agent.generate_page_content(pid)
        print(f"[Result]: {res}")
    else:
        print("Usage: python content_agent.py <PAGE-ID>")
