from __future__ import annotations

import base64
import gzip
import hashlib
import html
import json
import os
import re
import time
import zlib
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pypdf import PdfReader, PdfWriter


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) "
    "Gecko/20100101 Firefox/151.0"
)
DEFAULT_BASE_URL = "https://example.invalid/bookroll"
IMAGE_KEY_PREFIX = "uc5xi"
IMAGE_QUERY_KEY = "ndw2j"
IMAGE_KEY_SUFFIX = "ndw2j"
PAYLOAD_RE = re.compile(r"Hah6lu3wie\((['\"])([^'\"]+)\1")
CONTENTS_RE = re.compile(r"contents=([0-9a-f]{32,})", re.IGNORECASE)
INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class Material:
    number: int
    title: str
    contents: str


@dataclass(frozen=True)
class MaterialPlan:
    number: int
    title: str
    contents: str
    output_dir: Path
    output_pdf: Path
    known_pages: int | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["output_dir"] = str(self.output_dir)
        value["output_pdf"] = str(self.output_pdf)
        return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BookRollLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href and CONTENTS_RE.search(href):
            self._current_href = href
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current_href is None:
            return
        title = html.unescape("".join(self._current_text)).strip()
        match = CONTENTS_RE.search(self._current_href)
        if match and title:
            self.links.append((match.group(1), title))
        self._current_href = None
        self._current_text = []


def safe_name(value: str) -> str:
    name = INVALID_FILENAME_RE.sub("_", value).strip(" .")
    if not name:
        return "material"
    if name.upper() in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"COM[1-9]|LPT[1-9]", name.upper()):
        return f"_{name}"
    return name


def parse_home_html(path: Path) -> list[Material]:
    parser = BookRollLinkParser()
    parser.feed(path.read_text(encoding="utf-8-sig"))
    seen: set[str] = set()
    materials: list[Material] = []
    for contents, title in parser.links:
        if contents in seen:
            continue
        seen.add(contents)
        materials.append(Material(len(materials) + 1, title, contents))
    if not materials:
        raise ValueError(f"no BookRoll links found in {path}")
    return materials


def load_page_counts(path: Path | None) -> dict[str, int]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    entries = data.get("items", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError(f"page-count file must contain an items list: {path}")
    counts: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict) or "contents" not in entry or "pages" not in entry:
            continue
        counts[str(entry["contents"])] = int(entry["pages"])
    return counts


def parse_selection(selection: str | None, total: int) -> set[int] | None:
    if not selection:
        return None
    selected: set[int] = set()
    for token in selection.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            first, last = token.split("-", 1)
            start, end = int(first), int(last)
            if start > end:
                start, end = end, start
            selected.update(range(start, end + 1))
        else:
            selected.add(int(token))
    invalid = sorted(number for number in selected if number < 1 or number > total)
    if invalid:
        raise ValueError(f"selection is outside 1..{total}: {invalid}")
    return selected


def build_plan(
    home_html: Path,
    batch_dir: Path,
    selection: str | None = None,
    known_page_counts: Path | None = None,
) -> list[MaterialPlan]:
    materials = parse_home_html(home_html)
    selected = parse_selection(selection, len(materials))
    page_counts = load_page_counts(known_page_counts)
    plans: list[MaterialPlan] = []
    for material in materials:
        if selected is not None and material.number not in selected:
            continue
        folder = batch_dir / f"{material.number:02d}_{safe_name(material.title)}"
        plans.append(
            MaterialPlan(
                number=material.number,
                title=material.title,
                contents=material.contents,
                output_dir=folder,
                output_pdf=folder / "output" / "pdf" / f"{material.number:02d}_{safe_name(material.title)}_bookroll.pdf",
                known_pages=page_counts.get(material.contents),
            )
        )
    if not plans:
        raise ValueError("selection produced no materials")
    return plans


def plan_payload(plans: Iterable[MaterialPlan], home_html: Path, batch_dir: Path) -> dict[str, Any]:
    plan_list = list(plans)
    return {
        "home_html": str(home_html.resolve()),
        "batch_dir": str(batch_dir.resolve()),
        "count": len(plan_list),
        "known_pages": sum(plan.known_pages or 0 for plan in plan_list),
        "materials": [plan.as_dict() for plan in plan_list],
    }


