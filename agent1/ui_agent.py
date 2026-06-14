"""
ui_agent.py — UI Blueprint Generation Agent for Project Brain
=============================================================
Reads the content blocks (written by ContentAgent) plus all Brain context,
then produces a comprehensive, mobile-first UI Blueprint that tells the
Code Agent *exactly* how to build each page:

  • Layout structure & component hierarchy
  • Colour tokens + typography scale
  • Animation / transition specifications
  • Responsive breakpoint rules (mobile-first)
  • Component interaction states (hover, focus, active)
  • Accessibility notes
  • Self-generated warnings for the Code Agent

The output is stored as a 'ui_blueprint' entity in the Brain so it persists
across sessions and is always reachable by the Code Agent.
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

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "brain"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

import orchestrator as brain

# ── Logging ───────────────────────────────────────────────────────────────────
logger = logging.getLogger("UIAgent")
if not logger.handlers:
    _log_path = ROOT / "brain" / "gemini_debug.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(_log_path, encoding="utf-8")
    _fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s -- %(message)s"))
    logger.addHandler(_fh)
    logger.setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# UI Agent
# ─────────────────────────────────────────────────────────────────────────────

class UIAgent:
    """
    UI Agent produces a detailed, mobile-first UI blueprint for each page
    based on the content blocks already written by the Content Agent.

    The blueprint is machine-readable JSON that the Code Agent consumes
    directly — no ambiguity, no placeholders.
    """

    _MODEL_PRIORITIES = [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
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
                logger.info(f"UIAgent initialised -- model: {self.model_name}")
            except Exception as e:
                logger.error(f"Failed to initialise Gemini client in UIAgent: {e}")
        else:
            logger.warning("UIAgent: No API key found.")

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

    # ─────────────────────────────────────────────────────────────────────────
    # Context builders
    # ─────────────────────────────────────────────────────────────────────────

    def _get_page_content_blocks(self, page_id: str) -> List[Dict[str, Any]]:
        """Return all content entities linked to the given page."""
        b = brain.get_brain()
        content_reg = b.get("content", {})
        blocks = []
        for cid, c in content_reg.items():
            if c.get("data", {}).get("page_id") == page_id:
                blocks.append({"content_id": cid, **c["data"]})
        return blocks

    def _get_existing_blueprints_summary(self) -> str:
        """Brief summary of UI blueprints already created (cross-page awareness)."""
        b = brain.get_brain()
        blueprints = b.get("ui_blueprints", {})
        if not blueprints:
            return "(no UI blueprints created yet)"
        lines = []
        for bpid, bp in blueprints.items():
            d = bp.get("data", {})
            lines.append(
                f"  - {bpid} | page={d.get('page_id')} | "
                f"theme={d.get('theme_tokens', {}).get('primary_color','?')} | "
                f"components={len(d.get('sections', []))}"
            )
        return "\n".join(lines)

    def _get_asset_map(self) -> str:
        """Compact map of all available assets with their URLs."""
        lines = []
        for aid, a in brain.list_assets().items():
            d = a["data"]
            lines.append(
                f"  - {aid} | file={d.get('original_filename')} | "
                f"url={d.get('url')} | status={d.get('status')}"
            )
        for gid, g in brain.list_generated().items():
            d = g["data"]
            lines.append(
                f"  - {gid} | type=generated | url={d.get('url')} | "
                f"status={d.get('status')}"
            )
        return "\n".join(lines) if lines else "(no assets available)"

    # ─────────────────────────────────────────────────────────────────────────
    # Main entry point
    # ─────────────────────────────────────────────────────────────────────────

    def generate_ui_blueprint(self, page_id: str) -> Dict[str, Any]:
        """
        Read the content blocks for *page_id* and produce a comprehensive
        UI blueprint stored in Brain as a 'ui_blueprint' entity.

        Returns a summary dict with success flag and the blueprint ID.
        """
        if not self.client:
            return {"success": False, "error": "Gemini client not initialised."}

        # 1. Fetch the page
        page = brain.get_entity("page", page_id)
        if not page:
            return {"success": False, "error": f"Page {page_id} not found."}

        page_data = page["data"]
        page_name = page_data.get("name", "Untitled Page")
        template   = page_data.get("template", "generic")
        route      = page_data.get("route", "/")

        # 2. Get content blocks for this page
        content_blocks = self._get_page_content_blocks(page_id)
        if not content_blocks:
            return {
                "success": False,
                "error": f"No content blocks found for page {page_id}. "
                          "Run the Content Agent first."
            }

        # 3. Build context
        context_summary    = brain.get_context_summary(max_memories=12, for_page_id=page_id)
        existing_blueprints = self._get_existing_blueprints_summary()
        asset_map          = self._get_asset_map()

        # Compact content block dump (avoid sending full payloads, extract essentials)
        content_summary_lines = []
        for cb in content_blocks:
            payload = cb.get("payload", {})
            sig     = payload.get("ui_signals", {})
            content_summary_lines.append(
                f"  [{cb['content_id']}] kind={cb.get('kind')} | "
                f"heading=\"{payload.get('heading','')[:60]}\" | "
                f"layout_hint={sig.get('layout_hint','?')} | "
                f"image_pos={sig.get('image_position','?')} | "
                f"accent={sig.get('theme_accent','?')} | "
                f"image_id={payload.get('image_id','-')} | "
                f"image_url={payload.get('image_url','-')} | "
                f"cta_link={payload.get('cta_link','-')}"
            )
        content_summary = "\n".join(content_summary_lines)

        # 4. System instruction
        system_instruction = (
            "You are a senior UI/UX architect and mobile-first design expert.\n"
            "Your role is to translate content blocks and layout signals into a PRECISE, "
            "machine-readable UI Blueprint that a Code Agent will use to generate production HTML/CSS/JS.\n"
            "Every decision you make must be specific and implementable — no vague descriptions.\n"
            "You must ensure visual consistency across all pages by referencing previously created blueprints.\n"
            "Design for mobile-first: define the mobile layout first, then tablet, then desktop.\n"
            "Inject self-generated Code Agent warnings where a section is complex or error-prone.\n"
        )

        # 5. User prompt
        user_prompt = f"""
