"""
Agent Bridge — Adapters connecting Project Brain to existing agent scripts
==========================================================================

This module wires all agents (visual.py, gemini_generator.py, compressor.py,
content_agent.py, ui_agent.py) to the Project Brain orchestrator so they can:

  • Pull pending VISION / IMAGEN / COMPRESS / CONTENT / UI / CODE tool requests
  • Write their results back into the correct Brain registries
  • Mark requests DONE or FAILED automatically
  • Auto-enqueue the next agent in the pipeline on success

Usage (standalone CLI):
    python brain/agent_bridge.py --tool VISION
    python brain/agent_bridge.py --tool IMAGEN
    python brain/agent_bridge.py --tool COMPRESS
    python brain/agent_bridge.py --tool CONTENT
    python brain/agent_bridge.py --tool UI
    python brain/agent_bridge.py --tool CODE
    python brain/agent_bridge.py --tool ALL
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent          # agent1/
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))  # brain/

# Load local environment variables from .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

# Force verbose logging
os.environ["BRAIN_VERBOSE"] = "1"

import orchestrator as brain                 # noqa: E402  (local import)
from visual import VisualRecognizer          # noqa: E402
from gemini_generator import GeminiGenerator # noqa: E402
from compressor import compress_image        # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Lazy singletons — initialized on first use
# ─────────────────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        print("[WARN] GEMINI_API_KEY not set in environment.")
    return key

_vision_agent: VisualRecognizer | None = None
_gen_agent: GeminiGenerator | None = None
_temp_folder = str(ROOT / "brain" / "temp")
os.makedirs(_temp_folder, exist_ok=True)


def _vision() -> VisualRecognizer:
    global _vision_agent
    if _vision_agent is None:
        _vision_agent = VisualRecognizer(api_key=_get_api_key())
    return _vision_agent


def _generator() -> GeminiGenerator:
    global _gen_agent
    if _gen_agent is None:
        _gen_agent = GeminiGenerator(api_key=_get_api_key(), temp_folder=_temp_folder)
    return _gen_agent


_content_agent = None
_ui_agent = None
_code_agent = None


def _content():
    global _content_agent
    if _content_agent is None:
        from content_agent import ContentAgent
        _content_agent = ContentAgent(api_key=_get_api_key())
    return _content_agent


def _ui():
    global _ui_agent
    if _ui_agent is None:
        from ui_agent import UIAgent
        _ui_agent = UIAgent(api_key=_get_api_key())
    return _ui_agent


def _code():
    global _code_agent
    if _code_agent is None:
        from code_agent import CodeAgent
        _code_agent = CodeAgent(api_key=_get_api_key())
    return _code_agent


# ─────────────────────────────────────────────────────────────────────────────
# VISION handler
# ─────────────────────────────────────────────────────────────────────────────

def run_vision_requests() -> None:
    """Process all pending VISION tool requests."""
    requests = brain.list_pending_requests("VISION")
    if not requests:
        print("[VISION] No pending requests.")
        return

    for req in requests:
        req_id    = req["request_id"]
        target_id = req.get("target_id")         # ASSET-xxxx
        params    = req.get("params", {})
        image_path = params.get("image_path")

        print(f"[VISION] Processing {req_id} -> asset {target_id} @ {image_path}")
        brain.update_tool_request(req_id, "IN_PROGRESS")

        result = _vision().analyze_image(image_path)

        if result.get("success"):
            vdata = result["data"]
            vdesc_id = brain.upsert_entity("image_description", {
                "asset_id":       target_id,
                "subject":        vdata.get("subject", ""),
                "description":    vdata.get("description", ""),
                "visual_style":   vdata.get("visual_style", ""),
                "mood":           vdata.get("mood", ""),
                "dominant_colors": vdata.get("dominant_colors", []),
                "keywords":       vdata.get("keywords", []),
                "suggested_use":  vdata.get("suggested_use", ""),
                "analyzed_at":    brain._now(),
            })
            # Link back to asset
            asset = brain.get_entity("asset", target_id)
            if asset:
                asset_data = asset["data"]
                asset_data["vision_id"] = vdesc_id
                asset_data["status"]    = "ANALYZED"
                brain.upsert_entity("asset", asset_data, entity_id=target_id)

                # Record asset_ref memory
                updated_asset = brain.get_entity("asset", target_id)
                asset_url = updated_asset["data"].get("url") if updated_asset else f"/assets/{target_id}.webp"
                use_desc = vdata.get("suggested_use", "general")
                file_name = asset_data.get("original_filename") or os.path.basename(asset_data.get("local_path") or "")
                ref_content = f"{target_id} | file={file_name} | url={asset_url} | use={use_desc}"
                brain.upsert_entity("memory", {
                    "key":        "asset_ref",
                    "content":    ref_content,
                    "importance": 4,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })

            brain.update_tool_request(req_id, "DONE", {"vision_id": vdesc_id})
            print(f"[VISION]  {req_id} done -> {vdesc_id}")

            # ── Record visual style memory so Imagen stays consistent ────────
            style_summary = (
                f"style={vdata.get('visual_style','?')} "
                f"mood={vdata.get('mood','?')} "
                f"colors={','.join(vdata.get('dominant_colors', [])[:3])}"
            )
            brain.upsert_entity("memory", {
                "key":        "visual_style",
                "content":    style_summary,
                "importance": 4,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            print(f"[VISION] OK Style memory saved: {style_summary}")
        else:
            brain.update_tool_request(req_id, "FAILED", result)
            print(f"[VISION] FAILED {req_id}: {result.get('error')}")


# ─────────────────────────────────────────────────────────────────────────────
# IMAGEN handler
# ─────────────────────────────────────────────────────────────────────────────

def run_imagen_requests() -> None:
    """Process all pending IMAGEN tool requests."""
    requests = brain.list_pending_requests("IMAGEN")
    if not requests:
        print("[IMAGEN] No pending requests.")
        return

    for req in requests:
        req_id    = req["request_id"]
        target_id = req.get("target_id")         # GEN-xxxx
        params    = req.get("params", {})

        prompt       = params.get("prompt", "")
        aspect_ratio = params.get("aspect_ratio", "1:1")
        user_id      = params.get("user_id", "brain")
        ref_image    = params.get("reference_image_path")

        # ── Self-prompt: prepend style context window to keep consistency ────
        # Only use relevant style memories — not the whole brain file.
        style_mems = [
            m["data"]["content"]
            for m in brain.list_memories().values()
            if m.get("data", {}).get("key") == "visual_style"
        ]
        if style_mems:
            style_ctx = "Visual style guidelines from analysed project images: " + " | ".join(style_mems[-3:])
            prompt = f"{style_ctx}\n\n{prompt}"
            print(f"[IMAGEN] OK Style context injected ({len(style_mems)} memory record(s))")

        print(f"[IMAGEN] Processing {req_id} -> {target_id}")
        brain.update_tool_request(req_id, "IN_PROGRESS")

        result = _generator().generate_image(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            user_id=user_id,
            reference_image_path=ref_image,
        )

        if result.get("success"):
            filename  = result["filename"]
            file_path = os.path.join(_temp_folder, filename)

            # Compress and move generated PNG to persistent assets/gen/ location as WebP
            output_dir = ROOT / "output" / "assets" / "gen"
            output_dir.mkdir(parents=True, exist_ok=True)
            compressed_path = str(output_dir / f"{target_id}.webp")
            
            print(f"[IMAGEN] Compressing generated image {file_path} to {compressed_path}...")
            try:
                compress_image(file_path, compressed_path, target_size_mb=1.0, max_width=3840)
                # If compression succeeded and output file exists, delete the temp png
                if os.path.exists(compressed_path):
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    filename = f"{target_id}.webp"
                    file_path = compressed_path
                else:
                    print(f"[IMAGEN] Warning: Compression didn't produce output, keeping raw PNG.")
            except Exception as e:
                print(f"[IMAGEN] Warning: Failed to compress generated image: {e}")

            # Update the generated_asset entity
            gen = brain.get_entity("generated_asset", target_id)
            if gen:
                gen_data = gen["data"]
                gen_data["filename"]   = filename
                gen_data["local_path"] = file_path
                gen_data["url"]        = f"/assets/gen/{target_id}.webp"
                gen_data["status"]     = "DONE"
                brain.upsert_entity("generated_asset", gen_data, entity_id=target_id)

            brain.update_tool_request(req_id, "DONE", {"filename": filename})
            print(f"[IMAGEN]  {req_id} done -> {filename}")

            # Record asset_ref memory
            ref_content = f"{target_id} | file={filename} | url=/assets/gen/{target_id}.webp | use=generated image"
            brain.upsert_entity("memory", {
                "key":        "asset_ref",
                "content":    ref_content,
                "importance": 4,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        else:
            # Mark entity as FAILED
            gen = brain.get_entity("generated_asset", target_id)
            if gen:
                gen["data"]["status"] = "FAILED"
                brain.upsert_entity("generated_asset", gen["data"], entity_id=target_id)

            brain.update_tool_request(req_id, "FAILED", result)
            print(f"[IMAGEN] FAILED {req_id}: {result.get('error')}")


# ─────────────────────────────────────────────────────────────────────────────
# COMPRESS handler
# ─────────────────────────────────────────────────────────────────────────────

def run_compress_requests() -> None:
    """Process all pending COMPRESS tool requests."""
    requests = brain.list_pending_requests("COMPRESS")
    if not requests:
        print("[COMPRESS] No pending requests.")
        return

    for req in requests:
        req_id    = req["request_id"]
        target_id = req.get("target_id")         # ASSET-xxxx
        params    = req.get("params", {})

        input_path    = params.get("input_path")
        
        # Override output_path to ensure it goes to the dedicated output/assets/ folder
        filename = os.path.basename(input_path)
        name, _ = os.path.splitext(filename)
        output_dir = ROOT / "output" / "assets"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"{name}.webp")

        target_size   = params.get("target_size_mb", 1.0)
        max_width     = params.get("max_width", 3840)

        print(f"[COMPRESS] Processing {req_id} -> asset {target_id}")
        brain.update_tool_request(req_id, "IN_PROGRESS")

        try:
            compress_image(input_path, output_path, target_size, max_width)

            compressed_size = os.path.getsize(output_path) / (1024 * 1024) if os.path.exists(output_path) else 0

            # Update asset entity
            asset = brain.get_entity("asset", target_id)
            if asset:
                asset_data = asset["data"]
                asset_data["compressed_path"] = output_path
                asset_data["url"]              = f"/assets/{name}.webp"
                # Check if already analyzed, if so mark READY, otherwise COMPRESSED
                if asset_data.get("vision_id") or asset_data.get("status") == "ANALYZED":
                    asset_data["status"] = "READY"
                else:
                    asset_data["status"] = "COMPRESSED"
                brain.upsert_entity("asset", asset_data, entity_id=target_id)

            brain.update_tool_request(req_id, "DONE", {
                "compressed_path": output_path,
                "compressed_size_mb": round(compressed_size, 3),
            })
            print(f"[COMPRESS]  {req_id} done -> {output_path} ({compressed_size:.2f} MB)")
        except Exception as e:
            brain.update_tool_request(req_id, "FAILED", {"error": str(e)})
            print(f"[COMPRESS] FAILED {req_id}: {e}")


def run_content_requests() -> None:
    """Process all pending CONTENT tool requests."""
    requests = brain.list_pending_requests("CONTENT")
    if not requests:
        print("[CONTENT] No pending requests.")
        return

    for req in requests:
        req_id    = req["request_id"]
        target_id = req.get("target_id")         # PAGE-xxxx

        # ── STAGE 1 Deep Page Planner ─────────────────────────────────────────
        # Automatically run detailed page planner if the page is still PENDING
        page = brain.get_entity("page", target_id)
        if page and page["data"].get("status") == "PENDING":
            print(f"[BRIDGE] Stage 1 Detailed Page Planner -> planning {target_id}...")
            from page_planner import PagePlanner
            planner = PagePlanner(api_key=_get_api_key())
            planner.generate_page_plan(target_id)

        print(f"[CONTENT] Processing {req_id} -> page {target_id}")
        brain.update_tool_request(req_id, "IN_PROGRESS")

        result = _content().generate_page_content(target_id)

        if result.get("success"):
            cids = result["content_ids"]
            brain.update_tool_request(req_id, "DONE", {"content_ids": cids})
            print(f"[CONTENT] OK {req_id} done -> generated {len(cids)} content block(s)")

            # Record content_ready memory
            page = brain.get_entity("page", target_id)
            pname = page["data"].get("name") if page else target_id
            sections_str = ", ".join(cids)
            mem_content = f"Content written for {pname} ({target_id}): sections=[{sections_str}] with layout signals"
            brain.upsert_entity("memory", {
                "key":        "content_ready",
                "content":    mem_content,
                "importance": 4,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

            # Auto-enqueue UI blueprint generation for this page
            ui_req_id = brain.enqueue_tool_request({
                "tool":      "UI",
                "target_id": target_id,
                "params":    {"page_id": target_id},
                "priority":  6,
            })
            print(f"[CONTENT] -> Enqueued UI request {ui_req_id} for page {target_id}")
        else:
            brain.update_tool_request(req_id, "FAILED", result)
            print(f"[CONTENT] FAILED {req_id}: {result.get('error')}")


# ─────────────────────────────────────────────────────────────────────────────
# UI Blueprint handler
# ─────────────────────────────────────────────────────────────────────────────

def run_ui_requests() -> None:
    """Process all pending UI tool requests."""
    requests = brain.list_pending_requests("UI")
    if not requests:
        print("[UI] No pending requests.")
        return

    for req in requests:
        req_id    = req["request_id"]
        target_id = req.get("target_id")         # PAGE-xxxx

        print(f"[UI] Processing {req_id} -> page {target_id}")
        brain.update_tool_request(req_id, "IN_PROGRESS")

        result = _ui().generate_ui_blueprint(target_id)

        if result.get("success"):
            bp_id = result["blueprint_id"]
            brain.update_tool_request(req_id, "DONE", {
                "blueprint_id": bp_id,
                "sections":     result["sections"],
            })
            print(f"[UI] OK {req_id} done -> {bp_id} ({result['sections']} sections)")

            # Auto-enqueue CODE generation for this page
            code_req_id = brain.enqueue_tool_request({
                "tool":      "CODE",
                "target_id": target_id,
                "params":    {"page_id": target_id, "blueprint_id": bp_id},
                "priority":  7,
            })
            print(f"[UI] -> Enqueued CODE request {code_req_id} for page {target_id}")
        else:
            brain.update_tool_request(req_id, "FAILED", result)
            print(f"[UI] FAILED {req_id}: {result.get('error')}")


# ─────────────────────────────────────────────────────────────────────────────
# CODE handler
# ─────────────────────────────────────────────────────────────────────────────

def run_code_requests() -> None:
    """Process all pending CODE tool requests."""
    requests = brain.list_pending_requests("CODE")
    if not requests:
        print("[CODE] No pending requests.")
        return

    for req in requests:
        req_id    = req["request_id"]
        target_id = req.get("target_id")         # PAGE-xxxx
        params    = req.get("params", {})
        bp_id     = params.get("blueprint_id")

        print(f"[CODE] Processing {req_id} -> page {target_id} (blueprint={bp_id})")
        brain.update_tool_request(req_id, "IN_PROGRESS")

        result = _code().generate_page_code(target_id, blueprint_id=bp_id)

        if result.get("success"):
            brain.update_tool_request(req_id, "DONE", result)
            print(f"[CODE] OK {req_id} done -> {result.get('output_dir')} (HTML: {result.get('html_size')}KB, CSS: {result.get('css_size')}KB, JS: {result.get('js_size')}KB)")
        else:
            brain.update_tool_request(req_id, "FAILED", result)
            print(f"[CODE] FAILED {req_id}: {result.get('error')}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Agent Bridge — run pending Project Brain tool requests."
    )
    parser.add_argument(
        "--tool",
        choices=["VISION", "IMAGEN", "COMPRESS", "CONTENT", "UI", "CODE", "ALL"],
        default="ALL",
        help="Which tool queue to process (default: ALL).",
    )
    args = parser.parse_args()

    if args.tool in ("VISION", "ALL"):
        run_vision_requests()
    if args.tool in ("IMAGEN", "ALL"):
        run_imagen_requests()
    if args.tool in ("COMPRESS", "ALL"):
        run_compress_requests()
    if args.tool in ("CONTENT", "ALL"):
        run_content_requests()
    if args.tool in ("UI", "ALL"):
        run_ui_requests()
    if args.tool in ("CODE", "ALL"):
        run_code_requests()

    print("\n[BRIDGE] Done.")


if __name__ == "__main__":
    main()
