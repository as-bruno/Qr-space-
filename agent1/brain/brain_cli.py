"""
Brain CLI — Interactive Project Brain Command Interface
=======================================================
A unified REPL / command-line tool for inspecting and mutating the
Project Brain without writing Python.

Commands
--------
  new <request>           Parse a new user request and commit to Brain
  status                  Print a summary of current Brain state
  pages                   List all registered pages
  assets                  List all registered assets
  generated               List all generated asset placeholders
  blueprints              List all UI blueprints
  outputs                 List all built page outputs
  log [N]                 Show last N execution log entries (default: 10)
  queue [TOOL]            Show pending tool requests (optionally filtered)
  run [TOOL]              Execute pending requests via Agent Bridge
                          Tools: VISION, IMAGEN, COMPRESS, CONTENT, UI, CODE, ALL
  set-prompt <GEN-ID> <prompt>
                          Update the generation prompt for a GEN-xxxx entity
  memories                Show all stored agent memories
  remember <key> <content> Store a new memory rule
  context [PAGE-ID]       Print context summary
  reset                   Wipe and re-initialise the Project Brain (DESTRUCTIVE)
  help                    Show this help text
  exit / quit             Exit the CLI

Usage:
    python brain/brain_cli.py
    python brain/brain_cli.py new "Blog site with home, about, blog pages and 3 images"
"""

import sys
import os

# Force UTF-8 encoding for standard streams to prevent UnicodeEncodeError on Windows
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import json
from pathlib import Path
from textwrap import indent

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

# Load local environment variables from .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

import orchestrator as brain
import agent_bridge as bridge
from intent_parser import parse_and_commit

# ── ANSI colours (degrade gracefully on Windows without VT mode) ──────────────
try:
    import ctypes
    ctypes.windll.kernel32.SetConsoleMode(
        ctypes.windll.kernel32.GetStdHandle(-11), 7)
    _COLORS = True
except Exception:
    _COLORS = False

def _c(code: str, text: str) -> str:
    if not _COLORS:
        return text
    codes = {"bold":"\033[1m","reset":"\033[0m","cyan":"\033[96m",
             "green":"\033[92m","yellow":"\033[93m","red":"\033[91m",
             "magenta":"\033[95m","dim":"\033[2m"}
    return f"{codes.get(code,'')}{text}{codes['reset']}"


# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────

def _banner():
    print(_c("cyan", """
╔══════════════════════════════════════════════════════╗
║          PROJECT BRAIN  —  Orchestrator CLI          ║
║              Type 'help' for commands                ║
╚══════════════════════════════════════════════════════╝
"""))


def _divider(label: str = ""):
    w = 56
    if label:
        pad = (w - len(label) - 2) // 2
        print(_c("dim", "─" * pad + f" {label} " + "─" * pad))
    else:
        print(_c("dim", "─" * w))


def _print_status():
    b = brain.get_brain()
    meta = b["_meta"]
    ss   = b["site_structure"]
    _divider("BRAIN STATUS")
    print(f"  Schema Version : {meta['schema_version']}")
    print(f"  Last Updated   : {meta['last_updated']}")
    print(f"  Brain Status   : {_c('green', meta['status'])}")
    print(f"  Project Name   : {_c('bold', ss.get('project_name') or '(not set)')}")
    print(f"  Pages          : {len(b['page_registry'])}")
    print(f"  Routes         : {len(b['route_registry'])}")
    print(f"  Uploaded Assets: {len(b['asset_registry'])}")
    print(f"  Generated Assets: {len(b['generated_assets'])}")
    pending = [r for r in b["tool_requests"] if r["status"] == "PENDING"]
    print(f"  Pending Tasks  : {_c('yellow', str(len(pending)))}")
    print(f"  Log Entries    : {len(b['execution_log'])}")
    _divider()


def _print_pages():
    pages = brain.list_pages()
    if not pages:
        print(_c("yellow", "  (no pages registered)"))
        return
    _divider("PAGE REGISTRY")
    for pid, p in pages.items():
        d = p["data"]
        status_color = "green" if d["status"] == "READY" else "yellow"
        print(f"  {_c('cyan', pid)}  {_c('bold', d['name']):<18} "
              f"{d['route']:<22} [{d['template']}]  "
              f"{_c(status_color, d['status'])}")
    _divider()