You are designing the UI blueprint for the page: **{page_name}**
- Route: `{route}`
- Template: `{template}`
- Page ID: `{page_id}`

## Project Context & Memory
{context_summary}

## Previously Created UI Blueprints (for cross-page visual consistency)
{existing_blueprints}

## Available Image Assets
{asset_map}

## Content Blocks Written by Content Agent
{content_summary}

---

Generate a comprehensive UI Blueprint. Return ONLY a valid JSON object with this exact structure:

{{
  "page_id": "{page_id}",
  "page_name": "{page_name}",
  "route": "{route}",

  "theme_tokens": {{
    "primary_color": "#hex or CSS var",
    "secondary_color": "#hex or CSS var",
    "accent_color": "#hex or CSS var",
    "background_color": "#hex",
    "surface_color": "#hex",
    "text_primary": "#hex",
    "text_secondary": "#hex",
    "font_heading": "Font family name (Google Fonts preferred)",
    "font_body": "Font family name",
    "font_size_base": "16px",
    "border_radius": "8px",
    "shadow": "box-shadow CSS value",
    "transition_default": "all 0.25s ease"
  }},

  "breakpoints": {{
    "mobile": "max-width: 639px",
    "tablet": "640px - 1023px",
    "desktop": "min-width: 1024px"
  }},

  "global_layout": {{
    "max_content_width": "1280px",
    "navbar_height": "64px",
    "navbar_style": "sticky | fixed | static",
    "navbar_bg": "#hex or glassmorphism or transparent",
    "footer_style": "dark-band | minimal | full-column",
    "page_padding_mobile": "16px",
    "page_padding_desktop": "40px"
  }},

  "sections": [
    {{
      "content_id": "CONTENT-XXXX (must match a content block above)",
      "section_type": "hero | nav | card-grid | cta | text | gallery | footer",
      "component_tag": "section | header | nav | footer | article",

      "layout": {{
        "mobile": "stack | single-column | centered",
        "tablet": "two-column | side-by-side | centered",
        "desktop": "two-column-split | full-bleed | card-grid-3col",
        "height_mobile": "auto | 100vh | 60vh",
        "height_desktop": "auto | 80vh",
        "padding_mobile": "40px 16px",
        "padding_desktop": "80px 40px",
        "gap": "24px",
        "align_items": "center | flex-start | flex-end",
        "justify_content": "center | space-between | flex-start"
      }},

      "typography": {{
        "heading_tag": "h1 | h2 | h3",
        "heading_size_mobile": "2rem",
        "heading_size_desktop": "3.5rem",
        "heading_weight": "700 | 800",
        "heading_gradient": true,
        "body_size": "1rem",
        "body_line_height": "1.7",
        "cta_label": "Button text from content",
        "cta_href": "/route from content",
        "cta_style": "primary | secondary | ghost | outline"
      }},

      "image": {{
        "asset_id": "ASSET-XXXX or GEN-XXXX (from content block)",
        "url": "/assets/filename.webp (from content block)",
        "alt": "Descriptive alt text for accessibility",
        "position": "left | right | background | center | top",
        "object_fit": "cover | contain",
        "aspect_ratio": "16/9 | 1/1 | 4/3",
        "mobile_visible": true,
        "overlay": "none | dark-gradient | light-gradient",
        "border_radius": "0 | 12px | 50%",
        "animation": "fade-in | slide-in-right | zoom-in | none"
      }},

      "animation": {{
        "entrance": "fade-up | fade-in | slide-left | zoom-scale | none",
        "entrance_delay_ms": 0,
        "entrance_duration_ms": 600,
        "scroll_trigger": true,
        "hover_effect": "lift | glow | scale | underline | none",
        "hover_transition": "transform 0.3s ease, box-shadow 0.3s ease"
      }},

      "background": {{
        "type": "solid | gradient | image | glassmorphism | none",
        "value": "#hex or linear-gradient(...) or image URL",
        "blur": "0px | 12px (for glassmorphism)",
        "opacity": 1.0
      }},

      "components": [
        {{
          "type": "button | card | badge | tag | icon | divider",
          "label": "text",
          "variant": "filled | outlined | ghost",
          "color": "#hex or token reference",
          "border_radius": "8px",
          "padding": "12px 24px",
          "icon": "arrow-right | star | check | none",
          "hover_state": "background changes to #hex, slight scale 1.03"
        }}
      ],

      "accessibility": {{
        "aria_label": "Descriptive aria label for screen readers",
        "focus_ring": "2px solid #accent_color",
        "color_contrast_note": "ensure 4.5:1 contrast ratio for body text"
      }},

      "code_agent_warnings": [
        "List any specific implementation notes, tricky CSS, z-index issues, etc."
      ]
    }}
  ],

  "global_animations": {{
    "page_transition": "fade | slide | none",
    "scroll_reveal_library": "Intersection Observer API (native, no library needed)",
    "loading_skeleton": true,
    "reduce_motion_safe": true
  }},

  "code_agent_self_prompt": {{
    "critical_reminders": [
      "Always use semantic HTML5 elements",
      "All images must have descriptive alt text",
      "Test on 320px mobile viewport minimum",
      "Use CSS custom properties for all theme tokens",
      "Ensure all interactive elements have :focus-visible styles"
    ],
    "cross_page_consistency": "List any tokens or patterns that must match other pages",
    "known_risks": [
      "Any layout, z-index, or browser compatibility warnings the Code Agent should watch for"
    ]
  }}
}}

