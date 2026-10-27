"""Run the same native compiler as the game addon, on staged JSON only."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from .model import ServiceError, bundle_shape, parse_json, source_text

ROOT = Path(__file__).resolve().parents[2]


class GodotValidator:
    def __init__(self, godot: str = "godot", timeout: float = 30):
        self.godot = shutil.which(godot)
        if self.godot is None:
            raise RuntimeError("Godot is required for authoritative source validation")
        self.timeout = timeout
        self.slots = threading.BoundedSemaphore(2)
        self.sources = {"addons/arrow_dialogic/" + name: (ROOT / "addons/arrow_dialogic" / name).read_bytes()
                        for name in ("compiler.gd", "story.gd")}
        self.sources["validate.gd"] = Path(__file__).with_name("validate.gd").read_bytes()
        with tempfile.TemporaryDirectory(prefix="arrow-validator-version-") as directory:
            result = subprocess.run([self.godot, "--headless", "--version"], capture_output=True,
                                    text=True, timeout=10, env=self.environment(Path(directory)))
        if result.returncode or not result.stdout.strip():
            raise RuntimeError("Could not determine the Godot validation toolchain")
        self.version = result.stdout.strip()
        digest = hashlib.sha256(self.version.encode())
        for name, content in sorted(self.sources.items()):
            digest.update(name.encode())
            digest.update(content)
        self.fingerprint = digest.hexdigest()

    @staticmethod
    def environment(work: Path) -> dict:
        result = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"}
        for name, folder in [("XDG_DATA_HOME", ".data"), ("XDG_CONFIG_HOME", ".config"), ("XDG_CACHE_HOME", ".cache")]:
            (work / folder).mkdir(exist_ok=True)
            result[name] = str(work / folder)
        return result

    def validate(self, bundle: dict) -> dict:
        bundle_shape(bundle)
        if not self.slots.acquire(timeout=5):
            raise ServiceError(503, "validator_busy", "Validation workers are busy; retry later")
        try:
            with tempfile.TemporaryDirectory(prefix="arrow-validate-") as directory:
                work = Path(directory)
                for name, content in self.sources.items():
                    target = work / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                (work / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Arrow service validator"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n')
                story = work / "story"
                story.mkdir()
                (story / bundle["manifest_path"]).write_text(source_text(bundle["package"]), encoding="utf-8")
                for chapter, spec in bundle["package"]["documents"].items():
                    target = story / spec["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(source_text(bundle["documents"][chapter]), encoding="utf-8")
                (work / "request.json").write_text(source_text({"manifest_path": bundle["manifest_path"]}))
                process = subprocess.run([self.godot, "--headless", "--path", str(work), "--script", "res://validate.gd"],
                                         capture_output=True, text=True, timeout=self.timeout, env=self.environment(work))
                output = process.stdout + process.stderr
                if process.returncode or "SCRIPT ERROR:" in output or "\nERROR:" in output or not (work / "result.json").exists():
                    raise ServiceError(503, "validator_failed", "Godot validation did not complete")
                result = parse_json((work / "result.json").read_text())
                result.update(fingerprint=self.fingerprint, godot=self.version)
                return result
        except subprocess.TimeoutExpired as error:
            raise ServiceError(503, "validator_timeout", "Godot validation exceeded its time limit") from error
        except OSError as error:
            raise ServiceError(503, "validator_unavailable", "Validator staging or execution failed") from error
        finally:
            self.slots.release()
