"""Cache public GPU IDs and names without delaying warm CLI invocations."""

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict


CACHE_TTL = 24 * 60 * 60


def valid_gpu_types(value: object) -> bool:
    return isinstance(value, dict) and bool(value) and all(
        isinstance(key, str) and bool(key.strip()) and isinstance(name, str) and bool(name.strip())
        for key, name in value.items()
    )


def get_gpu_types(fetch: Callable[[], Dict[str, str]], refresh: bool = False) -> Dict[str, str]:
    """Use a fresh cache, or return stale names while refreshing in the background."""
    cache_root = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    path = Path(cache_root) / "runpod_cli" / "gpu-types.json"

    def fetch_and_save() -> Dict[str, str]:
        gpu_types = fetch()
        if not valid_gpu_types(gpu_types):
            raise ValueError("Invalid GPU catalog: expected nonempty GPU IDs and names")
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as dest:
                temporary = dest.name
                json.dump({"fetched_at": time.time(), "gpus": gpu_types}, dest)
            os.replace(temporary, path)
            temporary = None
        except OSError:
            logging.debug("GPU catalog cache could not be written", exc_info=True)
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        return gpu_types

    if not refresh:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            gpu_types = cached["gpus"]
            age = time.time() - float(cached["fetched_at"])
            if not valid_gpu_types(gpu_types):
                raise ValueError("Invalid cached GPU catalog")
        except (OSError, ValueError, KeyError, TypeError):
            pass
        else:
            if not 0 <= age < CACHE_TTL:
                def refresh_in_background() -> None:
                    try:
                        fetch_and_save()
                    except (RuntimeError, ValueError, OSError) as error:
                        logging.debug("GPU catalog refresh failed; keeping cached names: %s", error)

                threading.Thread(target=refresh_in_background, daemon=True, name="gpu-catalog-refresh").start()
            return gpu_types
    return fetch_and_save()
