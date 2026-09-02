# BookRoll-Automation

A small, local-first tool for turning authorized BookRoll course materials into individual PDFs and an optional bookmarked collection PDF.

This repository is intentionally deployment-neutral and anonymous. It contains no saved course pages, material IDs, cookies, bearer tokens, institution names, or site-specific URLs. Configure your own deployment values at run time.

Use this only for documents you are authorized to view and save. It is not a login bypass, an authorization bypass, or a tool for collecting someone else’s materials.

## What it does

- Reads material links from a saved course-list HTML file.
- Obtains a short-lived viewer token from each authorized material view.
- Calls the material metadata and page-image endpoints.
- Extracts the encrypted Base64 payload from the JSON-like response without executing page JavaScript.
- Decrypts the payload, validates the decoded file signature, and writes one-page PDFs.
- Merges pages into an individual PDF and optionally a bookmarked collection PDF.
- Provides a CLI, a local WebUI, and a network-free dry-run mode.

The current protocol adapter expects the response shape and JavaScript wrapper discovered in one BookRoll deployment. The deployment base URL is deliberately a placeholder. If another deployment uses a different protocol, update the adapter only after inspecting that deployment with permission.

## Requirements

- Windows PowerShell or another shell
- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)

The project uses only `cryptography`, `Pillow`, and `pypdf`. It does not install Torch, CUDA, cuDNN, cuBLAS, or other large ML packages.

## Install

```powershell
Set-Location C:\path\bookroll-pdf-automation
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
uv sync
uv run bookroll --help
```

## Prepare your own inputs

Save the authorized course-list page as UTF-8 HTML. The parser looks for links with a `contents` query parameter, for example:

```html
<a href="/bookroll/book/view?contents=YOUR_MATERIAL_ID">Your material title</a>
```

Do not commit this HTML if it contains private course names, material IDs, or session details. Keep it in a local, ignored working directory.

Set the base URL for your own BookRoll deployment. Include the `/bookroll` application path if that is how your deployment is mounted.

```powershell
$env:BOOKROLL_BASE_URL = 'https://your-bookroll-host.example/bookroll'
```

The repository default is `https://example.invalid/bookroll`, which is intentionally non-functional. This prevents an accidental connection to an unknown deployment.

## Dry-run first

Dry-run performs no network request, does not read the cookie environment variable, creates no output directory, and prints the planned materials and paths.

```powershell
uv run bookroll dry-run `
  --home-html C:\private\bookroll\home.html `
  --batch-dir C:\private\bookroll\output_20260902_01
```

If you already have a previous collection index, show its known page counts too:

```powershell
uv run bookroll dry-run `
  --home-html C:\private\bookroll\home.html `
  --batch-dir C:\private\bookroll\output_20260902_01 `
  --known-index C:\private\bookroll\previous\collection_index.json
```

Select a subset with `--select 1,3-5`. Add `--json` for machine-readable output.

## Extract authorized materials

Inject your current session cookie into the process environment only. Never paste a real cookie into a README, source file, issue, commit, screenshot, or chat.

```powershell
$env:BOOKROLL_SESSION_COOKIE = 'YOUR_CURRENT_COOKIE_STRING'
uv run bookroll extract `
  --home-html C:\private\bookroll\home.html `
  --batch-dir C:\private\bookroll\output_20260902_01 `
  --base-url $env:BOOKROLL_BASE_URL `
  --delay 0.15 `
  --combine
Remove-Item Env:BOOKROLL_SESSION_COOKIE
```

The extractor refuses to use a non-empty batch directory, existing page PDFs, or an existing combined PDF. Use a new timestamped output directory for a new run.

Typical output:

```text
output_20260902_01/
  01_Material_Title/
    pages/page_0001.pdf ...
    output/pdf/01_Material_Title_bookroll.pdf
    extraction_manifest.json
  collection_index.json
  collection_summary.json
  progress.jsonl
  output/pdf/bookroll_all.pdf       # with --combine
  combined_manifest.json            # with --combine
```

## Combine existing PDFs

```powershell
uv run bookroll combine `
  --collection-index C:\private\bookroll\output_20260902_01\collection_index.json `
  --output-pdf C:\private\bookroll\output_20260902_01\output\pdf\bookroll_all.pdf
```

The merge keeps each source page size instead of resizing every page to one format. A collection can therefore contain mixed page sizes.

## Local WebUI (easy path)

```powershell
uv run bookroll webui --host 127.0.0.1 --port 51837
```

Open `http://127.0.0.1:51837/`. For a beginner-friendly flow, click `run_webui.bat`; it creates the small `uv` environment on first launch and then starts the loopback-only server.

In the form, enter your own base URL, the UTF-8 course-list HTML path, and a new output folder. Leave dry-run checked first. The UI shows the selected materials and planned paths without contacting the server. After checking the plan, enter a current cookie (or set `BOOKROLL_SESSION_COOKIE` before launching the server), disable dry-run, and run the authorized extraction. The base URL and cookie are used for that run only; they are not included in job output or logs.

You can also run `run_webui.bat`. The WebUI binds to loopback by default and should not be exposed to a LAN or reverse proxy without adding a proper authentication and secret-handling layer.

## Protocol notes

The adapter follows this sequence:

1. `GET /bookroll/book/view?contents=<id>` and capture the redirect location.
2. `GET /bookroll/v1/contents/<id>` with the short-lived bearer token.
3. `GET /bookroll/v1/contents/<id>/image/<page>?ndw2j`.
4. Parse `data` and `iv` from the JSON response.
5. Extract the argument inside the expected `Hah6lu3wie(...)` wrapper.
6. Build the AES key as `uc5xi + iv + ndw2j`, decrypt AES-128-ECB, remove PKCS#7 padding, and decode the inner Base64.
7. Accept only recognized PDF or image signatures before writing output.

The code intentionally does not use `eval`, execute Vue bundles, or treat an arbitrary response as code. When the response format changes, it fails closed so that an unexpected payload is not silently saved.

## Verification

Run the local tests:

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
uv run python -m unittest discover -s tests -v
```

The test suite covers material-list parsing, AES payload decryption, PDF merging, and a local HTTP protocol fixture. It does not prove that a real account can access a particular deployment; that depends on your current cookie, permissions, and server state.

## Privacy and publication checklist

Before publishing or sharing a run:

- Remove saved course HTML and downloaded PDFs from the Git working tree.
- Check for cookies, bearer tokens, private material IDs, hostnames, institution names, and screenshots.
- Keep the base URL and cookie in environment variables or a local secret store.
- Use a new output directory rather than overwriting an earlier run.
- Confirm that the documents may legally be saved and redistributed before sharing them.

## License

MIT. See [LICENSE](LICENSE).
