import json, os, re, urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

URL = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")
TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")
DEFAULT = {
    "unlocked": 1,
    "best_score": 0,
    "difficulty": "normal",
    "career": {"kills": 0, "wins": 0, "towers": 0, "upgrades": 0,
               "endless_best": 0, "earned": []},
}
MAX_LEVEL = 10
MAX_COUNTER = 1_000_000_000
DIFFICULTIES = {"casual", "normal", "hard"}
ACHIEVEMENTS = {"firstKill", "captain", "firstUpgrade", "perfect",
                "campaign", "marathon"}

def _count(value, fallback=0):
    if isinstance(value, bool):
        return fallback
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return min(MAX_COUNTER, max(0, value))

def merge_progress(old, new):
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    old_career = old.get("career") if isinstance(old.get("career"), dict) else {}
    new_career = new.get("career") if isinstance(new.get("career"), dict) else {}

    old_unlocked = _count(old.get("unlocked"), DEFAULT["unlocked"])
    new_unlocked = _count(new.get("unlocked"), DEFAULT["unlocked"])
    old_difficulty = old.get("difficulty")
    new_difficulty = new.get("difficulty")
    earned = {
        item
        for source in (old_career.get("earned", []), new_career.get("earned", []))
        if isinstance(source, list)
        for item in source
        if isinstance(item, str) and item in ACHIEVEMENTS
    }
    career = {
        key: max(_count(old_career.get(key)), _count(new_career.get(key)))
        for key in ("kills", "wins", "towers", "upgrades", "endless_best")
    }
    career["earned"] = sorted(earned)

    difficulty = new_difficulty if isinstance(new_difficulty, str) and new_difficulty in DIFFICULTIES else \
                 old_difficulty if isinstance(old_difficulty, str) and old_difficulty in DIFFICULTIES else "normal"
    return {
        "unlocked": min(MAX_LEVEL, max(1, old_unlocked, new_unlocked)),
        "best_score": max(_count(old.get("best_score")), _count(new.get("best_score"))),
        "difficulty": difficulty,
        "career": career,
    }

def redis(cmd):
    if not URL or not TOKEN:
        raise RuntimeError("Redis is not configured")
    req = urllib.request.Request(
        URL, data=json.dumps(cmd).encode(),
        headers={"Authorization": "Bearer " + TOKEN,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)["result"]

def load(pid):
    r = redis(["GET", "td:" + pid])
    return merge_progress(DEFAULT, json.loads(r)) if r else merge_progress(DEFAULT, {})

class handler(BaseHTTPRequestHandler):
    def _pid(self):
        q = parse_qs(urlparse(self.path).query)
        return re.sub(r"[^a-zA-Z0-9-]", "", q.get("id", ["anon"])[0])[:40] or "anon"

    def _send(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._send(load(self._pid()))
        except Exception as e:
            self._send({"error": str(e)}, 500)

    def do_POST(self):
        try:
            pid = self._pid()
            size = int(self.headers.get("Content-Length", 0))
            new = json.loads(self.rfile.read(size) or "{}")
            old = load(pid)
            merged = merge_progress(old, new)
            redis(["SET", "td:" + pid, json.dumps(merged)])
            self._send(merged)
        except Exception as e:
            self._send({"error": str(e)}, 500)
