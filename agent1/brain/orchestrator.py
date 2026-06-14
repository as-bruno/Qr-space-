"""
Project Brain Core — Orchestrator Module
=========================================
Provides read/write access to project_brain.json, ID generation,
entity validation, and task-queue management.

This module is the ONLY authorized path to mutate Project Brain state.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

# ── Paths ────────────────────────────────────────────────────────────────────
BRAIN_DIR = Path(__file__).parent
BRAIN_FILE = BRAIN_DIR / "project_brain.json"

# ── Valid entity types ────────────────────────────────────────────────────────
EntityType = Literal[
    "page",
    "route",
    "asset",
    "image_description",
    "generated_asset",
    "seo",
    "design",
    "content",
    "memory",
    "ui_blueprint",
    "code_output",
]

# ── Registry keys that correspond to each entity type ────────────────────────
_TYPE_TO_REGISTRY: Dict[str, str] = {
    "page":              "page_registry",
    "route":             "route_registry",
    "asset":             "asset_registry",
    "image_description": "image_descriptions",
    "generated_asset":   "generated_assets",
    "seo":               "seo_graph",
    "design":            "design",
    "content":           "content",
    "memory":            "memory_registry",
    "ui_blueprint":      "ui_blueprints",
    "code_output":       "code_outputs",
}

# ── ID prefixes ───────────────────────────────────────────────────────────────
_TYPE_PREFIX: Dict[str, str] = {
    "page":              "PAGE",
    "route":             "ROUTE",
    "asset":             "ASSET",
    "image_description": "VDESC",
    "generated_asset":   "GEN",
    "seo":               "SEO",
    "design":            "DESIGN",
    "content":           "CONTENT",
    "memory":            "MEM",
    "ui_blueprint":      "UIBP",
    "code_output":       "CODE_OUT",
}



# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _derive_asset_url(filename: str, prefix: str = "/assets") -> str:
    if not filename:
        return ""
    filename = filename.replace("\\", "/")
    base = filename.split("/")[-1]
    name, _ = os.path.splitext(base)
    return f"{prefix}/{name}.webp"


def _load() -> Dict[str, Any]:
    """Load the Project Brain JSON from disk."""
    with open(BRAIN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(brain: Dict[str, Any]) -> None:
    """Persist the Project Brain JSON to disk atomically."""
    brain["_meta"]["last_updated"] = _now()
    tmp = BRAIN_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(brain, f, indent=2, ensure_ascii=False)
    tmp.replace(BRAIN_FILE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id(brain: Dict[str, Any], entity_type: str) -> str:
    """Generate a sequential ID like PAGE-0003."""
    prefix = _TYPE_PREFIX.get(entity_type, "ENT")
    registry_key = _TYPE_TO_REGISTRY.get(entity_type, entity_type)
    registry = brain.get(registry_key, {})
    n = len(registry) + 1
    return f"{prefix}-{n:04d}"


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_brain() -> Dict[str, Any]:
    """Return the full Project Brain state (read-only view)."""
    return _load()


def get_entity(entity_type: str, entity_id: str) -> Optional[Dict[str, Any]]:
    """Return a single entity by type and ID, or None if not found."""
    brain = _load()
    registry_key = _TYPE_TO_REGISTRY.get(entity_type)
    if not registry_key:
        raise ValueError(f"Unknown entity type: {entity_type}")
    return brain.get(registry_key, {}).get(entity_id)


def upsert_entity(entity_type: str, data: Dict[str, Any],
                  entity_id: Optional[str] = None) -> str:
    """
    Create or update an entity in the Project Brain.

    Parameters
    ----------
    entity_type : str
        One of the valid EntityType values.
    data : dict
        The entity payload (everything under 'data:' in the schema).
    entity_id : str, optional
        If provided, update the existing entity; otherwise create a new one.

    Returns
    -------
    str
        The entity_id of the created/updated entity.
    """
    brain = _load()
    registry_key = _TYPE_TO_REGISTRY.get(entity_type)
    if not registry_key:
        raise ValueError(f"Unknown entity type: '{entity_type}'")

    if registry_key not in brain:
        brain[registry_key] = {}

    eid = entity_id or _next_id(brain, entity_type)

    if entity_type == "asset":
        if not data.get("url"):
            fn = data.get("original_filename") or data.get("local_path") or ""
            if fn:
                data["url"] = _derive_asset_url(fn)
    elif entity_type == "generated_asset":
        if data.get("status") == "DONE" and not data.get("url"):
            data["url"] = f"/assets/gen/{eid}.webp"

    brain[registry_key][eid] = {
        "entity_id": eid,
        "type":      entity_type,
        "data":      data,
    }

    _log_event(brain, f"{entity_type.upper()}_UPSERTED", f"Entity {eid} saved.")
    _save(brain)
    return eid


def delete_entity(entity_type: str, entity_id: str) -> bool:
    """Remove an entity from the registry. Returns True if deleted."""
    brain = _load()
    registry_key = _TYPE_TO_REGISTRY.get(entity_type)
    if not registry_key:
        return False
    deleted = brain[registry_key].pop(entity_id, None)
    if deleted:
        _log_event(brain, f"{entity_type.upper()}_DELETED", f"Entity {entity_id} removed.")
        _save(brain)
        return True
    return False


def enqueue_tool_request(request: Dict[str, Any]) -> str:
    """
    Add a tool request to the task queue.

    Expected fields in *request*:
        tool      : str   — e.g. "VISION", "IMAGEN", "COMPRESS", "CODE"
        target_id : str   — entity the tool should operate on
        params    : dict  — tool-specific parameters
        priority  : int   — lower = higher priority (default 5)
    """
    brain = _load()
    req_id = f"TREQ-{uuid.uuid4().hex[:8].upper()}"
    request["request_id"] = req_id
    request["status"]     = "PENDING"
    request["created_at"] = _now()
    request.setdefault("priority", 5)

    brain["tool_requests"].append(request)
    _log_event(brain, "TOOL_REQUEST_ENQUEUED",
               f"{request['tool']} request {req_id} queued for {request.get('target_id', 'N/A')}.")
    _save(brain)
    return req_id


def dequeue_tool_requests(tool: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Return all PENDING tool requests (optionally filtered by tool name).
    Does NOT mutate state — caller must call update_tool_request() when done.
    """
    brain = _load()
    reqs = brain["tool_requests"]
    pending = [r for r in reqs if r["status"] == "PENDING"]
    if tool:
        pending = [r for r in pending if r["tool"] == tool]
    return sorted(pending, key=lambda r: r.get("priority", 5))


