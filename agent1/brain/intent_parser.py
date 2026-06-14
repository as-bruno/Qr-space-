"""
Intent Parser — Natural Language → Project Brain Updates
=========================================================
Translates a plain-English user request into structured Project Brain
mutations and tool-request queues using Gemini AI.

This is the Stage 0 entry-point for website generation.
"""

import os
import sys
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from pathlib import Path

from google import genai
from google.genai import types
from google.genai.errors import ClientError

# Set up paths and load env
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "brain"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

import orchestrator as brain

logger = logging.getLogger("IntentParser")
if not logger.handlers:
    _log_path = ROOT / "brain" / "gemini_debug.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(_log_path, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s"))
    logger.addHandler(_fh)
    logger.setLevel(logging.INFO)


@dataclass
class PageIntent:
    name: str
    route: str
    template: str = "generic"
    goal: str = ""
    is_home: bool = False

@dataclass
class AssetIntent:
    kind: str             # "uploaded" | "generated"
    filename: Optional[str] = None
    local_path: Optional[str] = None
    gen_prompt: Optional[str] = None
    aspect_ratio: str = "16:9"

@dataclass
class ParsedIntent:
    project_name: str = "Untitled Project"
    pages: List[PageIntent] = field(default_factory=list)
    uploaded_assets: List[AssetIntent] = field(default_factory=list)
    generated_assets: List[AssetIntent] = field(default_factory=list)
    theme_hints: List[str] = field(default_factory=list)
    branding_guide: Dict[str, Any] = field(default_factory=dict)
    raw_request: str = ""


class GlobalPlanner:
    _MODEL_PRIORITIES = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]

    def __init__(self):
        self.api_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY", "")
        )
        self.client = None
        self.model_name = "gemini-2.0-flash"
        
        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
                self.model_name = self._pick_model() or "gemini-2.0-flash"
            except Exception as e:
                logger.error(f"Failed to initialise Gemini client in GlobalPlanner: {e}")

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

    def plan_project(self, user_request: str) -> ParsedIntent:
        if not self.client:
            print("[WARN] Gemini client not initialised. Falling back to default Home page.")
            return ParsedIntent(
                project_name="Untitled Project",
                pages=[PageIntent(name="Home", route="/", template="landing", goal="Default landing page", is_home=True)],
                raw_request=user_request
            )

        system_instruction = (
            "You are a principal web software architect and product manager.\n"
            "Your job is to read a natural-language website build request, determine the optimal "
            "site structure (number of pages, routes, template types), define the branding look and feel, "
            "and identify any generated assets required across the project.\n"
            "Keep the route structure clean and standard (e.g. '/' for Home, '/about' for About, '/services' for Services, '/contact' for Contact).\n"
            "Templates must be one of: landing, about, blog-list, blog-post, portfolio, contact, gallery, shop, pricing, services, faq, auth, dashboard, settings, search, legal, error.\n"
            "Response MUST be a JSON object matching the requested schema."
        )

        user_prompt = f"""
Analyze this website request: "{user_request}"

Provide a structured, cohesive, and comprehensive plan for the entire project. Return a JSON object matching this structure:
{{
  "project_name": "Short Name of the Website/Project",
  "theme_hints": ["modern", "glassmorphism", "neon", "minimal", etc.],
  "branding_guide": {{
    "visual_direction": "Brief summary of color themes, look, and aesthetic goals",
    "primary_font": "e.g. Outfit",
    "secondary_font": "e.g. Inter"
  }},
  "pages": [
    {{
      "name": "Page Name (e.g. Home)",
      "route": "Route path (e.g. /)",
      "template": "one of the supported template types",
      "goal": "Specific functional description of what this page must accomplish and show"
    }}
  ],
  "generated_assets": [
    {{
      "gen_prompt": "High-quality, descriptive prompt for generating this asset using Imagen 3",
      "aspect_ratio": "16:9 | 1:1 | 9:16"
    }}
  ]
}}

Make sure you structure the proper number of pages requested or implied by the user. If they want a 3-page site, generate exactly 3 pages. If they say 'a site for an agency', plan the appropriate pages (e.g. Home, Services, Contact).
"""

        try:
            print(f"[PLANNER] Running Stage 0 Global Planning using model {self.model_name}...")
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
                raise ValueError("Empty response received from Gemini.")

            plan = json.loads(response.text)
            
            pages = []
            for p in plan.get("pages", []):
                route = p.get("route", "/")
                pages.append(PageIntent(
                    name=p.get("name", "Untitled Page"),
                    route=route,
                    template=p.get("template", "landing"),
                    goal=p.get("goal", ""),
                    is_home=(route == "/")
                ))

            gen_assets = []
            for ga in plan.get("generated_assets", []):
                gen_assets.append(AssetIntent(
                    kind="generated",
                    gen_prompt=ga.get("gen_prompt", "Placeholder image"),
                    aspect_ratio=ga.get("aspect_ratio", "16:9")
                ))

            parsed = ParsedIntent(
                project_name=plan.get("project_name", "Untitled Project"),
                pages=pages,
                generated_assets=gen_assets,
                theme_hints=plan.get("theme_hints", []),
                branding_guide=plan.get("branding_guide", {}),
                raw_request=user_request
            )
            return parsed

        except Exception as e:
            print(f"[PLANNER] Error running global planner: {e}. Falling back.")
            logger.error(f"Global planning error: {e}", exc_info=True)
            return ParsedIntent(
                project_name="Untitled Project",
                pages=[PageIntent(name="Home", route="/", template="landing", goal="Default landing page", is_home=True)],
                raw_request=user_request
            )