Rules:
1. Every `content_id` in sections MUST reference one of the real content block IDs listed above.
2. image URLs MUST come from the content block or asset map — never invent URLs.
3. Choose fonts from Google Fonts (Inter, Outfit, Plus Jakarta Sans, DM Sans, Syne are recommended).
4. Design theme tokens that are consistent with any previously created blueprints on this project.
5. Mobile layout ALWAYS stacks vertically. Desktop may split into columns.
6. Fill ALL fields — the Code Agent will fail if fields are missing.
7. Add `code_agent_warnings` for any section that is technically complex.
"""

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    temperature=0.15,   # Low temp = precise, consistent decisions
                )
            )

            if not response.text:
                return {"success": False, "error": "Empty response from Gemini."}

            blueprint_data = json.loads(response.text)

            # Ensure page_id is correctly set (model sometimes gets creative)
            blueprint_data["page_id"] = page_id
            blueprint_data["page_name"] = page_name

            # Save to Brain
            bp_id = brain.upsert_entity("ui_blueprint", blueprint_data)

            # Link blueprint back to the page entity
            page_entity = brain.get_entity("page", page_id)
            if page_entity:
                pdata = page_entity["data"]
                pdata["ui_blueprint_id"] = bp_id
                brain.upsert_entity("page", pdata, entity_id=page_id)

            logger.info(
                f"UIAgent: Blueprint {bp_id} generated for page {page_id} "
                f"({len(blueprint_data.get('sections', []))} sections)"
            )

            # Write a cross-page awareness memory for future agents
            theme = blueprint_data.get("theme_tokens", {})
            mem_content = (
                f"{bp_id} | page={page_id} | route={route} | "
                f"primary={theme.get('primary_color','?')} | "
                f"font_heading={theme.get('font_heading','?')} | "
                f"sections={len(blueprint_data.get('sections', []))} | "
                f"status=BLUEPRINT_READY"
            )
            from datetime import datetime, timezone
            brain.upsert_entity("memory", {
                "key":        "ui_blueprint_trace",
                "content":    mem_content,
                "importance": 4,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

            return {
                "success":      True,
                "blueprint_id": bp_id,
                "sections":     len(blueprint_data.get("sections", [])),
                "theme_tokens": theme,
            }

        except ClientError as e:
            logger.error(f"ClientError in UIAgent: {e}")
            return {"success": False, "error": f"Gemini API error: {e}"}
        except json.JSONDecodeError as e:
            logger.error(
                f"UIAgent: Failed to parse response as JSON: {e}\n"
                f"Response: {response.text[:500]}"
            )
            return {"success": False, "error": "Invalid JSON returned by Gemini."}
        except Exception as e:
            logger.error(f"Unexpected error in UIAgent: {e}")
            return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        pid = sys.argv[1]
        _api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        agent = UIAgent(api_key=_api_key)
        print(f"[*] Generating UI blueprint for page {pid}...")
        res = agent.generate_ui_blueprint(pid)
        if res.get("success"):
            print(f"[OK] Blueprint: {res['blueprint_id']} | Sections: {res['sections']}")
            print(f"     Theme: {res['theme_tokens']}")
        else:
            print(f"[FAILED] {res.get('error')}")
    else:
        print("Usage: python ui_agent.py <PAGE-ID>")