def format_plan(payload: dict[str, Any]) -> str:
    lines = [
        "DRY-RUN: network access and file writes are disabled",
        f"materials: {payload['count']}",
        f"known pages: {payload['known_pages'] or 'unknown (metadata request required)'}",
        f"batch: {payload['batch_dir']}",
        "",
    ]
    for item in payload["materials"]:
        pages = item["known_pages"] if item["known_pages"] is not None else "?"
        lines.append(f"{item['number']:02d}  {pages:>4} pages  {item['title']}")
        lines.append(f"     -> {item['output_pdf']}")
    return "\n".join(lines)


def _request_headers(
    cookie: str,
    token: str | None = None,
    referer: str | None = None,
    origin: str | None = None,
) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Cookie": cookie,
    }
    if origin:
        headers["Origin"] = origin
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if referer:
        headers["Referer"] = referer
    return headers


def _read_response(opener, request: Request) -> tuple[bytes, Any]:
    with opener.open(request, timeout=60) as response:
        body = response.read()
        encoding = (response.headers.get("Content-Encoding") or "").lower()
        if "gzip" in encoding:
            body = gzip.decompress(body)
        elif "deflate" in encoding:
            body = zlib.decompress(body)
        return body, response.headers


class BookRollClient:
    def __init__(self, cookie: str, base_url: str = DEFAULT_BASE_URL) -> None:
        if not cookie.strip():
            raise ValueError("a non-empty session cookie is required")
        self.cookie = cookie
        self.base_url = base_url.rstrip("/")
        self.api_base_url = f"{self.base_url}/v1"
        parsed_base = urlparse(self.base_url)
        if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
            raise ValueError("base_url must be an absolute http(s) URL")
        self.origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
        self.opener = build_opener(NoRedirect)

    def get_view_token(self, contents: str) -> tuple[str, str]:
        view_url = f"{self.base_url}/book/view?contents={contents}"
        request = Request(view_url, headers=_request_headers(self.cookie, origin=self.origin), method="GET")
        try:
            with self.opener.open(request, timeout=60) as response:
                raise RuntimeError(f"BookRoll view returned HTTP {response.status}, expected redirect")
        except HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308}:
                raise RuntimeError(f"BookRoll view did not redirect: HTTP {error.code}") from error
            location = error.headers.get("Location")
            if not location:
                raise RuntimeError("BookRoll view redirect had no Location header")
        location = urljoin(view_url, location)
        token = urlparse(location).path.rstrip("/").split("/")[-1]
        if len(token) < 100:
            raise RuntimeError("BookRoll view redirect token was unexpectedly short")
        return token, location

    def api_get(self, token: str, url: str, referer: str) -> tuple[Any, str]:
        request = Request(
            url,
            headers=_request_headers(self.cookie, token, referer, origin=self.origin),
            method="GET",
        )
        try:
            body, headers = _read_response(self.opener, request)
        except HTTPError as error:
            detail = error.read(400).decode("utf-8", "replace")
            raise RuntimeError(f"BookRoll API HTTP {error.code} for {url}: {detail[:300]}") from error
        next_token = headers.get("x-token") or token
        try:
            return json.loads(body.decode("utf-8")), next_token
        except json.JSONDecodeError as error:
            raise RuntimeError(f"BookRoll API returned invalid JSON for {url}") from error

    def get_metadata(self, contents: str, token: str, referer: str) -> tuple[dict[str, Any], str]:
        metadata, token = self.api_get(token, f"{self.api_base_url}/contents/{contents}", referer)
        if not isinstance(metadata, dict):
            raise RuntimeError("BookRoll metadata was not a JSON object")
        return metadata, token

    def get_page(self, contents: str, page: int, token: str, referer: str) -> tuple[bytes, str]:
        url = f"{self.api_base_url}/contents/{contents}/image/{page}?{IMAGE_QUERY_KEY}"
        response, token = self.api_get(token, url, referer)
        return decrypt_page(response), token


