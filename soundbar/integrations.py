"""Soundbar — Modular integration system for on-demand Python venv-based tools.

Each integration manages:
- Python version detection (direct binaries, uv, pyenv, conda)
- Venv creation with fallbacks (ensurepip, uv, pyvenv.cfg repair)
- Package installation (pip or uv pip)
- State persistence via integrations.json
- In-memory install progress tracking

Usage:
    from integrations import kokoro
    kokoro.find_python()      # detect compatible Python
    kokoro.is_installed()     # check if ready
    kokoro.install()          # background install (thread entry point)
    kokoro.progress           # {"status": ..., "message": ...}
"""

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

SND = Path(__file__).parent
INTEGRATIONS_FILE = SND / "integrations.json"

# ── File loggers (same debug.log / error.log as server.py) ──

_dlog = logging.getLogger("soundbar.integrations.debug")
_elog = logging.getLogger("soundbar.integrations.errors")

_loggers_initialized = False


def _setup_loggers():
    """Set up debug.log and error.log handlers (idempotent)."""
    global _loggers_initialized
    if _loggers_initialized:
        return
    _loggers_initialized = True
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    # debug.log — everything (DEBUG+)
    dh = logging.FileHandler(SND / "debug.log", mode="a")
    dh.setLevel(logging.DEBUG)
    dh.setFormatter(fmt)
    _dlog.addHandler(dh)
    _dlog.setLevel(logging.DEBUG)
    # error.log — errors only
    eh = logging.FileHandler(SND / "error.log", mode="a")
    eh.setLevel(logging.ERROR)
    eh.setFormatter(fmt)
    _elog.addHandler(eh)
    _elog.setLevel(logging.ERROR)


log = logging.getLogger("soundbar")


# ── State persistence ──

