from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path


CACHE_SCHEMA = 1
MAX_FRAGMENT_LENGTH = 128
MAX_BUNDLE_WINDOW = 32 * 1024
SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]{0,127}")
SAFE_FRAGMENT_RE = re.compile(r"[A-Za-z0-9._~-]{1,128}")
SCRIPT_SRC_RE = re.compile(r"<script[^>]+\bsrc\s*=\s*(['\"])([^'\"]+)\1", re.IGNORECASE)


class ProtocolDiscoveryError(RuntimeError):
    """The viewer bundle did not match the static protocol shape we support."""


@dataclass(frozen=True)
class ProtocolAdapter:
    wrapper_name: str
    key_prefix: str
    image_query_key: str
    key_suffix: str

    def validate(self) -> None:
        if not SAFE_IDENTIFIER_RE.fullmatch(self.wrapper_name):
            raise ValueError("protocol wrapper name is invalid")
        for label, value in (
            ("key prefix", self.key_prefix),
            ("image query key", self.image_query_key),
            ("key suffix", self.key_suffix),
        ):
            if len(value) > MAX_FRAGMENT_LENGTH or not SAFE_FRAGMENT_RE.fullmatch(value):
                raise ValueError(f"protocol {label} is invalid")

    def payload_pattern(self) -> re.Pattern[str]:
        return re.compile(
            rf"{re.escape(self.wrapper_name)}\(\s*(['\"])([A-Za-z0-9+/=]+)\1\s*\)"
        )


# This is only a compatibility fallback. A normal first extraction discovers the
# current adapter from the authenticated viewer bundle and then caches it locally.
LEGACY_ADAPTER = ProtocolAdapter(
    wrapper_name="Hah6lu3wie",
    key_prefix="uc5xi",
    image_query_key="ndw2j",
    key_suffix="ndw2j",
)


def default_protocol_cache_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "BookRoll-Automation" / "protocol-cache"
    return Path.home() / ".cache" / "BookRoll-Automation" / "protocol-cache"


def cache_key(base_url: str) -> str:
    normalized = base_url.rstrip("/").encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def cache_path(base_url: str, cache_dir: Path | None = None) -> Path:
    root = cache_dir if cache_dir is not None else default_protocol_cache_dir()
    return root / f"{cache_key(base_url)}.json"


def load_cached_adapter(base_url: str, cache_dir: Path | None = None) -> ProtocolAdapter | None:
    path = cache_path(base_url, cache_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != CACHE_SCHEMA or data.get("base_url_hash") != cache_key(base_url):
            return None
        adapter = ProtocolAdapter(**data["adapter"])
        adapter.validate()
        return adapter
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def save_cached_adapter(base_url: str, adapter: ProtocolAdapter, cache_dir: Path | None = None) -> Path:
    """Persist only non-secret protocol shape under a non-reversible URL fingerprint."""
    adapter.validate()
    path = cache_path(base_url, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": CACHE_SCHEMA,
        "base_url_hash": cache_key(base_url),
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "adapter": asdict(adapter),
    }
    if path.exists():
        existing = load_cached_adapter(base_url, cache_dir)
        if existing == adapter:
            return path
        backup = path.with_name(f"{path.name}.bak-v1-{time.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(path, backup)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def viewer_script_urls(viewer_html: str) -> list[str]:
    return [src for _, src in SCRIPT_SRC_RE.findall(viewer_html) if re.search(r"(?:^|/)app(?:\.[^/]+)?\.js(?:\?|$)", src)]


def _assignment_values(window: str) -> dict[str, str]:
    return {
        name: value
        for name, value in re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*['\"]([^'\"]{1,128})['\"]", window)
        if SAFE_FRAGMENT_RE.fullmatch(value)
    }


def discover_adapter_from_bundle(bundle: str) -> ProtocolAdapter:
    """Statically read the narrow getBookImage implementation; never execute JavaScript."""
    markers = [match.start() for match in re.finditer("getBookImage", bundle)]
    if not markers:
        raise ProtocolDiscoveryError("viewer bundle has no getBookImage implementation")
    for marker in reversed(markers):
        window = bundle[marker : marker + MAX_BUNDLE_WINDOW]
        wrapper = re.search(r"new\s+Function\(\s*['\"]([A-Za-z_$][A-Za-z0-9_$]{0,127})['\"]\s*,", window)
        key_expression = re.search(
            r"Utf8(?:\[['\"]parse['\"]\]|\.parse)\(\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\+\s*[^+;)]{1,160}?\.iv\s*\+\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\)",
            window,
        )
        if wrapper is None or key_expression is None:
            continue
        query_expression = re.search(
            r"['\"]/image/['\"]\)\.concat\([^)]*,\s*['\"]\?['\"]\)\.concat\(\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\)",
            window,
        )
        assignments = _assignment_values(window)
        prefix_name, suffix_name = key_expression.groups()
        query_name = query_expression.group(1) if query_expression is not None else suffix_name
        try:
            adapter = ProtocolAdapter(
                wrapper_name=wrapper.group(1),
                key_prefix=assignments[prefix_name],
                image_query_key=assignments[query_name],
                key_suffix=assignments[suffix_name],
            )
        except KeyError:
            continue
        adapter.validate()
        return adapter
    raise ProtocolDiscoveryError("viewer bundle did not expose the expected static image decoder")
