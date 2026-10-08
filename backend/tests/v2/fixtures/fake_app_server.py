import json
import sys
import time
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
pending = []
waiting = None
def emit(frame):
    print(json.dumps(frame, ensure_ascii=False), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    method, rid = request.get("method"), request.get("id")
    if method == "initialize":
        emit({"id": rid, "result": {"userAgent": "fake/1"}})
    elif method == "initialized":
        continue
    elif method == "interleave":
        pending.append(rid)
        if len(pending) == 2:
            emit({"method": "delta", "params": {"delta": "\u4f60\u597d"}})
            for item in reversed(pending):
                emit({"id": item, "result": {"request": item}})
            pending.clear()
    elif method == "approval":
        waiting = rid
        emit({"id": "approval", "method": "item/commandExecution/requestApproval", "params": {}})
        emit({"method": "during/approval", "params": {}})
    elif rid == "approval":
        emit({"id": waiting, "result": request})
    elif method == "stall":
        continue
    elif method == "late":
        time.sleep(0.2)
        emit({"id": rid, "result": {"late": True}})
    elif method == "stderr":
        sys.stderr.write("private-token\n" * 10000)
        sys.stderr.flush()
        emit({"id": rid, "result": {"ok": True}})
    elif method == "malformed":
        print("{broken", flush=True)
    elif method == "invalid-utf8":
        sys.stdout.buffer.write(b"\xff\n")
        sys.stdout.buffer.flush()
    elif method == "large":
        emit({"id": rid, "result": {"text": "x" * 5000}})
    elif method == "flood":
        for i in range(30):
            emit({"method": "event", "params": {"i": i}})
        emit({"id": rid, "result": {}})
    elif method == "crash":
        sys.exit(2)
    elif method == "error":
        emit({"id": rid, "error": {"code": -32001, "message": "secret-token"}})
    elif method == "split":
        raw = (json.dumps({"method": "text", "params": {"text": "\u4e2d\u6587"}}, ensure_ascii=False) + "\n").encode()
        for byte in raw:
            sys.stdout.buffer.write(bytes([byte]))
            sys.stdout.buffer.flush()
        emit({"id": rid, "result": {"ok": True}})
    else:
        emit({"id": rid, "result": {"ok": True}})