def read_integrations():
    try:
        return json.loads(INTEGRATIONS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_integration(key, value):
    data = read_integrations()
    data[key] = value
    INTEGRATIONS_FILE.write_text(json.dumps(data, indent=2) + "\n")
    log.info("wrote %s (%s=%s)", INTEGRATIONS_FILE.name, key, value)


# ── Python version detection ──

def _get_python_version(python_path):
    """Get (major, minor) tuple for a Python binary, or None."""
    try:
        r = subprocess.run(
            [python_path, "-c", "import sys; print(sys.version_info.major, sys.version_info.minor)"],
            capture_output=True, text=True, timeout=5,
        )
        _dlog.debug("version check %s: rc=%s stdout=%r stderr=%r", python_path, r.returncode, r.stdout.strip(), r.stderr.strip()[:200])
        if r.returncode == 0:
            parts = r.stdout.strip().split()
            return (int(parts[0]), int(parts[1]))
    except Exception as e:
        _dlog.debug("version check %s: exception %s", python_path, e)
    return None


# ── VenvIntegration base class ──

class VenvIntegration:
    """Base for on-demand Python venv-based integrations."""

    def __init__(self, name, packages, post_install_packages,
                 python_min, python_max, verify_import, venv_dir=None):
        self.name = name
        self.packages = packages
        self.post_install_packages = post_install_packages
        self.python_min = python_min
        self.python_max = python_max
        self.verify_import = verify_import
        self.venv_dir = venv_dir or (SND / ".venv")
        self.venv_py = self.venv_dir / "bin" / "python"
        self._progress = {"status": "idle", "message": ""}
        self._lock = threading.Lock()

    # ── Progress tracking ──

    @property
    def progress(self):
        return dict(self._progress)

    def _set_progress(self, status, message):
        self._progress["status"] = status
        self._progress["message"] = message

    # ── State (integrations.json) ──

    def is_installed(self):
        """Check if integration is actually importable. Fast path via integrations.json flag."""
        _setup_loggers()
        data = read_integrations()
        if data.get(f"{self.name}_installed"):
            return True
        # Flag not set — verify by importing
        if not self.venv_py.exists():
            return False
        try:
            r = subprocess.run(
                [str(self.venv_py), "-c", self.verify_import],
                capture_output=True, timeout=120,
            )
            if r.returncode == 0:
                write_integration(f"{self.name}_installed", True)
                return True
        except Exception:
            pass
        return False

    def status(self):
        """Return status dict for API response."""
        _setup_loggers()
        if self._progress["status"] == "running":
            return {
                "ok": True, "status": "running",
                "message": self._progress["message"],
                "installed": False,
            }
        elif self._progress["status"] == "error":
            py_info = self.find_python()
            return {
                "ok": False, "status": "error",
                "message": self._progress["message"],
                "installed": False, "python_info": py_info,
            }
        else:
            installed = self.is_installed()
            py_info = self.find_python()
            return {
                "ok": installed,
                "status": "done" if installed else "idle",
                "installed": installed, "python_info": py_info,
            }

    # ── Python detection ──

    def find_python(self):
        """Find a compatible Python for venv creation.

        Detection order:
        1. Direct binaries: python3.11, python3.10, python3.9 in PATH
        2. uv: can create venvs with specific Python versions
        3. pyenv: check installed versions
        4. conda: check for compatible environments

        Returns dict with keys:
          - ok: bool
          - python: str (absolute path to python binary)
          - method: str ("direct", "uv", "pyenv", "conda")
          - version: str (e.g. "3.11") — human-readable
          - message: str — describes what was found or what failed
        """
        _setup_loggers()
        _dlog.debug("=== %s find_python start ===", self.name)

        # Fast path: cached in integrations.json
        data = read_integrations()
        cached = data.get(f"{self.name}_python")
        _dlog.debug("cached %s_python: %s", self.name, cached)
        if cached and isinstance(cached, dict) and cached.get("ok"):
            py = cached.get("python", "")
            _dlog.debug("cached python: %s exists=%s executable=%s", py, os.path.isfile(py) if py else False, os.access(py, os.X_OK) if py else False)
            if py and os.path.isfile(py) and os.access(py, os.X_OK):
                ver = _get_python_version(py)
                if ver and self.python_min <= ver < self.python_max:
                    _dlog.debug("using cached python: %s (%s)", py, ver)
                    return cached

        tried = []

        # 1. Direct binaries in PATH
        max_minor = self.python_max[1] - 1  # exclusive upper → inclusive
        min_minor = self.python_min[1]
        for minor in range(max_minor, min_minor - 1, -1):
            name = f"python3.{minor}"
            path = shutil.which(name)
            if path:
                ver = _get_python_version(path)
                if ver and self.python_min <= ver < self.python_max:
                    result = {
                        "ok": True, "python": path, "method": "direct",
                        "version": f"{ver[0]}.{ver[1]}",
                        "message": f"Using {name} from PATH",
                    }
                    write_integration(f"{self.name}_python", result)
                    return result
            tried.append(name)

        # 2. uv — install + find a compatible Python binary
        uv_bin = shutil.which("uv")
        _dlog.debug("uv binary: %s", uv_bin)
        if uv_bin:
            try:
                target_ver = f"3.{max_minor}"
                _dlog.debug("running: uv python install %s", target_ver)
                r_install = subprocess.run(
                    [uv_bin, "python", "install", target_ver],
                    capture_output=True, text=True, timeout=120,
                )
                _dlog.debug("uv python install: rc=%s stdout=%r stderr=%r", r_install.returncode, r_install.stdout.strip()[:200], r_install.stderr.strip()[:200])
                r = subprocess.run(
                    [uv_bin, "python", "find", target_ver],
                    capture_output=True, text=True, timeout=10,
                )
                _dlog.debug("uv python find: rc=%s stdout=%r stderr=%r", r.returncode, r.stdout.strip(), r.stderr.strip()[:200])
                if r.returncode == 0:
                    raw_path = r.stdout.strip()
                    py_path = os.path.realpath(raw_path)
                    _dlog.debug("uv python path: raw=%s resolved=%s", raw_path, py_path)
                    _dlog.debug("uv python exists=%s executable=%s", os.path.isfile(py_path), os.access(py_path, os.X_OK))
                    ver = _get_python_version(py_path)
                    if ver and self.python_min <= ver < self.python_max:
                        result = {
                            "ok": True, "python": py_path, "method": "uv",
                            "version": f"{ver[0]}.{ver[1]}",
                            "message": f"Using Python {ver[0]}.{ver[1]} via uv",
                        }
                        write_integration(f"{self.name}_python", result)
                        return result
            except Exception:
                pass
        tried.append("uv")

        # 3. pyenv — check installed versions
        pyenv_bin = shutil.which("pyenv")
        if pyenv_bin:
            try:
                r = subprocess.run(
                    [pyenv_bin, "versions", "--bare"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        ver_str = line.strip()
                        if not ver_str:
                            continue
                        parts = ver_str.split(".")
                        if len(parts) >= 2:
                            try:
                                ver = (int(parts[0]), int(parts[1]))
                            except ValueError:
                                continue
                            if self.python_min <= ver < self.python_max:
                                # Find the actual binary
                                r2 = subprocess.run(
                                    [pyenv_bin, "root"],
                                    capture_output=True, text=True, timeout=5,
                                )
                                if r2.returncode == 0:
                                    pyenv_root = r2.stdout.strip()
                                    py_path = os.path.join(pyenv_root, "versions", ver_str, "bin", "python3")
                                    if os.path.isfile(py_path):
                                        result = {
                                            "ok": True, "python": py_path, "method": "pyenv",
                                            "version": ver_str,
                                            "message": f"Using Python {ver_str} from pyenv",
                                        }
                                        write_integration(f"{self.name}_python", result)
                                        return result
            except Exception:
                pass
        tried.append("pyenv")

        # 4. conda — check for compatible environments
        conda_bin = shutil.which("conda")
        if conda_bin:
            try:
                r = subprocess.run(
                    [conda_bin, "info", "--envs", "--json"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0:
                    info = json.loads(r.stdout)
                    for env_path in info.get("envs", []):
                        py_path = os.path.join(env_path, "bin", "python3")
                        if os.path.isfile(py_path):
                            ver = _get_python_version(py_path)
                            if ver and self.python_min <= ver < self.python_max:
                                result = {
                                    "ok": True, "python": py_path, "method": "conda",
                                    "version": f"{ver[0]}.{ver[1]}",
                                    "message": f"Using Python {ver[0]}.{ver[1]} from conda env",
                                }
                                write_integration(f"{self.name}_python", result)
                                return result
            except Exception:
                pass
        tried.append("conda")

        # 5. Nothing found
        min_str = f"{self.python_min[0]}.{self.python_min[1]}"
        max_str = f"{self.python_max[0]}.{self.python_max[1] - 1}"
        result = {
            "ok": False, "python": "", "method": "", "version": "",
            "message": (
                f"No compatible Python ({min_str}-{max_str}) found. "
                f"Checked: {', '.join(tried)}.\n"
                "Install one via:\n"
                f"  brew install python@{max_str}\n"
                f"  uv python install {max_str}\n"
                f"  pyenv install {max_str}"
            ),
        }
        write_integration(f"{self.name}_python", result)
        return result

    # ── Venv lifecycle ──

    def create_venv(self, py_info):
        """Create venv from py_info. Returns (ok, is_uv_python)."""
        method = py_info["method"]

        # Detect if "direct" python is actually uv-managed (symlink into uv store)
        py_real = os.path.realpath(py_info["python"])
        is_uv_python = "/uv/python/" in py_real or (method == "uv")
        _dlog.debug("python realpath: %s is_uv_python: %s", py_real, is_uv_python)

        self._set_progress("running", f"Creating venv ({py_info['message']})...")
        log.info("%s install: creating venv via %s at %s", self.name, method, self.venv_dir)

        if is_uv_python and shutil.which("uv"):
            # uv-managed Python: use uv venv (handles standalone builds correctly)
            cmd = ["uv", "venv", "--python", py_info["python"], str(self.venv_dir)]
            _dlog.debug("venv cmd (uv): %s", cmd)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        else:
            cmd = [py_info["python"], "-m", "venv", str(self.venv_dir)]
            _dlog.debug("venv cmd: %s", cmd)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            # If ensurepip fails, retry without pip — standalone/uv Pythons lack it
            if result.returncode != 0 and "ensurepip" in result.stderr:
                _dlog.debug("ensurepip failed, retrying with --without-pip")
                # Clean up partial venv
                shutil.rmtree(str(self.venv_dir), ignore_errors=True)
                cmd = [py_info["python"], "-m", "venv", "--without-pip", str(self.venv_dir)]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if result.returncode == 0:
                    is_uv_python = True  # force uv pip path for install step

        _dlog.debug("venv creation: rc=%s stdout=%r stderr=%r", result.returncode, result.stdout.strip()[:300], result.stderr.strip()[:300])

        if result.returncode != 0:
            self._set_progress("error", f"venv creation failed: {result.stderr.strip()[-200:]}")
            log.error("%s install: venv creation failed: %s", self.name, result.stderr.strip())
            _elog.error("venv creation failed: cmd=%s stderr=%s", cmd, result.stderr.strip())
            write_integration(f"{self.name}_python", None)
            return False, is_uv_python

        if not self.venv_py.exists():
            self._set_progress("error", "venv created but bin/python not found.")
            log.error("%s install: venv python missing after creation", self.name)
            # Dump venv directory contents for debugging
            try:
                venv_bin = self.venv_dir / "bin"
                if venv_bin.exists():
                    _dlog.debug("venv bin contents: %s", list(venv_bin.iterdir()))
                else:
                    _dlog.debug("venv bin dir missing. venv contents: %s", list(self.venv_dir.iterdir()) if self.venv_dir.exists() else "venv dir missing")
            except Exception:
                pass
            write_integration(f"{self.name}_python", None)
            return False, is_uv_python

        return True, is_uv_python

    def _verify_venv_python(self, py_info):
        """Verify venv python can start, repair pyvenv.cfg if broken. Returns True if ok."""
        # Dump pyvenv.cfg and venv python details for debugging
        pyvenv_cfg = self.venv_dir / "pyvenv.cfg"
        if pyvenv_cfg.exists():
            _dlog.debug("pyvenv.cfg contents:\n%s", pyvenv_cfg.read_text())
        else:
            _dlog.debug("pyvenv.cfg MISSING at %s", pyvenv_cfg)

        venv_py_real = os.path.realpath(str(self.venv_py))
        _dlog.debug("venv python: %s -> realpath: %s", self.venv_py, venv_py_real)

        # Verify venv python can start (catches broken pyvenv.cfg / standalone builds)
        verify = subprocess.run(
            [str(self.venv_py), "-c", "import sys; print('prefix:', sys.prefix, 'base:', sys.base_prefix, 'exec_prefix:', sys.exec_prefix)"],
            capture_output=True, text=True, timeout=10,
        )
        _dlog.debug("venv verify: rc=%s stdout=%r stderr=%r", verify.returncode, verify.stdout.strip()[:300], verify.stderr.strip()[:500])

        if verify.returncode == 0:
            return True

        _elog.error("venv python broken: stderr=%s", verify.stderr.strip()[:500])
        log.warning("%s install: venv python broken, attempting pyvenv.cfg repair", self.name)
        real_bin = os.path.dirname(os.path.realpath(py_info["python"]))
        _dlog.debug("repair target: home = %s (from python %s)", real_bin, py_info["python"])

        if not pyvenv_cfg.exists():
            self._set_progress("error", "venv python is broken and pyvenv.cfg not found.")
            log.error("%s install: pyvenv.cfg missing", self.name)
            _elog.error("pyvenv.cfg missing at %s", pyvenv_cfg)
            write_integration(f"{self.name}_python", None)
            return False

        try:
            original = pyvenv_cfg.read_text()
            _dlog.debug("pyvenv.cfg BEFORE repair:\n%s", original)
            lines = original.splitlines()
            new_lines = []
            for line in lines:
                if line.startswith("home"):
                    new_lines.append(f"home = {real_bin}")
                else:
                    new_lines.append(line)
            pyvenv_cfg.write_text("\n".join(new_lines) + "\n")
            _dlog.debug("pyvenv.cfg AFTER repair:\n%s", pyvenv_cfg.read_text())
            log.info("%s install: pyvenv.cfg home rewritten to %s", self.name, real_bin)
        except Exception as e:
            log.error("%s install: pyvenv.cfg repair failed: %s", self.name, e)
            _elog.error("pyvenv.cfg repair failed: %s", e)

        # Re-verify after repair
        verify2 = subprocess.run(
            [str(self.venv_py), "-c", "import sys; print('prefix:', sys.prefix, 'base:', sys.base_prefix)"],
            capture_output=True, text=True, timeout=10,
        )
        _dlog.debug("venv re-verify: rc=%s stdout=%r stderr=%r", verify2.returncode, verify2.stdout.strip()[:300], verify2.stderr.strip()[:500])
        if verify2.returncode != 0:
            self._set_progress("error",
                "venv python is broken (can't import stdlib). "
                "Try: uv python upgrade --reinstall"
            )
            log.error("%s install: venv python still broken after repair", self.name)
            _elog.error("venv still broken after repair: stderr=%s", verify2.stderr.strip()[:500])
            write_integration(f"{self.name}_python", None)
            return False

        return True

    def install_packages(self, is_uv_python):
        """Install main packages + post-install packages. Returns True on success."""
        # Main packages
        self._set_progress("running", f"Installing {self.name} + dependencies (this may take a few minutes)...")
        if is_uv_python and shutil.which("uv"):
            log.info("%s install: uv pip install %s", self.name, self.packages)
            result = subprocess.run(
                ["uv", "pip", "install", "--python", str(self.venv_py)] + self.packages,
                capture_output=True, text=True, timeout=600,
            )
        else:
            venv_pip = self.venv_dir / "bin" / "pip"
            if not venv_pip.exists():
                log.info("%s install: bootstrapping pip via ensurepip", self.name)
                subprocess.run(
                    [str(self.venv_py), "-m", "ensurepip", "--upgrade"],
                    capture_output=True, timeout=30,
                )
            log.info("%s install: pip install %s", self.name, self.packages)
            result = subprocess.run(
                [str(self.venv_py), "-m", "pip", "install", "-q"] + self.packages,
                capture_output=True, text=True, timeout=600,
            )
        _dlog.debug("pip install: rc=%s stdout=%r stderr=%r", result.returncode, result.stdout.strip()[:500], result.stderr.strip()[:500])
        if result.returncode != 0:
            self._set_progress("error", f"pip install failed: {result.stderr.strip()[-200:]}")
            log.error("%s install: pip install failed: %s", self.name, result.stderr.strip()[-200:])
            _elog.error("pip install failed: rc=%s stderr=%s", result.returncode, result.stderr.strip()[:1000])
            return False

        # Post-install packages (e.g. spaCy model)
        if self.post_install_packages:
            self._set_progress("running", "Downloading language model...")
            for pkg in self.post_install_packages:
                log.info("%s install: downloading %s", self.name, pkg.split("@")[0] if "@" in pkg else pkg)
                if is_uv_python and shutil.which("uv"):
                    result = subprocess.run(
                        ["uv", "pip", "install", "--python", str(self.venv_py), pkg],
                        capture_output=True, text=True, timeout=120,
                    )
                else:
                    result = subprocess.run(
                        [str(self.venv_py), "-m", "pip", "install", "-q", pkg],
                        capture_output=True, text=True, timeout=120,
                    )
                _dlog.debug("post-install %s: rc=%s stderr=%r", pkg, result.returncode, result.stderr.strip()[:300])
                if result.returncode != 0:
                    # Fallback: try spacy download command for spacy models
                    if "spacy" in pkg or "en_core_web" in pkg:
                        model_name = pkg.split("@")[0] if "@" in pkg else pkg
                        log.warning("%s install: post-install download failed, trying spacy download command", self.name)
                        subprocess.run(
                            [str(self.venv_py), "-m", "spacy", "download", model_name],
                            capture_output=True, text=True, timeout=120,
                        )

        return True

    def verify(self):
        """Verify the integration import works. Returns True on success."""
        log.info("%s install: verifying import", self.name)
        result = subprocess.run(
            [str(self.venv_py), "-c", f"{self.verify_import}; print('{self.name} ok')"],
            capture_output=True, text=True, timeout=120,
        )
        _dlog.debug("import verify: rc=%s stdout=%r stderr=%r", result.returncode, result.stdout.strip(), result.stderr.strip()[:500])
        if result.returncode != 0:
            self._set_progress("error", "Install succeeded but import failed.")
            log.error("%s install: import verification failed", self.name)
            _elog.error("import verification failed: stderr=%s", result.stderr.strip()[:500])
            return False
        return True

    # ── Install (background thread entry point) ──

    def install(self):
        """Full install pipeline: find python, create venv, install packages, verify."""
        _setup_loggers()
        _dlog.debug("=== %s install start ===", self.name)
        log.info("%s install started", self.name)
        self._set_progress("running", f"Finding compatible Python ({self.python_min[0]}.{self.python_min[1]}-{self.python_max[0]}.{self.python_max[1] - 1})...")
        try:
            py_info = self.find_python()
            _dlog.debug("python detection result: %s", py_info)
            if not py_info["ok"]:
                self._set_progress("error", py_info["message"])
                log.error("%s install: no compatible Python found", self.name)
                return

            method = py_info["method"]
            _dlog.debug("install method: %s python: %s", method, py_info.get("python"))

            if not self.venv_py.exists():
                ok, is_uv_python = self.create_venv(py_info)
                if not ok:
                    return
            else:
                # Venv already exists — detect uv for pip install path
                py_real = os.path.realpath(py_info["python"])
                is_uv_python = "/uv/python/" in py_real or (method == "uv")

            # Verify venv python works (repair pyvenv.cfg if needed)
            if not self._verify_venv_python(py_info):
                return

            # Install packages
            if not self.install_packages(is_uv_python):
                return

            # Verify import
            if not self.verify():
                return

            write_integration(f"{self.name}_installed", True)
            self._set_progress("done", "Installed. Daemon will auto-start on first use.")
            log.info("%s install completed successfully", self.name)
            _dlog.debug("=== %s install success ===", self.name)

        except subprocess.TimeoutExpired as e:
            self._set_progress("error", "Install timed out.")
            _elog.error("install timed out: %s", e)
            log.error("%s install timed out", self.name)
        except Exception as e:
            self._set_progress("error", str(e))
            log.error("%s install failed: %s", self.name, e)
            _elog.error("install exception: %s", e, exc_info=True)


# ── Pre-configured integrations ──

kokoro = VenvIntegration(
    name="kokoro",
    packages=["kokoro", "soundfile"],
    post_install_packages=[
        "en_core_web_sm@https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl",
    ],
    python_min=(3, 9),
    python_max=(3, 12),
    verify_import="import kokoro",
    venv_dir=None,  # defaults to SND / ".venv"
)