def update_tool_request(request_id: str,
                        status: str,
                        result: Optional[Dict[str, Any]] = None) -> None:
    """Mark a tool request as DONE, FAILED, or IN_PROGRESS, and store its result."""
    brain = _load()
    for req in brain["tool_requests"]:
        if req["request_id"] == request_id:
            req["status"]     = status
            req["updated_at"] = _now()
            if result is not None:
                req["result"] = result
            break
    _log_event(brain, "TOOL_REQUEST_UPDATED",
               f"Request {request_id} -> {status}.")
    _save(brain)


def update_site_structure(updates: Dict[str, Any]) -> None:
    """Merge *updates* into the top-level site_structure block."""
    brain = _load()
    brain["site_structure"].update(updates)
    _log_event(brain, "SITE_STRUCTURE_UPDATED", str(updates))
    _save(brain)


def update_navigation_graph(nodes: List[Dict], edges: List[Dict]) -> None:
    """Replace the navigation graph with new nodes and edges."""
    brain = _load()
    brain["navigation_graph"]["nodes"] = nodes
    brain["navigation_graph"]["edges"] = edges
    _log_event(brain, "NAV_GRAPH_UPDATED",
               f"{len(nodes)} nodes, {len(edges)} edges.")
    _save(brain)


# ─────────────────────────────────────────────────────────────────────────────
# Internal logging
# ─────────────────────────────────────────────────────────────────────────────

