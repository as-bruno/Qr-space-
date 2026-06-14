"""
code_agent.py — Page Code Generation Agent for Project Brain
=============================================================
The final stage of the Build Pipeline:

  CONTENT Agent -> UI Agent -> CODE Agent

Reads from the Brain:
  - UI Blueprint  (layout, typography, animations, breakpoints, tokens)
  - Content Blocks (copy, CTA links, image URLs)
  - Asset registry (resolved image URLs)
  - Memory traces  (warnings, cross-page consistency notes)

Produces:
  - Separate HTML structure file
  - Separate CSS stylesheet
  - Separate JS script
  - Saves the output to /output/<page_slug>/ (index.html, style.css, script.js)
  - Writes a 'code_output' entity back to the Brain
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Force UTF-8 encoding for stdout/stderr on Windows to prevent charmap errors
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

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

# ── Output directory ──────────────────────────────────────────────────────────
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
logger = logging.getLogger("CodeAgent")
if not logger.handlers:
    _log_path = ROOT / "brain" / "gemini_debug.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(_log_path, encoding="utf-8")
    _fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s -- %(message)s"))
    logger.addHandler(_fh)
    logger.setLevel(logging.INFO)


class CodeAgent:
    """
    Code Agent reads the UI Blueprint and content from the Brain and writes
    a complete, production-ready HTML page, CSS stylesheet, and JS script.
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
                logger.info(f"CodeAgent initialised -- model: {self.model_name}")
            except Exception as e:
                logger.error(f"Failed to initialise Gemini client in CodeAgent: {e}")
        else:
            logger.warning("CodeAgent: No API key found.")

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

    def _get_content_blocks(self, page_id: str) -> List[Dict[str, Any]]:
        """Return all content blocks for the given page."""
        b = brain.get_brain()
        content_reg = b.get("content", {})
        blocks = []
        for cid, c in content_reg.items():
            if c.get("data", {}).get("page_id") == page_id:
                blocks.append({"content_id": cid, **c["data"]})
        return blocks

    def _get_blueprint(self, page_id: str,
                       blueprint_id: Optional[str] = None) -> Optional[Dict]:
        """Fetch the UI blueprint from Brain for this page."""
        b = brain.get_brain()
        blueprints = b.get("ui_blueprints", {})

        # If a specific blueprint_id was provided use it
        if blueprint_id and blueprint_id in blueprints:
            return blueprints[blueprint_id].get("data", {})

        # Otherwise find the blueprint whose page_id matches
        for bpid, bp in blueprints.items():
            d = bp.get("data", {})
            if d.get("page_id") == page_id:
                return d

        return None

    def _build_warnings_block(self, blueprint: Dict) -> str:
        """Collect all code_agent_warnings from the blueprint sections."""
        lines = []
        self_prompt = blueprint.get("code_agent_self_prompt", {})

        # Global critical reminders
        for note in self_prompt.get("critical_reminders", []):
            lines.append(f"  REMINDER: {note}")

        # Cross-page consistency
        consistency = self_prompt.get("cross_page_consistency", "")
        if consistency:
            lines.append(f"  CROSS-PAGE: {consistency}")

        # Known risks
        for risk in self_prompt.get("known_risks", []):
            lines.append(f"  RISK: {risk}")

        # Per-section warnings
        for sec in blueprint.get("sections", []):
            for warn in sec.get("code_agent_warnings", []):
                kind = sec.get("section_type", "section")
                lines.append(f"  [{kind.upper()}] {warn}")

        return "\n".join(lines) if lines else "  (no specific warnings)"

    def _slug_from_route(self, route: str) -> str:
        """Convert a route like /about to about, / to index."""
        route = route.strip("/")
        if not route:
            return "index"
        return re.sub(r"[^a-z0-9_-]", "_", route.lower())

    def _get_other_page_links(self, page_id: str) -> str:
        """Compact nav link list from other pages — so code can build a navbar."""
        pages = brain.list_pages()
        links = []
        for pid, p in pages.items():
            if pid == page_id:
                continue
            d = p["data"]
            # Generate the correct relative link between pages in separate subdirectories
            # E.g. from index to about -> ../about/index.html
            slug = self._slug_from_route(d.get("route", "/"))
            links.append(f"{d.get('name','Page')} -> ../{slug}/index.html")
        return "\n".join(links) if links else "(single-page site)"

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 1: Generate HTML Structure
    # ─────────────────────────────────────────────────────────────────────────
    def _generate_html(self, page_name: str, route: str, seo_block: str,
                       context_summary: str, nav_links: str, warnings_block: str,
                       blueprint_json: str, content_json: str) -> str:
        
        system_instruction = (
            "You are an elite front-end developer specialising in semantic HTML5 structure.\n"
            "Output ONLY the complete HTML document starting with <!DOCTYPE html>.\n"
            "CRITICAL RULES:\n"
            "1. Output ONLY the HTML text. No markdown formatting, no code block fences (```html), no explanations.\n"
            "2. Link the stylesheet using `<link rel=\"stylesheet\" href=\"style.css\">` in <head>.\n"
            "3. Link the script using `<script defer src=\"script.js\"></script>` in <head>.\n"
            "4. NEVER include any `<style>` tags or inline style attributes in the HTML.\n"
            "5. NEVER include any `<script>` tags containing Javascript logic (only the reference to script.js).\n"
            "6. Structure sections using semantic tags like <header>, <section>, <article>, <footer>.\n"
            "7. Every interactive element must be fully structured with classes, IDs, and aria-labels.\n"
        )

        user_prompt = f"""
Build the semantic HTML structure skeleton for: **{page_name}** (route: `{route}`)

## SEO Requirements
{seo_block}

## Project Context
{context_summary}

## Navigation Links (use these exact URLs for navbar/footer menu)
{nav_links}

## Self-Prompt Warnings
{warnings_block}

## UI Blueprint
```json
{blueprint_json}
```

## Content Blocks (verbatim headings, body copy, CTA labels)
```json
{content_json}
```

Build the COMPLETE semantic HTML structure now. Follow the blueprint sections in order. Do NOT use inline style/scripts or placeholder text.
"""
        print(f"[CODE] Phase 1/3 -> Generating HTML structure for {page_name}...")
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.05,
            )
        )
        code = response.text or ""
        code = code.strip()
        if code.startswith("```"):
            code = re.sub(r"^```[a-z]*\n?", "", code)
            code = re.sub(r"\n?```$", "", code.strip())
        if not code.lstrip().startswith("<!"):
            code = "<!DOCTYPE html>\n" + code
        return code

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 2: Generate CSS Stylesheet
    # ─────────────────────────────────────────────────────────────────────────
    def _generate_css(self, page_name: str, route: str, context_summary: str,
                      warnings_block: str, blueprint_json: str, html_code: str) -> str:
        
        system_instruction = (
            "You are a master CSS architect specialising in premium, responsive layout styling.\n"
            "Output ONLY clean, raw CSS. Do not wrap in markdown ```css code blocks, and do not write explanations.\n"
            "CRITICAL RULES:\n"
            "1. Output ONLY the CSS text.\n"
            "2. Use CSS custom properties (--token-name) at the `:root` level for theme colors, typography, borders, shadows, spacing, etc.\n"
            "3. Style the page to match the premium, professional, responsive, and glassmorphism/aesthetic requirements from design hints.\n"
            "4. Follow mobile-first design: write baseline mobile styles first, then override using media queries like `@media (min-width: 768px)` and `@media (min-width: 1024px)`.\n"
            "5. Style hover, focus-visible, and active states for all buttons, nav links, and interactive elements.\n"
            "6. Include the scroll-reveal classes and transition properties needed for entrance animations.\n"
        )

        user_prompt = f"""
Generate the standalone CSS stylesheet (`style.css`) for: **{page_name}** (route: `{route}`)

Here is the HTML structure skeleton we are styling:
```html
{html_code}
```

## Project Design Theme & Blueprint:
```json
{blueprint_json}
```

## Self-Prompt Warnings:
{warnings_block}

## Context & Theme:
{context_summary}

Build the COMPLETE CSS stylesheet now. Ensure all UI theme tokens are implemented and mobile breakpoints are configured correctly.
"""
        print(f"[CODE] Phase 2/3 -> Generating CSS stylesheet for {page_name}...")
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.05,
                max_output_tokens=8192,
            )
        )
        code = response.text or ""
        code = code.strip()
        if code.startswith("```"):
            code = re.sub(r"^```[a-z]*\n?", "", code)
            code = re.sub(r"\n?```$", "", code.strip())
        return code

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 3: Generate JS Interactivity
    # ─────────────────────────────────────────────────────────────────────────
    def _generate_js(self, page_name: str, route: str, context_summary: str,
                     warnings_block: str, blueprint_json: str, html_code: str) -> str:
        
        system_instruction = (
            "You are a skilled front-end JavaScript developer specialising in vanilla interactions and animations.\n"
            "Output ONLY clean, raw JavaScript. Do not wrap in markdown ```javascript code blocks, and do not write explanations.\n"
            "CRITICAL RULES:\n"
            "1. Output ONLY the JavaScript text.\n"
            "2. Implement Intersection Observer API for entrance / scroll-reveal animations.\n"
            "3. Implement mobile menu toggle interactivity (open/close side drawers).\n"
            "4. Implement any custom micro-animations (e.g. typing text headers, interactive sliders, pricing toggles) requested in the plan.\n"
            "5. Ensure code is safe: wrap in `document.addEventListener('DOMContentLoaded', ...)`.\n"
            "6. Apply motion-sensitivity checks (disable animations if `prefers-reduced-motion` is active).\n"
        )

        user_prompt = f"""
Generate the standalone vanilla Javascript file (`script.js`) for: **{page_name}** (route: `{route}`)

Here is the HTML structure we are script-handling:
```html
{html_code}
```

## UI Blueprint Instructions:
```json
{blueprint_json}
```

## Self-Prompt Warnings:
{warnings_block}

Build the COMPLETE Javascript script now. Make it responsive, interactive, and fast.
"""
        print(f"[CODE] Phase 3/3 -> Generating JS interactivity scripts for {page_name}...")
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.05,
            )
        )
        code = response.text or ""
        code = code.strip()
        if code.startswith("```"):
            code = re.sub(r"^```[a-z]*\n?", "", code)
            code = re.sub(r"\n?```$", "", code.strip())
        return code

    # ─────────────────────────────────────────────────────────────────────────
    # Main entry point
    # ─────────────────────────────────────────────────────────────────────────

    def generate_page_code(self,
                            page_id: str,
                            blueprint_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate production HTML/CSS/JS for *page_id* using its UI blueprint
        and content blocks. Saves files separately in /output/<slug>/.
        """
        if not self.client:
            return {"success": False, "error": "Gemini client not initialised."}

        # 1. Load page
        page = brain.get_entity("page", page_id)
        if not page:
            return {"success": False, "error": f"Page {page_id} not found."}
        page_data = page["data"]
        page_name = page_data.get("name", "Page")
        route     = page_data.get("route", "/")
        seo_id    = page_data.get("seo_id")

        # 2. Load blueprint
        blueprint = self._get_blueprint(page_id, blueprint_id)
        if not blueprint:
            return {
                "success": False,
                "error": (
                    f"No UI blueprint found for page {page_id}. "
                    "Run the UI Agent first."
                )
            }

        # 3. Load content blocks
        content_blocks = self._get_content_blocks(page_id)
        if not content_blocks:
            return {
                "success": False,
                "error": f"No content blocks for page {page_id}. Run Content Agent first."
            }

        # 4. Load SEO data
        seo_data = {}
        if seo_id:
            seo_entity = brain.get_entity("seo", seo_id)
            if seo_entity:
                seo_data = seo_entity["data"]

        # 5. Build rich context strings
        context_summary = brain.get_context_summary(max_memories=10, for_page_id=page_id)
        warnings_block  = self._build_warnings_block(blueprint)
        nav_links       = self._get_other_page_links(page_id)

        # Serialise inputs
        blueprint_json = json.dumps(blueprint, indent=2, ensure_ascii=False)
        content_json = json.dumps(content_blocks, indent=2, ensure_ascii=False)
        seo_block = (
            f"Title: {seo_data.get('title_tag', page_name)}\n"
            f"Meta Description: {seo_data.get('meta_description', '')}\n"
            f"Canonical: {seo_data.get('canonical_url', route)}"
        )

        # Output slug and directories
        slug = self._slug_from_route(route)
        output_dir = OUTPUT_DIR / slug
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 6. Execute 3 passes
            html_code = self._generate_html(page_name, route, seo_block, context_summary, nav_links, warnings_block, blueprint_json, content_json)
            css_code  = self._generate_css(page_name, route, context_summary, warnings_block, blueprint_json, html_code)
            js_code   = self._generate_js(page_name, route, context_summary, warnings_block, blueprint_json, html_code)

            # 7. Write to disk
            html_path = output_dir / "index.html"
            css_path  = output_dir / "style.css"
            js_path   = output_dir / "script.js"

            # Make absolute /assets/ paths relative so pages work directly via file:/// protocol
            html_code = html_code.replace('"/assets/', '"../assets/')
            html_code = html_code.replace("'/assets/", "'../assets/")
            html_code = html_code.replace('(/assets/', '(../assets/')
            html_code = html_code.replace('=/assets/', '=../assets/')
            
            css_code = css_code.replace('"/assets/', '"../assets/')
            css_code = css_code.replace("'/assets/", "'../assets/")
            css_code = css_code.replace('(/assets/', '(../assets/')
            css_code = css_code.replace('=/assets/', '=../assets/')
            css_code = css_code.replace('url(/assets/', 'url(../assets/')

            html_path.write_text(html_code, encoding="utf-8")
            css_path.write_text(css_code, encoding="utf-8")
            js_path.write_text(js_code, encoding="utf-8")

            html_size_kb = round(html_path.stat().st_size / 1024, 1)
            css_size_kb  = round(css_path.stat().st_size / 1024, 1)
            js_size_kb   = round(js_path.stat().st_size / 1024, 1)

            print(f"[CODE] OK HTML written: {html_path.name} ({html_size_kb} KB, {len(html_code.splitlines())} lines)")
            print(f"[CODE] OK CSS written: {css_path.name} ({css_size_kb} KB, {len(css_code.splitlines())} lines)")
            print(f"[CODE] OK JS written: {js_path.name} ({js_size_kb} KB, {len(js_code.splitlines())} lines)")

            # ── Update page status in Brain ─────────────────────────────────
            pdata = page_data.copy()
            pdata["status"]        = "BUILT"
            pdata["output_dir"]    = str(output_dir)
            pdata["html_file"]     = str(html_path)
            pdata["css_file"]      = str(css_path)
            pdata["js_file"]       = str(js_path)
            pdata["output_file"]   = str(html_path)  # for backward compatibility
            pdata["built_at"]      = datetime.now(timezone.utc).isoformat()
            brain.upsert_entity("page", pdata, entity_id=page_id)

            # ── Register code_output entity in Brain ────────────────────────
            code_out_id = brain.upsert_entity("code_output", {
                "page_id":      page_id,
                "output_dir":   str(output_dir),
                "html_file":    str(html_path),
                "css_file":     str(css_path),
                "js_file":      str(js_path),
                "slug":         slug,
                "html_size_kb": html_size_kb,
                "css_size_kb":  css_size_kb,
                "js_size_kb":   js_size_kb,
                "built_at":     datetime.now(timezone.utc).isoformat()
            })

            # ── Write code_output trace memory ──────────────────────────────
            mem_content = (
                f"{page_id} | name={page_name} | route={route} | "
                f"html={html_size_kb}KB | css={css_size_kb}KB | js={js_size_kb}KB | "
                f"status=BUILT"
            )
            brain.upsert_entity("memory", {
                "key":        "code_output_trace",
                "content":    mem_content,
                "importance": 4,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

            return {
                "success":     True,
                "output_dir":  str(output_dir),
                "output_file": str(html_path),
                "html_file":   str(html_path),
                "css_file":    str(css_path),
                "js_file":     str(js_path),
                "slug":        slug,
                "html_size":   html_size_kb,
                "css_size":    css_size_kb,
                "js_size":     js_size_kb,
            }

        except ClientError as e:
            logger.error(f"ClientError in CodeAgent: {e}")
            return {"success": False, "error": f"Gemini API error: {e}"}
        except Exception as e:
            logger.error(f"Unexpected error in CodeAgent: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        pid = sys.argv[1]
        bid = sys.argv[2] if len(sys.argv) > 2 else None
        _api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        agent = CodeAgent(api_key=_api_key)
        print(f"[*] Generating code for page {pid}...")
        res = agent.generate_page_code(pid, blueprint_id=bid)
        if res.get("success"):
            print(f"[OK] Output directory: {res['output_dir']}")
            print(f"     HTML: {res['html_file']} ({res['html_size']} KB)")
            print(f"     CSS:  {res['css_file']} ({res['css_size']} KB)")
            print(f"     JS:   {res['js_file']} ({res['js_size']} KB)")
        else:
            print(f"[FAILED] {res.get('error')}")
    else:
        print("Usage: python code_agent.py <PAGE-ID> [BLUEPRINT-ID]")
