#!/usr/bin/env python3
"""Kokoro TTS daemon — keeps model warm, serves speech over Unix socket.

Loads the Kokoro-82M model once on startup, then serves TTS requests with
~100-200ms latency instead of 3-5s cold starts. Auto-shuts down after 10
minutes idle.

Socket: ~/.claude/soundbar/kokoro.sock
Protocol: newline-delimited JSON over Unix stream socket.

Requests:
  {"cmd": "health"}
  {"cmd": "speak", "text": "...", "voice": "af_heart", "volume": 100}

Responses:
  {"ok": true, ...}
  {"ok": false, "error": "..."}

Setup:
  python3 -m venv ~/.claude/soundbar/.venv
  ~/.claude/soundbar/.venv/bin/pip install kokoro soundfile

Run:
  ~/.claude/soundbar/.venv/bin/python3 kokoro_server.py
"""

import json
import os
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import wave

SOCK_PATH = os.path.expanduser("~/.claude/soundbar/kokoro.sock")
IDLE_TIMEOUT = 600  # 10 minutes


class ModelManager:
    """Lazily loads and caches Kokoro pipelines per language code."""

    def __init__(self):
        self.pipelines = {}
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.last_activity = time.time()

    def preload(self, lang_code="a"):
        """Load a pipeline in background thread."""
        def _load():
            self.get_pipeline(lang_code)
            self.ready.set()
        threading.Thread(target=_load, daemon=True).start()

    def get_pipeline(self, lang_code):
        with self.lock:
            if lang_code not in self.pipelines:
                from kokoro import KPipeline
                self.pipelines[lang_code] = KPipeline(
                    lang_code=lang_code, repo_id="hexgrad/Kokoro-82M"
                )
            self.last_activity = time.time()
            return self.pipelines[lang_code]

    def touch(self):
        self.last_activity = time.time()

    def idle_seconds(self):
        return time.time() - self.last_activity


model = ModelManager()
# Serialize inference (model is not thread-safe)
inference_lock = threading.Lock()


class Handler(socketserver.StreamRequestHandler):

    def handle(self):
        try:
            line = self.rfile.readline().decode("utf-8").strip()
            if not line:
                return
            req = json.loads(line)
            cmd = req.get("cmd", "")

            if cmd == "health":
                model.touch()
                resp = {
                    "ok": True,
                    "ready": model.ready.is_set(),
                    "loaded": list(model.pipelines.keys()),
                    "idle": int(model.idle_seconds()),
                }
            elif cmd == "speak":
                resp = self._speak(req)
            else:
                resp = {"ok": False, "error": f"unknown command: {cmd}"}

            self.wfile.write(json.dumps(resp).encode() + b"\n")
            self.wfile.flush()
        except Exception as e:
            try:
                self.wfile.write(
                    json.dumps({"ok": False, "error": str(e)}).encode() + b"\n"
                )
                self.wfile.flush()
            except Exception:
                pass

    def _speak(self, req):
        import numpy as np

        text = req.get("text", "")
        voice = req.get("voice", "af_heart")
        volume = req.get("volume", 100)

        if not text:
            return {"ok": False, "error": "no text"}

        lang_code = "b" if voice.startswith("b") else "a"

        # Serialize inference — model is not thread-safe
        with inference_lock:
            pipeline = model.get_pipeline(lang_code)
            chunks = []
            for _gs, _ps, audio in pipeline(text, voice=voice):
                if audio is not None:
                    chunks.append(audio)

        if not chunks:
            return {"ok": False, "error": "no audio generated"}

        combined = np.concatenate(chunks)
        pcm = (np.clip(combined, -1.0, 1.0) * 32767).astype(np.int16)

        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="soundbar_kokoro_")
        os.close(fd)
        try:
            with wave.open(tmp, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(pcm.tobytes())

            vol_str = f"{volume // 100}.{volume % 100:02d}"
            subprocess.run(
                ["afplay", "-v", vol_str, tmp],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            return {"ok": True}
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def idle_watcher(server):
    """Shut down server after idle timeout."""
    while True:
        time.sleep(30)
        if model.idle_seconds() > IDLE_TIMEOUT:
            print("Idle timeout — shutting down.")
            server.shutdown()
            return


def cleanup():
    try:
        os.unlink(SOCK_PATH)
    except OSError:
        pass


def main():
    if "--check" in sys.argv:
        try:
            from kokoro import KPipeline  # noqa: F401
            print(json.dumps({"ok": True, "message": "Kokoro is installed and ready."}))
        except ImportError:
            print(json.dumps({
                "ok": False,
                "message": "Kokoro not installed. Run:\n"
                "  python3 -m venv ~/.claude/soundbar/.venv\n"
                "  ~/.claude/soundbar/.venv/bin/pip install kokoro soundfile"
            }))
        return

    # Clean up stale socket
    cleanup()

    def _shutdown(signum, frame):
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)

    # Preload American English pipeline in background
    model.preload("a")

    server = UnixServer(SOCK_PATH, Handler)
    os.chmod(SOCK_PATH, 0o600)

    # Start idle watcher
    threading.Thread(target=idle_watcher, args=(server,), daemon=True).start()

    print(f"Kokoro daemon: {SOCK_PATH} (PID {os.getpid()})")
    try:
        server.serve_forever()
    finally:
        cleanup()
    print("Kokoro daemon stopped.")


if __name__ == "__main__":
    main()
