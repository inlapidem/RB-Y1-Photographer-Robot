"""Guest-facing photo gallery, served on its own origin.

The kiosk runs on a self-signed certificate, which is fine for the one phone
we set up ourselves but not for a guest who scanned a QR code — Safari's
"this connection is not private" screen is where they leave. So the gallery
gets a separate port that a Tailscale Funnel publishes under a real Let's
Encrypt certificate, and the QR points there.

That trusted origin buys more than a missing warning: `navigator.share` only
exists in a secure context, and it is the one path that drops a whole shoot
into the camera roll as individual images. A plain-HTTP gallery would work,
but every guest would be back to long-pressing photos one at a time.

Each shoot gets an unguessable token; without one the directory that holds
every guest's photos would be a single URL away.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

TOKEN_BYTES = 16


@dataclass
class Gallery:
    """Token → the photos of one shoot."""

    base_url: str = ""
    max_sessions: int = 200
    _sessions: dict[str, list[Path]] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def publish(self, photos: list[Path]) -> str:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self._sessions[token] = list(photos)
        self._order.append(token)
        # Old links stop working rather than growing without bound; a guest
        # who walked away an hour ago is not coming back to this URL.
        while len(self._order) > self.max_sessions:
            self._sessions.pop(self._order.pop(0), None)
        return token

    def photos(self, token: str) -> list[Path]:
        if token not in self._sessions:
            raise KeyError(token)
        return self._sessions[token]

    def url_for(self, token: str) -> str:
        return f"{self.base_url.rstrip('/')}/g/{token}"


def qr_svg(data: str, scale: int = 6) -> bytes:
    import segno

    buffer = __import__("io").BytesIO()
    segno.make(data, error="m").save(buffer, kind="svg", scale=scale, border=2, dark="#101522")
    return buffer.getvalue()


def create_gallery_app(gallery: Gallery) -> FastAPI:
    app = FastAPI(title="GeekSeek gallery", docs_url=None, redoc_url=None)

    def session(token: str) -> list[Path]:
        try:
            return gallery.photos(token)
        except KeyError:
            raise HTTPException(status_code=404, detail="만료되었거나 없는 링크입니다") from None

    @app.get("/g/{token}", response_class=HTMLResponse, include_in_schema=False)
    async def page(token: str) -> HTMLResponse:
        photos = session(token)
        return HTMLResponse(_render(token, len(photos)))

    @app.get("/g/{token}/{index}.jpg", include_in_schema=False)
    async def photo(token: str, index: int) -> Response:
        photos = session(token)
        if not 0 <= index < len(photos):
            raise HTTPException(status_code=404, detail="없는 사진입니다")
        try:
            data = photos[index].read_bytes()
        except OSError:
            raise HTTPException(status_code=404, detail="사진 파일이 사라졌습니다") from None
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/qr/{token}.svg", include_in_schema=False)
    async def qr(token: str) -> Response:
        session(token)
        return Response(qr_svg(gallery.url_for(token)), media_type="image/svg+xml")

    return app


def _render(token: str, count: int) -> str:
    return f"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>사진 받기 · GEEKSEEK</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0b0e17; color: #eef1f8;
         font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", sans-serif; }}
  header {{ padding: 22px 20px 12px; }}
  .brand {{ font-size: .72rem; font-weight: 800; letter-spacing: .22em; color: #8f9ad0; }}
  h1 {{ margin: 8px 0 4px; font-size: 1.55rem; }}
  p.sub {{ margin: 0; color: #9aa3bd; font-size: .9rem; }}
  .bar {{ position: sticky; top: 0; z-index: 5; padding: 12px 20px 14px;
          background: linear-gradient(#0b0e17 72%, rgba(11,14,23,0)); }}
  button {{ width: 100%; min-height: 56px; border: 0; border-radius: 16px; color: #fff;
            font-size: 1.02rem; font-weight: 800; cursor: pointer;
            background: linear-gradient(135deg, #6678ff, #9e52dc); }}
  button:disabled {{ opacity: .55; }}
  .note {{ margin: 10px 2px 0; color: #99a2bb; font-size: .82rem; line-height: 1.5; }}
  .grid {{ display: grid; gap: 12px; padding: 4px 20px 40px; }}
  figure {{ margin: 0; position: relative; }}
  img {{ width: 100%; display: block; border-radius: 14px; background: #161c2b; }}
  figcaption {{ position: absolute; left: 10px; bottom: 10px; padding: 4px 9px; border-radius: 999px;
                background: rgba(8,11,19,.66); font-size: .7rem; font-weight: 700; }}
</style></head>
<body>
<header>
  <div class="brand">GEEKSEEK</div>
  <h1>사진 {count}장이 준비됐어요</h1>
  <p class="sub">전부 저장한 뒤 마음에 드는 걸 고르세요.</p>
</header>
<div class="bar">
  <button id="save" type="button">사진 {count}장 모두 저장</button>
  <p class="note" id="note">아래 사진을 길게 눌러 한 장씩 저장할 수도 있어요.</p>
</div>
<div class="grid" id="grid"></div>
<script>
  const TOKEN = {token!r};
  const COUNT = {count};
  const grid = document.getElementById("grid");
  const save = document.getElementById("save");
  const note = document.getElementById("note");

  for (let i = 0; i < COUNT; i += 1) {{
    const figure = document.createElement("figure");
    const img = document.createElement("img");
    img.src = `/g/${{TOKEN}}/${{i}}.jpg`;
    img.alt = `사진 ${{i + 1}}`;
    img.loading = i < 4 ? "eager" : "lazy";
    const caption = document.createElement("figcaption");
    caption.textContent = String(i + 1).padStart(2, "0");
    figure.append(img, caption);
    grid.append(figure);
  }}

  const LONG_PRESS = "사진을 길게 눌러 저장해주세요.";
  const filename = (index) => `geekseek_${{String(index + 1).padStart(2, "0")}}.jpg`;
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

  // 두 플랫폼이 "사진첩에 넣기"에 쓰는 통로가 다르다.
  //
  // iOS 공유 시트에는 "이미지 N개 저장"이 있어서 navigator.share가 곧장
  // 사진 앱으로 들어간다. 안드로이드 공유 시트에는 그런 항목이 없어서 같은
  // 코드가 "다른 앱으로 보내기" 목록만 띄운다 — 저장이 아니다. 안드로이드에서
  // 갤러리에 넣는 방법은 그냥 내려받는 것이고, 받은 이미지는 Download 폴더로
  // 가서 갤러리 앱의 'Download' 앨범에 나타난다.
  //
  // 그래서 기능 감지가 아니라 플랫폼으로 갈라야 한다. 둘 다 두 API를 지원하지만
  // 결과가 다르다.
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  async function saveByDownload() {{
    for (let index = 0; index < COUNT; index += 1) {{
      const link = document.createElement("a");
      link.href = `/g/${{TOKEN}}/${{index}}.jpg`;
      link.download = filename(index);
      document.body.append(link);
      link.click();
      link.remove();
      save.textContent = `저장 중… ${{index + 1}}/${{COUNT}}`;
      // 브라우저가 연속 내려받기를 한꺼번에 받으면 뒤쪽을 버린다.
      await sleep(220);
    }}
    save.textContent = `사진 ${{COUNT}}장 저장 완료`;
    note.textContent = "갤러리 앱의 'Download' 앨범에서 볼 수 있어요.";
  }}

  // navigator.share는 탭이 아직 "활성"일 때 불러야 한다. 클릭한 뒤에 수십 장을
  // 받아오면 그 사이 활성 상태가 만료돼 iOS가 거부한다. 그래서 버튼이 눌리기
  // 전에 파일을 준비해 둔다 — 터널이 동시 요청 200개를 넘기면 거절하므로
  // 몇 개씩 나눠서.
  const files = [];
  async function preload() {{
    for (let i = 0; i < COUNT; i += 6) {{
      const batch = await Promise.all(
        Array.from({{length: Math.min(6, COUNT - i)}}, async (_, k) => {{
          const index = i + k;
          const response = await fetch(`/g/${{TOKEN}}/${{index}}.jpg`);
          const blob = await response.blob();
          return new File([blob], filename(index), {{type: "image/jpeg"}});
        }})
      );
      files.push(...batch);
      save.textContent = `사진 ${{COUNT}}장 모두 저장 (${{files.length}}/${{COUNT}} 준비 중)`;
    }}
  }}

  async function saveByShare() {{
    try {{
      await navigator.share({{files}});
    }} catch (error) {{
      // 사용자가 공유 시트를 닫은 것까지 실패로 보이면 안 된다.
      if (error && error.name === "AbortError") return;
      note.textContent = "한 번에 저장이 안 되네요 — " + LONG_PRESS;
    }}
  }}

  function useLongPressOnly() {{
    save.hidden = true;
    note.textContent = LONG_PRESS;
  }}

  if (!isIOS) {{
    if ("download" in document.createElement("a")) {{
      note.textContent = "저장하면 갤러리의 'Download' 앨범에 들어가요.";
      save.addEventListener("click", saveByDownload);
    }} else {{
      useLongPressOnly();
    }}
  }} else {{
    save.disabled = true;
    preload().then(() => {{
      const shareable = navigator.canShare && navigator.canShare({{files: [files[0]]}});
      if (!shareable) return useLongPressOnly();
      save.disabled = false;
      save.textContent = `사진 ${{COUNT}}장 모두 저장`;
      save.addEventListener("click", saveByShare);
    }}).catch(useLongPressOnly);
  }}
</script>
</body></html>
"""
