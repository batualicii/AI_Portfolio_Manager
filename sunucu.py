#!/usr/bin/env python3
"""STEP Görüntüleyici — yerel sunucu.

Tarayıcı güvenlik kuralları yüzünden index.html'i doğrudan açmak çalışmaz
(WebAssembly ve modüller file:// üzerinden yüklenemez). Bu script küçük bir
yerel sunucu açar, STEP dosyalarını bulur ve tarayıcıyı başlatır.

Kullanım:
    python3 sunucu.py                    # models/ ya da Masaüstü'ndeki Musashi klasörü
    python3 sunucu.py "/yol/klasor"      # belirli bir klasör
    python3 sunucu.py --port 9000 --no-browser
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import socket
import sys
import threading
import unicodedata
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VIEWER_DIR = Path(__file__).resolve().parent
STEP_SUFFIXES = {".step", ".stp"}
# Masaüstünde birden fazla aday klasör varsa adı bunlardan birini içeren öncelikli olur.
FOLDER_HINTS = ("step", "cad", "musashi", "musoshi", "kit", "montaj", "assembly", "parca", "parça", "model")
# Tarama sırasında atlanacak klasörler (büyük / ilgisiz)
SKIP_DIRS = {"library", "applications", "node_modules", "system", "pictures", "music", "movies"}


def natural_key(name: str):
    """Musashi2 < Musashi10 olacak şekilde sıralar."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def step_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in STEP_SUFFIXES]
    return sorted(files, key=lambda p: natural_key(p.name))


def is_hinted(folder: Path) -> bool:
    name = unicodedata.normalize("NFKD", folder.name).casefold()
    return any(hint in name for hint in FOLDER_HINTS)


def scannable(folder: Path) -> bool:
    name = folder.name.casefold()
    return folder.is_dir() and not name.startswith(".") and name not in SKIP_DIRS


MAX_SCAN_DIRS = 500          # tarama süresini sınırla


def scan_for_step_folders(root: Path, depth: int = 3) -> list[Path]:
    """root altında STEP dosyası barındıran klasörleri bulur (adı ipucu taşıyanlar önce)."""
    hinted: list[Path] = []
    plain: list[Path] = []
    frontier = [(root, 0)]
    visited = 0
    while frontier and visited < MAX_SCAN_DIRS:
        folder, level = frontier.pop(0)
        visited += 1
        try:
            entries = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if not scannable(entry):
                continue
            if step_files(entry):
                (hinted if is_hinted(entry) else plain).append(entry)
            elif level + 1 < depth:
                frontier.append((entry, level + 1))
    return hinted + plain


def find_models_dir(explicit: str | None) -> tuple[Path | None, list[Path]]:
    """Sırayla: komut satırı → MUSASHI_DIR/STEP_DIR → models/ → Masaüstündeki STEP klasörü."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    for var in ("STEP_DIR", "MUSASHI_DIR"):
        if os.environ.get(var):
            candidates.append(Path(os.environ[var]).expanduser())
    candidates.append(VIEWER_DIR / "models")

    for folder in candidates:
        found = step_files(folder)
        if found:
            return folder, found

    for root in (Path.home() / "Desktop", Path.home() / "Masaüstü", Path.home()):
        if not root.is_dir():
            continue
        if step_files(root):                      # dosyalar doğrudan masaüstündeyse
            return root, step_files(root)
        # Masaüstünde daha derine bakmaya değer; ev dizininde yüzeysel kal.
        depth = 3 if root != Path.home() else 2
        for folder in scan_for_step_folders(root, depth):
            return folder, step_files(folder)
    return None, []


class Handler(SimpleHTTPRequestHandler):
    models_dir: Path | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIEWER_DIR), **kwargs)

    # --- yardımcılar ---------------------------------------------------------
    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _current_files(self) -> list[Path]:
        return step_files(self.models_dir) if self.models_dir else []

    # --- yönlendirme ---------------------------------------------------------
    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] == "/api/models":
            files = self._current_files()
            return self._send_json({
                "dir": str(self.models_dir) if self.models_dir else "",
                "files": [{"name": f.name, "url": f"/model/{f.name}", "size": f.stat().st_size} for f in files],
            })

        if self.path.startswith("/model/"):
            from urllib.parse import unquote
            wanted = unquote(self.path[len("/model/"):].split("?")[0])
            for f in self._current_files():
                if f.name == wanted:                      # yalnızca listelenen dosyalar
                    return self._serve_file(f)
            self.send_error(HTTPStatus.NOT_FOUND, "Model bulunamadı")
            return None

        return super().do_GET()

    def _serve_file(self, path: Path):
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                while chunk := fh.read(256 * 1024):
                    self.wfile.write(chunk)
        except OSError as exc:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Dosya okunamadı: {exc}")

    def end_headers(self):
        # .wasm'ın doğru MIME tipiyle gitmesi WebAssembly.instantiateStreaming için şart
        if self.path.endswith(".wasm"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # sessiz: yalnızca hatalar
        if str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            sys.stderr.write("  ! %s\n" % (fmt % args))


Handler.extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".wasm": "application/wasm",
                          ".js": "text/javascript", ".mjs": "text/javascript"}


def free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 25):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("Boş port bulunamadı (8765-8790).")


def main() -> int:
    ap = argparse.ArgumentParser(description="STEP Görüntüleyici")
    ap.add_argument("folder", nargs="?", help="STEP dosyalarının bulunduğu klasör")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    models_dir, files = find_models_dir(args.folder)
    Handler.models_dir = models_dir

    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}/"

    print("\n  STEP Görüntüleyici")
    print("  " + "-" * 44)
    if files:
        print(f"  Klasör : {models_dir}")
        print(f"  Dosya  : {len(files)} adet — {', '.join(f.name for f in files[:6])}"
              + (" …" if len(files) > 6 else ""))
    else:
        print("  Klasör : bulunamadı — dosyaları pencereye sürükleyip bırakabilirsiniz.")
        print("           (ya da: python3 sunucu.py \"/Users/adiniz/Desktop/Klasorum\")")
    print(f"  Adres  : {url}")
    print("  Kapatmak için bu pencerede Ctrl+C\n")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if not args.no_browser:
        threading.Timer(0.6, functools.partial(webbrowser.open, url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Kapatıldı.\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
