#!/usr/bin/env python3
"""Sync local voices/ directory to a ComfyUI server's input folder."""

import argparse
import hashlib
import http.client
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
VOICES_DIR = Path(__file__).parent / "voices"


def _connect(url):
    """Parse URL and return (conn, path, parsed)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https":
        import ssl
        conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443)
    else:
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80)
    path = parsed.path
    if parsed.query:
        path += "?" + parsed.query
    return conn, path, parsed


def _resolve_url(url):
    """Follow redirects with a tiny request to find the final URL."""
    for _ in range(5):
        conn, path, parsed = _connect(url)
        try:
            conn.request("POST", path, body=b"", headers={"Content-Length": "0"})
            resp = conn.getresponse()
            resp.read()  # drain
            if resp.status in (301, 302, 307, 308):
                location = resp.getheader("Location")
                if location.startswith("/"):
                    url = f"{parsed.scheme}://{parsed.netloc}{location}"
                else:
                    url = location
                conn.close()
                continue
            conn.close()
            return url
        except Exception:
            conn.close()
            return url  # best effort
    return url


def _request(url, method="GET", body=None, headers=None):
    """Make an HTTP request using http.client, following redirects."""
    for _ in range(5):
        conn, path, parsed = _connect(url)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            if resp.status in (301, 302, 307, 308):
                location = resp.getheader("Location")
                if location.startswith("/"):
                    url = f"{parsed.scheme}://{parsed.netloc}{location}"
                else:
                    url = location
                resp.read()
                conn.close()
                continue
            return resp
        except Exception:
            conn.close()
            raise
    raise RuntimeError(f"Too many redirects for {url}")


def file_exists_on_server(base_url, filename):
    """Check if a file exists on the server by trying to fetch it."""
    params = urllib.parse.urlencode({
        "filename": filename, "subfolder": "", "type": "input",
    })
    url = f"{base_url}/api/view?{params}"
    try:
        resp = _request(url, method="HEAD")
        exists = resp.status == 200
        resp.close()
        return exists
    except Exception:
        return False


def upload_file(base_url, filepath):
    """Upload an audio file to ComfyUI's input folder."""
    filename = filepath.name
    file_data = filepath.read_bytes()

    boundary = "----PythonFormBoundary" + hashlib.md5(filename.encode()).hexdigest()
    body = bytearray()

    # File part
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n".encode()
    )
    body.extend(file_data)
    body.extend(b"\r\n")

    # subfolder part (empty = input root)
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        b'Content-Disposition: form-data; name="subfolder"\r\n\r\n'
        b"\r\n"
    )

    # overwrite part
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        b'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
        b"true\r\n"
    )

    body.extend(f"--{boundary}--\r\n".encode())

    url = _resolve_url(f"{base_url}/api/upload/image")
    resp = _request(
        url,
        method="POST",
        body=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    resp_body = resp.read().decode(errors="replace")
    if resp.status != 200:
        raise RuntimeError(f"Upload failed ({resp.status}): {resp_body}")
    return json.loads(resp_body)


def main():
    parser = argparse.ArgumentParser(description="Sync voices to ComfyUI server")
    parser.add_argument("--url", default=COMFYUI_URL, help=f"ComfyUI URL (default: {COMFYUI_URL})")
    parser.add_argument("--force", action="store_true", help="Re-upload even if file exists on server")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be uploaded")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")

    wav_files = sorted(VOICES_DIR.glob("*.wav"))
    if not wav_files:
        print(f"No .wav files found in {VOICES_DIR}")
        sys.exit(0)

    print(f"Found {len(wav_files)} voice(s) in {VOICES_DIR}")

    uploaded = 0
    skipped = 0
    for wav in wav_files:
        size_mb = wav.stat().st_size / (1024 * 1024)
        if not args.force and file_exists_on_server(base_url, wav.name):
            print(f"  skip  {wav.name} ({size_mb:.1f} MB) — already on server")
            skipped += 1
            continue

        if args.dry_run:
            print(f"  would upload  {wav.name} ({size_mb:.1f} MB)")
            uploaded += 1
            continue

        print(f"  upload  {wav.name} ({size_mb:.1f} MB) ... ", end="", flush=True)
        result = upload_file(base_url, wav)
        print(f"ok → {result.get('name', wav.name)}")
        uploaded += 1

    action = "would upload" if args.dry_run else "uploaded"
    print(f"\nDone: {action} {uploaded}, skipped {skipped}")


if __name__ == "__main__":
    main()
