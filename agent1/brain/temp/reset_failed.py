import sys
from pathlib import Path
ROOT = Path("c:/Users/USER/Desktop/agent1")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "brain"))

import orchestrator as brain

def reset_failed():
    b = brain.get_brain()
    count = 0
    for req in b.get("tool_requests", []):
        if req["status"] == "FAILED":
            print(f"Resetting request {req['request_id']} ({req['tool']} for {req.get('target_id')}) to PENDING")
            req["status"] = "PENDING"
            if "result" in req:
                del req["result"]
            count += 1
    
    if count > 0:
        brain._save(b)
        print(f"Successfully reset {count} failed request(s) to PENDING.")
    else:
        print("No failed requests found to reset.")

if __name__ == "__main__":
    reset_failed()