def decrypt_page(response: dict[str, Any]) -> bytes:
    if not isinstance(response.get("data"), str) or not isinstance(response.get("iv"), str):
        raise RuntimeError("image response did not contain the expected data and iv fields")
    match = PAYLOAD_RE.search(response["data"])
    if not match:
        raise RuntimeError("image response did not contain the expected encrypted payload")
    key = (IMAGE_KEY_PREFIX + response["iv"] + IMAGE_KEY_SUFFIX).encode("utf-8")
    if len(key) != 16:
        raise RuntimeError(f"unexpected AES key length: {len(key)}")
    ciphertext = base64.b64decode(match.group(2), validate=True)
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    payload_b64 = unpadder.update(padded) + unpadder.finalize()
    payload = base64.b64decode(payload_b64, validate=True)
    if not payload:
        raise RuntimeError("decrypted page payload was empty")
    return payload


def _payload_to_pdf(payload: bytes, output_path: Path) -> str:
    if payload.startswith(b"%PDF-"):
        output_path.write_bytes(payload)
        return ".pdf"
    if payload.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8")):
        from PIL import Image

        with Image.open(BytesIO(payload)) as image:
            image.convert("RGB").save(output_path, format="PDF", resolution=72.0)
        return ".image"
    raise RuntimeError(f"unsupported decoded page format: {payload[:16]!r}")


def _merge_pdf(page_paths: list[Path], output_path: Path, title: str) -> int:
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing PDF: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_metadata({"/Title": title, "/Creator": "BookRoll automation"})
    for page_path in page_paths:
        reader = PdfReader(str(page_path), strict=False)
        if len(reader.pages) != 1:
            raise RuntimeError(f"expected one page in {page_path.name}, found {len(reader.pages)}")
        writer.add_page(reader.pages[0])
    with output_path.open("wb") as stream:
        writer.write(stream)
    return len(writer.pages)