def _log_event(brain: Dict[str, Any], event: str, details: str = "") -> None:
    log_id = f"LOG-{len(brain['execution_log']) + 1:04d}"
    brain["execution_log"].append({
        "log_id":    log_id,
        "timestamp": _now(),
        "event":     event,
        "actor":     "ORCHESTRATOR",
        "details":   details,
    })
    if os.environ.get("BRAIN_VERBOSE") != "0":
        print(f"[BRAIN] {event:<25} | {details}")



# ─────────────────────────────────────────────────────────────────────────────
# Quick-access helpers used by downstream agents
# ─────────────────────────────────────────────────────────────────────────────

def list_pages() -> Dict[str, Any]:
    return _load()["page_registry"]

def list_assets() -> Dict[str, Any]:
    return _load()["asset_registry"]

def list_generated() -> Dict[str, Any]:
    return _load()["generated_assets"]

def list_pending_requests(tool: Optional[str] = None) -> List[Dict]:
    return dequeue_tool_requests(tool)

def get_execution_log() -> List[Dict[str, Any]]:
    return _load()["execution_log"]

def list_memories() -> Dict[str, Any]:
    return _load().get("memory_registry", {})

def get_context_summary(max_memories: int = 5, for_page_id: Optional[str] = None) -> str:
    """
    Builds a lightweight, token-saving plain-text context window of the current project state.
    Includes metadata, routes, high-importance rules, and recent memories.
    """
    brain = _load()
    ss = brain.get("site_structure", {})
    
    # 1. Base Metadata
    lines = []
    lines.append(f"PROJECT CONTEXT:")
    lines.append(f"- Name: {ss.get('project_name') or 'Untitled'}")
    theme = ss.get("theme")
    if theme:
        lines.append(f"- Theme hints: {', '.join(theme.get('hints', [])) if isinstance(theme, dict) else theme}")
        
    # 2. Page & Route Index (Concise lists)
    routes = brain.get("route_registry", {})
    if routes:
        route_paths = [r["data"]["path"] for r in routes.values() if "data" in r]
        lines.append(f"- Active Routes: {', '.join(route_paths)}")
        
    # 3. High-importance memories & Warnings
    memories = brain.get("memory_registry", {})
    recent_memories = []
    if memories:
        # Sort chronologically by ID
        sorted_mems = sorted(memories.values(), key=lambda m: m.get("entity_id", ""))
        
        # If for_page_id is specified:
        # 1. Pull page_build_trace memories for OTHER pages.
        # 2. Pull ALL asset_ref memories.
        # 3. Plus any other high-importance rules.
        if for_page_id:
            filtered_mems = []
            for m in sorted_mems:
                mdata = m.get("data", {})
                key = mdata.get("key", "")
                content = mdata.get("content", "")
                is_trace = key == "page_build_trace"
                is_asset_ref = key == "asset_ref"
                
                if is_asset_ref:
                    filtered_mems.append(m)
                elif is_trace:
                    if for_page_id not in content:
                        filtered_mems.append(m)
                else:
                    if mdata.get("importance", 3) >= 3:
                        filtered_mems.append(m)
            sorted_mems = filtered_mems
            
        # Filter high importance warnings first
        warnings = [m["data"] for m in sorted_mems if m.get("data", {}).get("importance", 3) >= 4]
        other_mems = [m["data"] for m in sorted_mems if m.get("data", {}).get("importance", 3) < 4]
        
        # Combine up to max_memories
        selected_mems = (warnings + other_mems)[:max_memories]
        for m in selected_mems:
            recent_memories.append(f"  * [{m.get('key')}]: {m.get('content')} (importance: {m.get('importance')})")
            
    if recent_memories:
        lines.append("- Critical Decisions & Memory Context:")
        lines.extend(recent_memories)
        
    # 4. Last 3 events from Log (Concise audit)
    log = brain.get("execution_log", [])[-3:]
    if log:
        lines.append("- Recent Activity Logs:")
        for entry in log:
            lines.append(f"  * {entry.get('event')}: {entry.get('details', '')[:80]}")
            
    return "\n".join(lines[:20])
