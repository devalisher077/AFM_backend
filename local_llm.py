from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_MODEL_PATH = "models/Llama-3.2-3B-Instruct-Q4_K_M.gguf"
DEFAULT_MODEL_NAME = "local-llama-3.2-3b-instruct-q4"


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def local_model_name() -> str:
    return env_str("LOCAL_LLM_MODEL_NAME", env_str("LLM_MODEL", DEFAULT_MODEL_NAME))


def local_model_path() -> Path:
    raw = env_str("LOCAL_LLM_MODEL_PATH", env_str("LLM_MODEL_PATH", DEFAULT_MODEL_PATH))
    return Path(raw).expanduser()


def extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if not cleaned:
        return {"raw_text": ""}

    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", cleaned):
        try:
            parsed, _ = decoder.raw_decode(cleaned[match.start():])
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {"raw_text": cleaned[:4000]}


class LocalLlamaAnalyzer:
    def __init__(self, model_path: Optional[Path] = None) -> None:
        self.model_path = model_path or local_model_path()
        if not self.model_path.exists():
            raise RuntimeError(f"Local LLM model not found: {self.model_path}")

        self.model_name = local_model_name()
        self.max_tokens = max(64, env_int("LOCAL_LLM_MAX_TOKENS", env_int("LLM_MAX_TOKENS", 350)))
        self.temperature = max(0.0, env_float("LOCAL_LLM_TEMPERATURE", env_float("LLM_TEMPERATURE", 0.1)))
        self.n_ctx = max(2048, env_int("LOCAL_LLM_N_CTX", env_int("LLM_N_CTX", 4096)))
        self.n_batch = max(32, env_int("LOCAL_LLM_N_BATCH", env_int("LLM_N_BATCH", 256)))
        self.threads = max(1, env_int("LOCAL_LLM_THREADS", env_int("LLM_THREADS", os.cpu_count() or 4)))
        self.timeout = max(30, env_int("LOCAL_LLM_TIMEOUT_SECONDS", env_int("LLM_TIMEOUT_SECONDS", 180)))
        self.cli = env_str("LOCAL_LLM_CLI", env_str("LLM_CLI", "llama-completion"))
        if not shutil.which(self.cli):
            raise RuntimeError(
                f"Local LLM CLI not found: {self.cli}. Install llama.cpp or set LOCAL_LLM_CLI."
            )

        self._lock = threading.Lock()

    def build_command(self, prompt_file: Path) -> list[str]:
        cmd = [
            self.cli,
            "-m", str(self.model_path),
            "-f", str(prompt_file),
            "-n", str(self.max_tokens),
            "-ngl", "0",
            "--device", "none",
            "--no-op-offload",
            "--no-kv-offload",
            "-t", str(self.threads),
            "-c", str(self.n_ctx),
            "-b", str(self.n_batch),
            "--temp", str(self.temperature),
            "--no-display-prompt",
            "--no-warmup",
            "--no-perf",
            "--simple-io",
            "-no-cnv",
        ]
        if not env_bool("LOCAL_LLM_VERBOSE", False):
            cmd.extend(["--verbosity", "0"])
        return cmd

    def analyze_prompt(self, prompt: str) -> Dict[str, Any]:
        full_prompt = (
            "Ты аналитик риска. Ответь строго одним валидным JSON-объектом, "
            "без markdown и без пояснений вне JSON. После закрывающей скобки } сразу остановись.\n\n"
            f"{prompt}"
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as f:
            f.write(full_prompt)
            prompt_path = Path(f.name)

        try:
            with self._lock:
                proc = subprocess.run(
                    self.build_command(prompt_path),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=self.timeout,
                    check=False,
                )
            output = proc.stdout or ""
            if proc.returncode != 0:
                return {"error": output[-2000:] or f"llama-completion exited with {proc.returncode}"}
            parsed = extract_json_object(output)
            if parsed == {"raw_text": ""}:
                return {"error": "llama-completion returned empty output"}
            return parsed
        except subprocess.TimeoutExpired as exc:
            return {"error": f"llama-completion timed out after {self.timeout}s: {exc}"}
        finally:
            try:
                prompt_path.unlink()
            except OSError:
                pass


_CLIENT: Optional[LocalLlamaAnalyzer] = None


def load_local_llm() -> LocalLlamaAnalyzer:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = LocalLlamaAnalyzer()
    return _CLIENT
