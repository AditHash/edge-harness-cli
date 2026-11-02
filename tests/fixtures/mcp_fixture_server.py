"""Minimal deterministic stdio MCP fixture for exercising the real client transport."""

import json
import os
import sys

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        result = {
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fixture", "version": "1"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "probe",
                    "description": "Check sandbox isolation",
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                }
            ]
        }
    elif method == "tools/call":
        safe = (
            not os.path.exists("/home")
            and not os.path.exists("/mnt")
            and not os.path.exists("/workspace/.env")
        )
        result = {
            "content": [{"type": "text", "text": "isolated" if safe else "unsafe"}],
            "isError": False,
        }
    else:
        print(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "not found"},
                }
            ),
            flush=True,
        )
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
