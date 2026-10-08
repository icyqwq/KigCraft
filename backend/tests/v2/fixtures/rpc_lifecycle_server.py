"""Wire-level test peer; no credentials, network or model calls."""
import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

def emit(frame):
    print(json.dumps(frame), flush=True)

for line in sys.stdin:
    frame = json.loads(line)
    method = frame.get("method")
    if method == "initialize":
        emit({"id": frame["id"], "result": {"userAgent": "lifecycle-fake/1"}})
    elif method == "initialized":
        continue
    elif method == "emit":
        for item in frame["params"]["frames"]:
            emit(item)
        emit({"id": frame["id"], "result": {}})
    elif method == "crash":
        sys.exit(2)
    elif method == "stall":
        continue
    elif method:
        emit({"id": frame["id"], "result": {"ok": True}})
    else:
        emit({"method": "server/reply", "params": {"frame": frame}})