def parse_and_commit(user_request: str,
                     uploaded_file_paths: Optional[List[str]] = None) -> Dict:
    """
    Parse the user request using the Global Planner and commit all resulting entities to Project Brain.
    """
    # Run the Global AI Planner
    planner = GlobalPlanner()
    intent = planner.plan_project(user_request)

    print(f"\n=======================================================")
    print(f"🌍 STAGE 0 GLOBAL PROJECT PLAN FOR: {intent.project_name}")
    print(f"=======================================================")
    print(f"Theme hints: {', '.join(intent.theme_hints)}")
    print(f"Visual direction: {intent.branding_guide.get('visual_direction')}")
    print(f"Pages planned:")
    for p in intent.pages:
        print(f"  * {p.name} ({p.route}) [{p.template}] -> {p.goal}")
    print(f"Generated assets planned:")
    for a in intent.generated_assets:
        print(f"  * Prompt: {a.gen_prompt} ({a.aspect_ratio})")
    print(f"=======================================================\n")

    summary = {
        "project_name": intent.project_name,
        "pages":        [],
        "routes":       [],
        "assets":       [],
        "generated":    [],
        "tool_requests": [],
    }

    # ── 1. Site structure ─────────────────────────────────────────────────────
    brain.update_site_structure({
        "project_name": intent.project_name,
        "theme":        {"hints": intent.theme_hints} if intent.theme_hints else None,
        "brand":        intent.branding_guide
    })

    # ── 2. Pages + Routes ─────────────────────────────────────────────────────
    nav_nodes = []
    nav_edges = []
    prev_page_id = None
    page_infos = []

    for page_intent in intent.pages:
        # SEO placeholder
        seo_id = brain.upsert_entity("seo", {
            "page_id":          "TBD",
            "title_tag":        f"{page_intent.name} | {intent.project_name}",
            "meta_description": f"Welcome to the {page_intent.name} page of {intent.project_name}.",
            "og_image":         None,
            "canonical_url":    page_intent.route,
            "structured_data":  None,
        })

        page_id = brain.upsert_entity("page", {
            "name":     page_intent.name,
            "route":    page_intent.route,
            "template": page_intent.template,
            "sections": [],
            "seo_id":   seo_id,
            "status":   "PENDING",
            "goal":     page_intent.goal
        })

        page_infos.append((page_id, page_intent.name, page_intent.route, page_intent.template, page_intent.goal))

        # Update SEO with real page_id
        seo_entity = brain.get_entity("seo", seo_id)
        if seo_entity:
            seo_entity["data"]["page_id"] = page_id
            brain.upsert_entity("seo", seo_entity["data"], entity_id=seo_id)

        route_id = brain.upsert_entity("route", {
            "path":    page_intent.route,
            "page_id": page_id,
            "dynamic": ":slug" in page_intent.route,
            "params":  ["slug"] if ":slug" in page_intent.route else [],
        })

        summary["pages"].append(page_id)
        summary["routes"].append(route_id)

        # Enqueue CONTENT tool request for this page
        req_id = brain.enqueue_tool_request({
            "tool":      "CONTENT",
            "target_id": page_id,
            "params":    {"page_id": page_id},
            "priority":  5,
        })
        summary["tool_requests"].append(req_id)

        nav_nodes.append({"id": page_id, "label": page_intent.name,
                          "route": page_intent.route})
        if prev_page_id:
            nav_edges.append({"from": prev_page_id, "to": page_id})
        prev_page_id = page_id

    brain.update_navigation_graph(nav_nodes, nav_edges)

    # ── 3. Uploaded asset placeholders ───────────────────────────────────────
    actual_paths = uploaded_file_paths or []
    for i, asset_intent in enumerate(intent.uploaded_assets):
        lpath = actual_paths[i] if i < len(actual_paths) else None
        fname = Path(lpath).name if lpath else asset_intent.filename

        asset_id = brain.upsert_entity("asset", {
            "original_filename": fname,
            "local_path":        lpath,
            "compressed_path":   None,
            "mime_type":         "image/unknown",
            "size_bytes":        None,
            "vision_id":         None,
            "status":            "PENDING",
        })
        summary["assets"].append(asset_id)

        # Queue VISION analysis if we have a real path
        if lpath:
            req_id = brain.enqueue_tool_request({
                "tool":      "VISION",
                "target_id": asset_id,
                "params":    {"image_path": lpath},
                "priority":  2,
            })
            summary["tool_requests"].append(req_id)

            # Queue COMPRESS after vision
            name, _ = os.path.splitext(fname)
            output_dir = ROOT / "output" / "assets"
            output_dir.mkdir(parents=True, exist_ok=True)
            compressed_path = str(output_dir / f"{name}.webp")
            req_id2 = brain.enqueue_tool_request({
                "tool":      "COMPRESS",
                "target_id": asset_id,
                "params":    {
                    "input_path":     lpath,
                    "output_path":    compressed_path,
                    "target_size_mb": 1.0,
                    "max_width":      3840,
                },
                "priority": 3,
            })
            summary["tool_requests"].append(req_id2)

    # ── 4. Generated asset placeholders ──────────────────────────────────────
    for gen_intent in intent.generated_assets:
        gen_id = brain.upsert_entity("generated_asset", {
            "prompt":       gen_intent.gen_prompt,
            "model":        "imagen-3.0-generate-001",
            "aspect_ratio": gen_intent.aspect_ratio,
            "filename":     None,
            "local_path":   None,
            "page_id":      None,
            "status":       "PENDING",
        })
        summary["generated"].append(gen_id)

        req_id = brain.enqueue_tool_request({
            "tool":      "IMAGEN",
            "target_id": gen_id,
            "params":    {
                "prompt":       gen_intent.gen_prompt,
                "aspect_ratio": gen_intent.aspect_ratio,
                "user_id":      "brain",
            },
            "priority": 4,
        })
        summary["tool_requests"].append(req_id)

    # ── 5. Write page traces and session overview memories ───────────────────
    all_routes = [p.route for p in intent.pages]
    for pid, name, route, template, goal in page_infos:
        nav_links = [r for r in all_routes if r != route]
        nav_links_str = ", ".join(nav_links)
        mem_content = f"{pid} | name={name} | route={route} | template={template} | nav_links=[{nav_links_str}] | page_assets=[] | goal={goal} | status=PLANNED"
        brain.upsert_entity("memory", {
            "key":        "page_build_trace",
            "content":    mem_content,
            "importance": 4,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    pages_list = [f"{p.name}{p.route}" for p in intent.pages]
    pages_str = ", ".join(pages_list)
    theme_str = ", ".join(intent.theme_hints)
    overview_content = (
        f"{len(intent.pages)}-page '{intent.project_name}' | "
        f"pages=[{pages_str}] | "
        f"theme=[{theme_str}] | "
        f"branding={intent.branding_guide} | "
        f"uploaded={len(intent.uploaded_assets)} generated={len(intent.generated_assets)}"
    )
    brain.upsert_entity("memory", {
        "key":        "session_overview",
        "content":    overview_content,
        "importance": 3,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return summary