def _print_assets():
    assets = brain.list_assets()
    if not assets:
        print(_c("yellow", "  (no uploaded assets registered)"))
        return
    _divider("ASSET REGISTRY")
    for aid, a in assets.items():
        d = a["data"]
        status_color = "green" if d["status"] == "READY" else "yellow"
        print(f"  {_c('cyan', aid)}  {d.get('original_filename','?'):<28} "
              f"vision={d.get('vision_id') or '—'}  "
              f"{_c(status_color, d['status'])}")
    _divider()


def _print_generated():
    gens = brain.list_generated()
    if not gens:
        print(_c("yellow", "  (no generated asset placeholders)"))
        return
    _divider("GENERATED ASSETS")
    for gid, g in gens.items():
        d = g["data"]
        status_color = "green" if d["status"] == "DONE" else (
                       "red" if d["status"] == "FAILED" else "yellow")
        prompt_preview = (d.get("prompt") or "")[:50]
        print(f"  {_c('cyan', gid)}  [{d['aspect_ratio']}]  "
              f"{_c(status_color, d['status'])}")
        print(f"         prompt: {_c('dim', prompt_preview)}…")
    _divider()


def _print_log(n: int = 10):
    log = brain.get_execution_log()[-n:]
    _divider(f"EXECUTION LOG (last {len(log)})")
    for entry in log:
        ts = entry["timestamp"][11:19]   # HH:MM:SS
        print(f"  {_c('dim', ts)}  {_c('magenta', entry['event']):<35} {entry['details'][:60]}")
    _divider()


def _print_queue(tool: str = ""):
    reqs = brain.list_pending_requests(tool.upper() if tool else None)
    label = f"QUEUE — {tool.upper()}" if tool else "TOOL QUEUE (ALL PENDING)"
    _divider(label)
    if not reqs:
        print(_c("yellow", "  (queue is empty)"))
    for r in reqs:
        print(f"  {_c('cyan', r['request_id'])}  tool={_c('bold', r['tool']):<10} "
              f"target={r.get('target_id','?')}  pri={r.get('priority',5)}")
    _divider()


def _print_memories():
    mems = brain.list_memories()
    _divider("AGENT MEMORY REGISTRY")
    if not mems:
        print(_c("yellow", "  (no memories stored yet)"))
        _divider()
        return
    for mid, m in sorted(mems.items(), key=lambda x: x[0]):
        d = m["data"]
        imp = d.get("importance", 3)
        imp_label = f"imp:{imp}"
        importance_color = "red" if imp >= 4 else ("yellow" if imp >= 3 else "dim")
        key_label = d.get("key", "")
        print(f"  {_c('cyan', mid)}  [{_c(importance_color, imp_label)}]  "
              f"key={_c('bold', key_label):<20}  {d.get('content', '')}")
    _divider()


def _print_blueprints():
    b = brain.get_brain()
    blueprints = b.get("ui_blueprints", {})
    _divider("UI BLUEPRINT REGISTRY")
    if not blueprints:
        print(_c("yellow", "  (no UI blueprints generated yet)"))
        _divider()
        return
    for bpid, bp in blueprints.items():
        d = bp.get("data", {})
        theme = d.get("theme_tokens", {})
        n_sec = len(d.get("sections", []))
        page_id = d.get("page_id", "?")
        primary = theme.get("primary_color", "?")
        font_h  = theme.get("font_heading", "?")
        print(f"  {_c('cyan', bpid)}  page={_c('bold', page_id):<12} "
              f"sections={n_sec}  primary={primary}  font={font_h}")
    _divider()


def _print_outputs():
    from pathlib import Path
    output_dir = Path(__file__).parent.parent / "output"
    _divider("BUILT PAGE OUTPUTS")
    if not output_dir.exists():
        print(_c("yellow", "  (no output directory yet)"))
        _divider()
        return
    html_files = list(output_dir.rglob("index.html")) + list(output_dir.glob("*.html"))
    if not html_files:
        print(_c("yellow", "  (no HTML pages built yet — run CODE agent)"))
        _divider()
        return
    for f in sorted(html_files):
        size_kb = round(f.stat().st_size / 1024, 1)
        lines   = len(f.read_text(encoding="utf-8").splitlines())
        try:
            rel_path = f.relative_to(output_dir)
        except ValueError:
            rel_path = f.name
        print(f"  {_c('green', str(rel_path)):<30}  {size_kb:>6} KB  {lines} lines")
        # Check for CSS and JS
        parent = f.parent
        for sub_name in ("style.css", "script.js"):
            sub_file = parent / sub_name
            if sub_file.exists():
                sub_size = round(sub_file.stat().st_size / 1024, 1)
                sub_lines = len(sub_file.read_text(encoding="utf-8").splitlines())
                print(f"    * {sub_name:<28}  {sub_size:>6} KB  {sub_lines} lines")
    _divider()


