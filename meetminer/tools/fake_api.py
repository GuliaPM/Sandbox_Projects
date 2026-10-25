"""A stand-in for the Anthropic Messages API that returns canned answers. For testing the app for free.

    python tools/fake_api.py 8765
    set ANTHROPIC_BASE_URL=http://127.0.0.1:8765   (and any dummy ANTHROPIC_API_KEY), then run the app

The canned answer is NOT derived from your transcripts. It exists to exercise the cost meter, the
prompt cache fields, and the citation check (it deliberately includes one invented quote).
The most recent request body is saved to tools/fake_last_request.json for inspection.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

LOG = Path(__file__).with_name("fake_last_request.json")
STATE = {"calls": 0}

GOOD = (
    "The launch moved to November 14th because the payment provider integration is behind: "
    '"we can\'t test it properly before then" [Product planning call, Speaker 1, 00:00]. '
    "The price is \"$25 per seat per month\" [Customer onboarding call, Speaker 1, 00:00].\n\n"
    'The customer also asked for "a full refund guarantee forever" [Customer onboarding call, Speaker 3, 02:15].'
)
NOT_FOUND = "Not found in the transcripts. I looked for Kubernetes and found nothing."

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("request-id", "req_fake123")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if self.path.startswith("/v1/messages/count_tokens"):
            return self._send({"input_tokens": 10950})
        STATE["calls"] += 1
        shown = dict(req)
        shown["system"] = [{**b, "text": f"{b['text'][:80]}... ({len(b['text'])} chars)"} for b in req["system"]]
        LOG.write_text(json.dumps(shown, indent=2), encoding="utf-8")
        question = req["messages"][-1]["content"]
        first = STATE["calls"] == 1
        self._send({
            "id": "msg_fake", "type": "message", "role": "assistant", "model": req["model"],
            "content": [{"type": "text", "text": NOT_FOUND if "Kubernetes" in question else GOOD}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 120, "output_tokens": 350,
                      "cache_creation_input_tokens": 10900 if first else 0,
                      "cache_read_input_tokens": 0 if first else 10900},
        })


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Fake Anthropic API on http://127.0.0.1:{port} (Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
