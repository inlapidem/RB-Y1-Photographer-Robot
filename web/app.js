const page = document.documentElement.dataset.page;
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const copy = {
  booting: ["시스템을 준비하고 있어요", "서버의 첫 상태를 기다리고 있습니다."],
  waiting: ["사진 한 장 찍고 가세요", "가까이 오시면 촬영을 시작할게요."],
  greeting: ["반가워요!", "멋진 인생샷을 찍어드릴게요."],
  deciding: [page === "face" ? "어떤 사진을 찍어볼까요?" : "촬영 구도를 골라주세요", "원하는 구도를 선택하거나 촬영을 거절할 수 있어요."],
  guiding: ["표시된 곳으로 이동해 주세요", "화면의 가이드에 맞춰 천천히 움직여 주세요."],
  capturing: ["촬영 중이에요", "자연스럽게 포즈를 바꿔주세요."],
  previewing: ["사진이 완성됐어요", "촬영한 사진을 빠르게 보여드리고 있어요."],
  asking: ["마음에 드시나요?", "다시 보거나 마음에 드는 결과를 선택해 주세요."],
  farewell: ["사진을 받아가세요", "즐거운 시간 되셨길 바라요. 안녕히 가세요!"],
  error: ["잠시 문제가 생겼어요", "오류를 확인한 뒤 초기화해 주세요."],
};

async function post(path, body) {
  const options = {method: "POST"};
  if (body !== undefined) {
    options.headers = {"Content-Type": "application/json"};
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }
  if (response.status === 204) return null;
  return response.json().catch(() => null);
}

function renderPhotos(photos) {
  const list = $("#photos");
  if (list) {
    list.replaceChildren();
    if (!photos.length) {
      const empty = document.createElement("li");
      empty.textContent = "아직 촬영된 사진이 없습니다.";
      list.append(empty);
    } else {
      photos.forEach((photoUrl, index) => {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = photoUrl;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = `${index + 1}. ${photoUrl}`;
        item.append(link);
        list.append(item);
      });
    }
  }

  const photo = $("#photo");
  if (photo) {
    const latest = photos.at(-1) || "";
    photo.src = latest;
    photo.hidden = !latest;
  }
}

function update(context) {
  const [headline, fallbackMessage] = copy[context.state] || [context.state, ""];
  const photos = Array.isArray(context.photos) ? context.photos : [];
  if ($("#headline")) $("#headline").textContent = headline;
  if ($("#message")) $("#message").textContent = context.error || context.hint || fallbackMessage;
  if ($("#state")) $("#state").textContent = `${context.state} · r${context.revision}`;
  if ($("#debug-json")) $("#debug-json").textContent = JSON.stringify({...context, photos}, null, 2);
  document.body.dataset.state = context.state;

  $$('[data-template]').forEach((button) => { button.disabled = context.state !== "deciding"; });
  if ($("#decline")) $("#decline").disabled = context.state !== "deciding";
  if ($("#replay")) $("#replay").disabled = context.state !== "asking";
  if ($("#liked")) $("#liked").disabled = context.state !== "asking";
  if ($("#retake")) $("#retake").disabled = context.state !== "asking";
  if ($("#accept")) $("#accept").disabled = context.state !== "asking";
  if ($("#reset")) $("#reset").disabled = context.state !== "error";
  // 인식 건너뛰기 — 각 버튼은 그 인식이 실제로 흐름을 막고 있는 동안에만 산다.
  if ($("#skip-approach")) $("#skip-approach").disabled = context.state !== "waiting";
  if ($("#skip-position")) $("#skip-position").disabled = context.state !== "guiding";
  if ($("#skip-ready")) $("#skip-ready").disabled = !context.awaiting_ready;
  if ($("#gesture-left")) $("#gesture-left").disabled = context.state !== "deciding";
  if ($("#gesture-right")) $("#gesture-right").disabled = context.state !== "deciding";
  renderPhotos(photos);
}

function bind(selector, path) {
  const element = $(selector);
  if (!element) return;
  element.addEventListener("click", async () => {
    try {
      await post(path);
    } catch (error) {
      if ($("#message")) $("#message").textContent = error.message;
    }
  });
}

// The legacy guide page shares this script. Remove its obsolete alignment action
// and keep its two review buttons useful under the new workflow contract.
$("#align")?.remove();
if ($("#retake")) $("#retake").textContent = "다시 보기";
if ($("#accept")) $("#accept").textContent = "마음에 들어요";

$$('[data-template]').forEach((button) => button.addEventListener("click", async () => {
  try {
    await post("/api/capture-started", {template_id: button.dataset.template});
  } catch (error) {
    if ($("#message")) $("#message").textContent = error.message;
  }
}));
bind("#decline", "/api/decline");
bind("#replay", "/api/replay");
bind("#liked", "/api/liked");
bind("#retake", "/api/replay");
bind("#accept", "/api/liked");
bind("#reset", "/api/reset");
bind("#skip-approach", "/api/debug/person-approached");
bind("#skip-position", "/api/debug/position-reached");
bind("#skip-ready", "/api/debug/ready-signal");
bind("#gesture-left", "/api/debug/gesture-left");
bind("#gesture-right", "/api/debug/gesture-right");

const events = new EventSource("/events");
events.addEventListener("open", () => {
  if ($("#connection")) {
    $("#connection").textContent = "실시간 연결됨";
    $("#connection").classList.add("online");
  }
});
events.addEventListener("state", (event) => {
  try {
    update(JSON.parse(event.data));
  } catch (error) {
    if ($("#message")) $("#message").textContent = `상태 데이터 오류: ${error.message}`;
  }
});
events.addEventListener("error", () => {
  if ($("#connection")) {
    $("#connection").textContent = "재연결 중";
    $("#connection").classList.remove("online");
  }
});
