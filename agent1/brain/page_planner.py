"""
page_planner.py — Stage 1 Page planning agent for Project Brain
===============================================================
This planner runs prior to page building to structure specific layout,
navigation mappings, and content goals using Gemini AI.
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from google.genai.errors import ClientError

# Set up paths
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "brain"))

import orchestrator as brain

logger = logging.getLogger("PagePlanner")
if not logger.handlers:
    _log_path = ROOT / "brain" / "gemini_debug.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(_log_path, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s"))
    logger.addHandler(_fh)
    logger.setLevel(logging.INFO)


class PagePlanner:
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
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY", "")
        )
        self.client = None
        self.model_name = "gemini-2.0-flash"
        
        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
                self.model_name = self._pick_model() or "gemini-2.0-flash"
            except Exception as e:
                logger.error(f"Failed to initialise Gemini client in PagePlanner: {e}")

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

    def generate_page_plan(self, page_id: str) -> Dict[str, Any]:
        """
        Produce a detailed plan for page_id, combining global site intent,
        navigation nodes, and memories of previously built pages.
        """
        if not self.client:
            return {"success": False, "error": "Gemini client not initialised."}

        # 1. Fetch page data
        page = brain.get_entity("page", page_id)
        if not page:
            return {"success": False, "error": f"Page {page_id} not found."}

        page_data = page["data"]
        page_name = page_data.get("name", "Page")
        route = page_data.get("route", "/")
        goal = page_data.get("goal", "")
        template = page_data.get("template", "landing")

        # 2. Get site structure
        brain_state = brain.get_brain()
        site_struct = brain_state.get("site_structure", {})
        project_name = site_struct.get("project_name", "Untitled")
        theme_hints = site_struct.get("theme", {}).get("hints", [])
        brand_guide = site_struct.get("brand", {})

        # 3. Compile nav links for other pages
        nav_nodes = brain_state.get("navigation_graph", {}).get("nodes", [])
        other_routes = [n.get("route") for n in nav_nodes if n.get("route") != route]

        # 4. Gather memories (especially page traces, built page outputs, and visual styles)
        memories = brain.list_memories()
        memory_lines = []
        for mid, m in memories.items():
            d = m.get("data", {})
            key = d.get("key", "")
            content = d.get("content", "")
            if key in ("page_build_trace", "ui_blueprint_trace", "code_output_trace", "visual_style", "session_overview"):
                memory_lines.append(f"- [{key}]: {content}")
        memories_text = "\n".join(memory_lines) if memory_lines else "(no memories recorded yet)"

        # 5. Build prompt
        system_instruction = (
            "You are a lead web designer and site planner.\n"
            "Your task is to details a specific page's sections, component requirements, "
            "navigation goals, asset needs, and interactive layout signals.\n"
            "This plan will guide content copywriters, UI architects, and frontend developers.\n"
            "Ensure consistency with existing site branding and built pages (if any).\n"
            "Response MUST be a JSON object matching the requested schema."
        )

        user_prompt = f"""
Plan the detailed structure for the page: **{page_name}**
- Route: `{route}`
- Template Kind: `{template}`
- Goal: {goal}

---
### Global Project Context:
- Project Name: {project_name}
- Theme Hints: {theme_hints}
- Branding Guide: {brand_guide}
- Navigation routes to link to: {other_routes}

### History and Memories (to maintain style consistency):
{memories_text}

---
Generate a deep specification plan for this page. Return a JSON object matching this structure:
{{
  "page_id": "{page_id}",
  "sections_plan": [
    {{
      "section_type": "nav | hero | features | cards | text | gallery | cta | footer",
      "heading_goal": "A clear, descriptive heading concept for this section",
      "content_spec": "Details on copy requirements, key features to mention, and target message",
      "asset_intent": "Instructions on what assets to use (e.g. use GEN-0001, or describe a generated image needed here)",
      "cta_link_target": "The route that this section button should link to (must be one of: {other_routes} or '/' or external)"
    }}
  ],
  "interactivity_plan": {{
    "features": ["scroll reveal animations", "hover transitions", "responsive menus", "pricing toggle", etc.],
    "custom_elements": "e.g. typing effect in hero, light/dark mode switch, testimonial slider"
  }},
  "style_cohesion_notes": "Instructions on color variables, typography, and styling choices to match previous pages"
}}
"""

        try:
            print(f"[PLANNER] Running Stage 1 Detailed Page Planning for {page_name} ({page_id})...")
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    temperature=0.15
                )
            )

            if not response.text:
                raise ValueError("Empty response received from Gemini.")

            plan_data = json.loads(response.text)

            # Store in brain as a memory
            plan_key = f"detailed_page_plan_{page_id}"
            brain.upsert_entity("memory", {
                "key": plan_key,
                "content": json.dumps(plan_data, ensure_ascii=False),
                "importance": 4,
                "created_at": datetime.now(timezone.utc).isoformat()
            })

            # Update page status to PLAN_DETAILED
            pdata = page_data.copy()
            pdata["status"] = "PLAN_DETAILED"
            brain.upsert_entity("page", pdata, entity_id=page_id)

            # Log to console
            print(f"\n=======================================================")
            print(f"STAGE 1 DEEP PAGE PLAN FOR: {page_name} ({page_id})")
            print(f"=======================================================")
            print(f"Style cohesion notes: {plan_data.get('style_cohesion_notes')}")
            print(f"Sections plan:")
            for s in plan_data.get("sections_plan", []):
                print(f"  - [{s.get('section_type').upper()}] {s.get('heading_goal')}")
                print(f"    * Spec: {s.get('content_spec')}")
                print(f"    * Asset intent: {s.get('asset_intent')}")
                print(f"    * CTA Links to: {s.get('cta_link_target')}")
            print(f"Interactivity:")
            print(f"  * Features: {plan_data.get('interactivity_plan', {}).get('features')}")
            print(f"  * Custom elements: {plan_data.get('interactivity_plan', {}).get('custom_elements')}")
            print(f"=======================================================\n")

            return {"success": True, "plan": plan_data}

        except Exception as e:
            print(f"[PLANNER] Error running detailed planner for {page_id}: {e}")
            logger.error(f"Detailed planning error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        pid = sys.argv[1]
        planner = PagePlanner()
        res = planner.generate_page_plan(pid)
        print(res)
    else:
        print("Usage: python page_planner.py <PAGE-ID>")
