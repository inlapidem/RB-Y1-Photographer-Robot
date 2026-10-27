"""Show the frames produced by the running phone capture server."""

from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import pygame


ROOT = Path(__file__).resolve().parents[1]


def request_json(url: str, *, method: str = "GET") -> dict[str, object]:
    request = urllib.request.Request(url, method=method)
    context = ssl._create_unverified_context()
    with urllib.request.urlopen(request, timeout=6.0, context=context) as response:
        return json.loads(response.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="https://127.0.0.1:8443")
    parser.add_argument("--fps", type=float, default=3.0)
    args = parser.parse_args()

    pygame.init()
    screen = pygame.display.set_mode((675, 900), pygame.RESIZABLE)
    pygame.display.set_caption("iPhone capture output (3:4) - ESC to close")
    font = pygame.font.Font(None, 32)
    delay = 1.0 / max(args.fps, 0.2)
    frame: np.ndarray | None = None
    message = "Waiting for iPhone /phone connection..."

    try:
        running = True
        while running:
            started = time.monotonic()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
            if not running:
                break
            try:
                status = request_json(f"{args.server}/api/debug/phone-camera")
                if not status.get("connected"):
                    frame = None
                    message = "Waiting for iPhone /phone connection..."
                else:
                    result = request_json(
                        f"{args.server}/api/debug/phone-snapshot", method="POST"
                    )
                    photo_url = str(result["photo_url"])
                    path = ROOT / photo_url.lstrip("/")
                    try:
                        frame = cv2.imread(str(path))
                        if frame is None:
                            raise RuntimeError(f"cannot decode {path}")
                        message = (
                            f"input {result['metadata'].get('video_width')}x"
                            f"{result['metadata'].get('video_height')} -> "
                            f"saved {result['width']}x{result['height']}"
                        )
                    finally:
                        path.unlink(missing_ok=True)
            except (OSError, KeyError, RuntimeError, urllib.error.URLError) as exc:
                frame = None
                message = f"Waiting: {str(exc)[:60]}"

            screen.fill((0, 0, 0))
            if frame is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                surface = pygame.surfarray.make_surface(rgb.swapaxes(0, 1))
                width, height = screen.get_size()
                scale = min(width / surface.get_width(), height / surface.get_height())
                size = (int(surface.get_width() * scale), int(surface.get_height() * scale))
                surface = pygame.transform.smoothscale(surface, size)
                screen.blit(surface, ((width - size[0]) // 2, (height - size[1]) // 2))
            label = font.render(message, True, (70, 255, 70))
            screen.blit(label, (18, 18))
            pygame.display.flip()
            remaining = delay - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
    finally:
        pygame.quit()


if __name__ == "__main__":
    main()