def extract_material(
    plan: MaterialPlan,
    cookie: str,
    base_url: str = DEFAULT_BASE_URL,
    delay: float = 0.15,
    retries: int = 3,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    report = plan.as_dict()
    client = BookRollClient(cookie, base_url)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = plan.output_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    token, referer = client.get_view_token(plan.contents)
    metadata, token = client.get_metadata(plan.contents, token, referer)
    contents = metadata.get("contents") or {}
    page_count = int(contents.get("pages") or 0)
    if page_count < 1:
        raise RuntimeError("BookRoll metadata contained no page count")
    title = str(contents.get("title") or plan.title)
    version = contents.get("version")
    is_pdf = bool(contents.get("pdf"))
    if progress:
        progress(f"{plan.number:02d}: metadata {page_count} pages; pdf={is_pdf}")

    page_paths: list[Path] = []
    page_formats: set[str] = set()
    for page_no in range(1, page_count + 1):
        page_path = pages_dir / f"page_{page_no:04d}.pdf"
        if page_path.exists():
            raise FileExistsError(f"refusing to overwrite existing page file: {page_path}")
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                payload, token = client.get_page(plan.contents, page_no, token, referer)
                page_formats.add(_payload_to_pdf(payload, page_path))
                page_paths.append(page_path)
                break
            except (HTTPError, URLError, RuntimeError, ValueError) as error:
                last_error = error
                if attempt + 1 < retries:
                    time.sleep(1.0 + attempt)
        if last_error is not None and len(page_paths) != page_no:
            raise RuntimeError(f"page {page_no} failed after {retries} attempts: {last_error}") from last_error
        if progress and (page_no == 1 or page_no % 10 == 0 or page_no == page_count):
            progress(f"{plan.number:02d}: page {page_no}/{page_count}")
        if delay > 0:
            time.sleep(delay)

    merged_pages = _merge_pdf(page_paths, plan.output_pdf, title)
    manifest = {
        "contents": plan.contents,
        "title": title,
        "version": version,
        "metadata_pages": page_count,
        "merged_pages": merged_pages,
        "page_formats": sorted(page_formats),
        "image_endpoint": f"{client.api_base_url}/contents/{{contents}}/image/{{page}}?{IMAGE_QUERY_KEY}",
        "output_pdf": str(plan.output_pdf),
        "sha256": sha256(plan.output_pdf),
    }
    manifest_path = plan.output_dir / "extraction_manifest.json"
    if manifest_path.exists():
        raise FileExistsError(f"refusing to overwrite existing manifest: {manifest_path}")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report.update({"status": "extracted", "pages": merged_pages, "manifest": manifest})
    return report


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_collection(
    plans: list[MaterialPlan],
    batch_dir: Path,
    cookie: str,
    base_url: str = DEFAULT_BASE_URL,
    delay: float = 0.15,
    retries: int = 3,
    progress: ProgressCallback | None = None,
    combine: bool = False,
) -> dict[str, Any]:
    if batch_dir.exists() and any(batch_dir.iterdir()):
        raise FileExistsError(f"refusing to use a non-empty batch directory: {batch_dir}")
    batch_dir.mkdir(parents=True, exist_ok=True)
    if not plans:
        raise ValueError("no materials selected")
    results: list[dict[str, Any]] = []
    progress_log = batch_dir / "progress.jsonl"
    for plan in plans:
        if progress:
            progress(f"[{plan.number:02d}] {plan.title}")
        try:
            result = extract_material(plan, cookie, base_url, delay, retries, progress)
        except Exception as error:
            result = plan.as_dict()
            result.update({"status": "failed", "error": f"{type(error).__name__}: {error}"})
            if progress:
                progress(f"[{plan.number:02d}] FAILED: {result['error']}")
        results.append(result)
        with progress_log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")

    failures = [item for item in results if item.get("status") == "failed"]
    summary = {
        "count": len(results),
        "completed": len(results) - len(failures),
        "failed": len(failures),
        "total_pages": sum(int(item.get("pages", 0)) for item in results),
    }
    index = {
        "items": results,
        "summary": summary,
        "base_url": base_url,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    index_path = batch_dir / "collection_index.json"
    if index_path.exists():
        raise FileExistsError(f"refusing to overwrite existing index: {index_path}")
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if combine and not failures:
        combined_path = batch_dir / "output" / "pdf" / "bookroll_all.pdf"
        combined_path.parent.mkdir(parents=True, exist_ok=True)
        combine_collection(index_path, combined_path)
        summary["combined_pdf"] = str(combined_path)
    summary_path = batch_dir / "collection_summary.json"
    if summary_path.exists():
        raise FileExistsError(f"refusing to overwrite existing summary: {summary_path}")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"index": index, "summary": summary}


def combine_collection(index_path: Path, output_path: Path) -> dict[str, Any]:
    data = json.loads(index_path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    if not items:
        raise ValueError(f"collection index has no items: {index_path}")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing combined PDF: {output_path}")
    writer = PdfWriter()
    writer.add_metadata({"/Title": "BookRoll materials", "/Creator": "BookRoll automation"})
    sections: list[dict[str, Any]] = []
    total_pages = 0
    for item in items:
        source = Path(item["output_pdf"])
        reader = PdfReader(str(source), strict=False)
        start = total_pages
        for page in reader.pages:
            writer.add_page(page)
            total_pages += 1
        try:
            writer.add_outline_item(str(item["title"]), start)
        except AttributeError:
            writer.addBookmark(str(item["title"]), start)
        sections.append(
            {
                "number": item.get("number"),
                "title": item.get("title"),
                "pages": len(reader.pages),
                "start_page_1based": start + 1,
                "end_page_1based": total_pages,
                "source": str(source),
            }
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as stream:
        writer.write(stream)
    manifest = {
        "output_pdf": str(output_path),
        "sections": sections,
        "total_pages": total_pages,
        "bytes": output_path.stat().st_size,
        "sha256": sha256(output_path),
    }
    manifest_path = output_path.parent.parent.parent / "combined_manifest.json"
    if manifest_path.exists():
        raise FileExistsError(f"refusing to overwrite existing combined manifest: {manifest_path}")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