# ─────────────────────────────────────────────────────────────────────────────
# Command dispatcher
# ─────────────────────────────────────────────────────────────────────────────

HELP_TEXT = """
  new <request>               Parse + commit a user request to Brain
  status                      Brain summary
  pages                       Page registry
  assets                      Uploaded asset registry
  generated                   Generated asset registry
  blueprints                  UI blueprint registry
  outputs                     Built page file list
  log [N]                     Last N log entries (default 10)
  queue [TOOL]                Pending tool requests
  run [VISION|IMAGEN|COMPRESS|CONTENT|UI|CODE|ALL]
                              Execute pending requests via Agent Bridge
  add-asset <local_path>      Register a local image as an asset and queue tasks
  set-prompt <GEN-ID> <prompt>
                              Update an IMAGEN prompt before running
  memories                    Show all stored agent memories
  remember <key> <content>    Store a new memory rule or observation
  context [PAGE-ID]           Print context summary (optionally scoped to page)
  reset                       !! Wipe and reinitialise Project Brain
  help                        This text
  exit / quit                 Exit
"""


def dispatch(line: str) -> bool:
    """Execute a single CLI command. Returns False to exit."""
    parts = line.strip().split(None, 1)
    if not parts:
        return True
    cmd   = parts[0].lower()
    rest  = parts[1] if len(parts) > 1 else ""

    if cmd in ("exit", "quit"):
        print(_c("dim", "Goodbye."))
        return False

    elif cmd == "help":
        print(HELP_TEXT)

    elif cmd == "status":
        _print_status()

    elif cmd == "pages":
        _print_pages()

    elif cmd == "assets":
        _print_assets()

    elif cmd == "generated":
        _print_generated()

    elif cmd == "blueprints":
        _print_blueprints()

    elif cmd == "outputs":
        _print_outputs()

    elif cmd == "log":
        n = int(rest.strip()) if rest.strip().isdigit() else 10
        _print_log(n)

    elif cmd == "queue":
        _print_queue(rest.strip())

    elif cmd == "run":
        tool = rest.strip().upper() or "ALL"
        print(_c("yellow", f"  Running Agent Bridge for: {tool} ...\n"))
        if tool in ("VISION", "ALL"):
            bridge.run_vision_requests()
        if tool in ("IMAGEN", "ALL"):
            bridge.run_imagen_requests()
        if tool in ("COMPRESS", "ALL"):
            bridge.run_compress_requests()
        if tool in ("CONTENT", "ALL"):
            bridge.run_content_requests()
        if tool in ("UI", "ALL"):
            bridge.run_ui_requests()
        if tool in ("CODE", "ALL"):
            bridge.run_code_requests()
        print(_c("green", "\n  Bridge run complete."))

    elif cmd == "add-asset":
        if not rest.strip():
            print(_c("red", "  Usage: add-asset <local_path>"))
        else:
            lpath = rest.strip().strip('"').strip("'")
            if not os.path.exists(lpath):
                print(_c("red", f"  File not found: {lpath}"))
            else:
                fname = os.path.basename(lpath)
                asset_id = brain.upsert_entity("asset", {
                    "original_filename": fname,
                    "local_path":        lpath,
                    "compressed_path":   None,
                    "mime_type":         "image/unknown",
                    "size_bytes":        os.path.getsize(lpath),
                    "vision_id":         None,
                    "status":            "PENDING",
                })
                
                # Enqueue VISION analysis
                req_id_vision = brain.enqueue_tool_request({
                    "tool":      "VISION",
                    "target_id": asset_id,
                    "params":    {"image_path": lpath},
                    "priority":  2,
                })
                
                # Enqueue COMPRESS
                output_dir = ROOT / "output" / "assets"
                output_dir.mkdir(parents=True, exist_ok=True)
                name, _ = os.path.splitext(fname)
                compressed_path = str(output_dir / f"{name}.webp")
                
                req_id_compress = brain.enqueue_tool_request({
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
                
                print(_c("green", f"  ✓ Registered asset {asset_id}."))
                print(f"    Enqueued VISION request: {req_id_vision}")
                print(f"    Enqueued COMPRESS request: {req_id_compress}")

    elif cmd == "new":
        if not rest.strip():
            print(_c("red", "  Usage: new <your request>"))
        else:
            print(_c("yellow", f"  Parsing: \"{rest.strip()[:80]}\"…"))
            result = parse_and_commit(rest.strip())
            print(_c("green", "\n  ✓ Project Brain updated!\n"))
            print(f"  Pages    : {result['pages']}")
            print(f"  Routes   : {result['routes']}")
            print(f"  Assets   : {result['assets']}")
            print(f"  Generated: {result['generated']}")
            print(f"  Tasks    : {result['tool_requests']}")

    elif cmd == "set-prompt":
        sub_parts = rest.strip().split(None, 1)
        if len(sub_parts) < 2:
            print(_c("red", "  Usage: set-prompt <GEN-xxxx> <new prompt>"))
        else:
            gid, new_prompt = sub_parts[0], sub_parts[1]
            entity = brain.get_entity("generated_asset", gid)
            if not entity:
                print(_c("red", f"  Entity {gid} not found."))
            else:
                entity["data"]["prompt"] = new_prompt
                brain.upsert_entity("generated_asset", entity["data"],
                                    entity_id=gid)
                # Update the corresponding IMAGEN request
                b = brain.get_brain()
                for req in b["tool_requests"]:
                    if req.get("target_id") == gid and req["tool"] == "IMAGEN":
                        brain.update_tool_request(req["request_id"],
                                                  "PENDING",
                                                  None)
                        req["params"]["prompt"] = new_prompt
                        break
                print(_c("green", f"  ✓ Prompt for {gid} updated."))

    elif cmd == "memories":
        _print_memories()

    elif cmd == "remember":
        sub_parts = rest.strip().split(None, 1)
        if len(sub_parts) < 2:
            print(_c("red", "  Usage: remember <key> <content>"))
        else:
            key, content = sub_parts[0], sub_parts[1]
            mem_id = brain.upsert_entity("memory", {
                "key":        key,
                "content":    content,
                "importance": 3,
                "created_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
            })
            print(_c("green", f"  ✓ Memory stored as {mem_id}."))

    elif cmd == "context":
        page_id = rest.strip() if rest.strip() else None
        print(brain.get_context_summary(for_page_id=page_id))

    elif cmd == "reset":
        confirm = input(_c("red",
            "  ⚠ This will WIPE the Project Brain. Type 'yes' to confirm: "))
        if confirm.strip().lower() == "yes":
            _do_reset()
            print(_c("green", "  ✓ Project Brain reset."))
        else:
            print("  Cancelled.")

    else:
        print(_c("red", f"  Unknown command: '{cmd}'. Type 'help' for options."))

    return True


def _do_reset():
    """Re-initialise project_brain.json to empty state."""
    import json
    from datetime import datetime, timezone
    empty = {
        "_meta": {
            "schema_version": "1.0.0",
            "created_at":     datetime.now(timezone.utc).isoformat(),
            "last_updated":   datetime.now(timezone.utc).isoformat(),
            "status":         "INITIALIZED",
        },
        "site_structure":  {"site_id":None,"project_name":None,"base_url":None,"theme":None,"brand":{}},
        "page_registry":   {},
        "route_registry":  {},
        "asset_registry":  {},
        "image_descriptions": {},
        "generated_assets": {},
        "ui_blueprints":   {},
        "content":         {},
        "code_outputs":    {},
        "navigation_graph":{"nodes":[],"edges":[]},
        "seo_graph":       {},
        "memory_registry": {},
        "tool_requests":   [],
        "execution_log":   [{
            "log_id":"LOG-0001",
            "timestamp":datetime.now(timezone.utc).isoformat(),
            "event":"BRAIN_RESET",
            "actor":"CLI",
            "details":"Brain wiped and re-initialised by user.",
        }],
    }
    brain_file = Path(__file__).parent / "project_brain.json"
    with open(brain_file, "w", encoding="utf-8") as f:
        json.dump(empty, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Entry points
# ─────────────────────────────────────────────────────────────────────────────

def run_repl():
    """Interactive REPL."""
    _banner()
    _print_status()
    while True:
        try:
            line = input(_c("cyan", "brain> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not dispatch(line):
            break


def run_one_shot(args):
    """Run a single command from CLI args (non-interactive)."""
    dispatch(" ".join(args))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_one_shot(sys.argv[1:])
    else:
        run_repl()
