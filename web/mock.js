(() => {
  "use strict";

  const page = document.documentElement.dataset.page;
  const debugMode = new URLSearchParams(window.location.search).get("debug") === "1";
  const stateToStage = {
    waiting: 1,
    greeting: 2,
    deciding: 3,
    guiding: 4,
    capturing: 5,
    previewing: 6,
    asking: 7,
    farewell: 8,
  };
  const captions = [
    "안녕하세요! 사진 한 장 찍고 가세요.",
    "제가 인생샷 찍어드릴게요!",
    "제가 멋있는 구도로 찍어드릴게요.",
    "화면에 표시된 곳으로 움직여주세요.",
    "여러 개 찍겠습니다. 자연스럽게 포즈를 취해주세요!",
    "짜잔, 이렇게 나왔어요!",
    "QR로 사진을 받아가세요.",
    "즐거운 시간 되셨길 바라요. 안녕히 가세요!",
  ];
  const stageTitles = [
    "사람을 기다리는 중",
    "가까이 온 사람을 발견",
    "촬영 여부와 구도 선택",
    "촬영 위치 안내",
    "여러 구도로 연속 촬영",
    "촬영 결과 빠른 미리보기",
    "사진 전달 후 자동 종료 대기",
    "사진 전달 후 인사",
  ];
  const faceMoods = [
    ["mood-gentle", "상냥한 기본 얼굴", "반가워요"],
    ["mood-sparkly", "초롱초롱 웃는 얼굴", "제가 인생샷 찍어드릴게요"],
    ["mood-sparkly", "웃으며 기다리는 얼굴", "어떤 사진이 좋을까요?"],
    ["mood-excited", "약간 신나하는 얼굴", "좋아요, 시작해볼까요?"],
    ["mood-wink", "촬영할 때마다 윙크하는 얼굴", "촬영 준비 중"],
    ["mood-curious", "결과가 궁금한 얼굴", "어떻게 나왔을까요?"],
    ["mood-waiting", "사진 전달을 기다리는 얼굴", "사진을 받아가세요"],
    ["mood-happy", "밝게 웃으며 인사하는 얼굴", "다음에 또 만나요"],
  ];
  const DEFAULT_TEMPLATE = "full_body";
  const poseExampleCount = 4;
  const speechMuteKey = "geekseek.face-speech-muted";
  const stageVoiceFiles = [
    "/static/audio/voice-01.mp3",
    "/static/audio/voice-02.mp3",
    "/static/audio/voice-03.mp3",
    "/static/audio/voice-04.mp3",
    "/static/audio/voice-05.mp3",
    "/static/audio/voice-07.mp3",
    "/static/audio/voice-08.mp3",
    "/static/audio/voice-09.mp3",
  ];
  const countdownVoiceFile = "/static/audio/voice-06-countdown.mp3";

  let stage = 1;
  let workflowState = "booting";
  let context = {state: "booting", template_id: null, photos: [], hint: "", error: "", greeting_line: null, countdown: null, awaiting_ready: false, photo_target: 0, gallery_url: "", framing_message: "사람을 기다리는 중", framing_direction: "detect", framing_scale: 0, framing_inside: 0, framing_required: 0, framing_positioned: false, gesture_choice: "", gesture_progress: 0, revision: 0};
  let stageTimer = null;
  let effectTimer = null;
  let effectTimeout = null;
  let carouselIndex = 0;
  let slideIndex = 0;
  let actionPending = false;
  let speechMuted = false;
  let speechUnlocked = false;
  let voiceRequest = 0;
  let countdownVoicePlayed = false;
  const hasFace = page === "face" || page === "standby";
  const hasGuide = page === "guide" || page === "standby";
  const voicePlayer = hasFace ? new Audio() : null;
  const shutterPlayer = new Audio();
  // A near-zero-length silent clip so the first-gesture unlock (below) has
  // something to play immediately, even before the real shutter sound has
  // finished fetching/decoding/boosting.
  shutterPlayer.src =
    "data:audio/wav;base64,UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQIAAAAAAA==";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];

  document.body.classList.toggle("debug-mode", debugMode);

  function speechAvailable() {
    return hasFace && voicePlayer !== null;
  }

  // Recorded camera-shutter click. Played through a plain <audio> element —
  // the same mechanism the TTS voice lines use — because iOS Safari tracks
  // autoplay-unlock separately for AudioContext vs. HTMLMediaElement; an
  // AudioContext-based click was silently inaudible even after the page's
  // voice playback had already unlocked. The volume boost is baked into the
  // file up front (via a one-off OfflineAudioContext render, which isn't
  // gesture-gated) so playback itself stays on the proven <audio>.play() path.
  const shutterSoundFile = "/static/audio/shutter.mp3";
  const shutterGainLevel = 1.8;
  const shutterMaxDuration = 0.4; // seconds — trims any trailing tail/reverb
  const shutterOnsetThreshold = 0.04; // peak amplitude (0-1) that counts as "sound started"
  const shutterOnsetLookback = 0.01; // seconds kept before the detected onset, so the attack isn't clipped

  // The source recording has a bit of dead air before the actual click —
  // scan for where the waveform actually starts instead of assuming t=0.
  function detectOnsetSeconds(buffer) {
    const channels = [];
    for (let ch = 0; ch < buffer.numberOfChannels; ch += 1) channels.push(buffer.getChannelData(ch));
    for (let i = 0; i < buffer.length; i += 1) {
      let peak = 0;
      for (let ch = 0; ch < channels.length; ch += 1) {
        const value = Math.abs(channels[ch][i]);
        if (value > peak) peak = value;
      }
      if (peak >= shutterOnsetThreshold) {
        const onsetIndex = Math.max(0, i - Math.round(buffer.sampleRate * shutterOnsetLookback));
        return onsetIndex / buffer.sampleRate;
      }
    }
    return 0;
  }

  function audioBufferToWavBlob(buffer) {
    const numChannels = buffer.numberOfChannels;
    const numFrames = buffer.length;
    const blockAlign = numChannels * 2;
    const dataSize = numFrames * blockAlign;
    const view = new DataView(new ArrayBuffer(44 + dataSize));
    const writeString = (offset, text) => {
      for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
    };
    writeString(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, numChannels, true);
    view.setUint32(24, buffer.sampleRate, true);
    view.setUint32(28, buffer.sampleRate * blockAlign, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, dataSize, true);

    const channels = [];
    for (let ch = 0; ch < numChannels; ch += 1) channels.push(buffer.getChannelData(ch));
    let offset = 44;
    for (let i = 0; i < numFrames; i += 1) {
      for (let ch = 0; ch < numChannels; ch += 1) {
        const sample = Math.max(-1, Math.min(1, channels[ch][i]));
        view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
        offset += 2;
      }
    }
    return new Blob([view.buffer], {type: "audio/wav"});
  }

  function prepareShutterSound() {
    fetch(shutterSoundFile)
      .then((response) => response.arrayBuffer())
      .then((data) => {
        const Ctor = window.AudioContext || window.webkitAudioContext;
        const scratchCtx = new Ctor();
        return scratchCtx.decodeAudioData(data).then((decoded) => {
          const onsetSeconds = detectOnsetSeconds(decoded);
          const availableSeconds = decoded.duration - onsetSeconds;
          const outputSeconds = Math.min(availableSeconds, shutterMaxDuration);
          const outputFrames = Math.max(1, Math.round(outputSeconds * decoded.sampleRate));
          const offlineCtx = new OfflineAudioContext(decoded.numberOfChannels, outputFrames, decoded.sampleRate);
          const source = offlineCtx.createBufferSource();
          source.buffer = decoded;
          const gain = offlineCtx.createGain();
          const fadeOutStart = Math.max(0, outputSeconds - 0.015);
          gain.gain.setValueAtTime(shutterGainLevel, 0);
          if (fadeOutStart > 0 && outputSeconds < availableSeconds) {
            // Only the trimmed (shorter-than-source) case needs a fade —
            // otherwise the clip already ends on its own natural decay.
            gain.gain.setValueAtTime(shutterGainLevel, fadeOutStart);
            gain.gain.linearRampToValueAtTime(0, outputSeconds);
          }
          source.connect(gain);
          gain.connect(offlineCtx.destination);
          source.start(0, onsetSeconds);
          return offlineCtx.startRendering();
        }).finally(() => scratchCtx.close?.());
      })
      .then((boosted) => {
        shutterPlayer.src = URL.createObjectURL(audioBufferToWavBlob(boosted));
        shutterPlayer.preload = "auto";
        shutterPlayer.setAttribute("playsinline", "");
        shutterPlayer.load();
      })
      .catch((error) => {
        console.warn(`[Geekseek shutter] boosted render failed, using raw file: ${error.message}`);
        shutterPlayer.src = shutterSoundFile;
        shutterPlayer.preload = "auto";
        shutterPlayer.load();
      });
  }

  function playShutterSound() {
    if (!shutterPlayer.src) return;
    shutterPlayer.currentTime = 0;
    shutterPlayer.play().catch((error) => {
      console.warn(`[Geekseek shutter] playback failed: ${error.message}`);
    });
  }

  function updateSpeechButton() {
    const button = $("#speech-toggle");
    if (!button) return;
    const supported = speechAvailable();
    button.disabled = !supported;
    button.setAttribute("aria-pressed", String(speechMuted));
    button.setAttribute("aria-label", speechMuted ? "음성 켜기" : "음소거");
    button.title = supported ? (speechMuted ? "음성 켜기" : "음소거") : "이 브라우저는 음성을 지원하지 않습니다";
    $("#speech-icon").textContent = speechMuted ? "🔇" : "🔊";
    $("#speech-label").textContent = supported ? (speechMuted ? "음성 꺼짐" : "음성 켜짐") : "음성 미지원";
  }

  function cancelSpeech() {
    voiceRequest += 1;
    if (!voicePlayer) return;
    voicePlayer.pause();
    voicePlayer.currentTime = 0;
  }

  function playVoice(source) {
    if (!speechAvailable() || !source) return;
    cancelSpeech();
    if (speechMuted) return;
    const request = voiceRequest;
    voicePlayer.src = source;
    voicePlayer.volume = 1;
    voicePlayer.muted = false;
    voicePlayer.load();
    voicePlayer.play().then(() => {
      if (request === voiceRequest) speechUnlocked = true;
    }).catch((error) => {
      if (request !== voiceRequest || error.name === "AbortError") return;
      speechUnlocked = false;
      console.warn(`[Geekseek voice] playback failed: ${error.message}`);
      if (!speechMuted) showUnlockBanner();
    });
  }

  function playStageVoice(nextStage = stage) {
    playVoice(stageVoiceFiles[nextStage - 1]);
  }

  function preloadVoices() {
    if (!speechAvailable()) return;
    [...stageVoiceFiles, countdownVoiceFile].forEach((source) => {
      const audio = document.createElement("audio");
      audio.preload = "auto";
      audio.src = source;
      audio.load();
    });
    voicePlayer.preload = "auto";
    voicePlayer.setAttribute("playsinline", "");
    voicePlayer.addEventListener("error", () => {
      const mediaError = voicePlayer.error;
      if (mediaError) {
        console.warn(`[Geekseek voice] media error ${mediaError.code}: ${voicePlayer.currentSrc}`);
      }
    });
  }

  function ensureUnlockBanner() {
    let banner = $("#speech-unlock-banner");
    if (banner) return banner;
    banner = document.createElement("button");
    banner.id = "speech-unlock-banner";
    banner.type = "button";
    banner.className = "speech-unlock-banner";
    banner.innerHTML = '<span>🔈</span> 화면을 한 번 눌러 음성을 켜주세요 (설정 시 1회)';
    banner.addEventListener("click", warmUpSpeech);
    document.body.append(banner);
    return banner;
  }

  function showUnlockBanner() {
    if (!hasFace || !speechAvailable() || speechMuted) return;
    ensureUnlockBanner().hidden = false;
  }

  function hideUnlockBanner() {
    $("#speech-unlock-banner")?.remove();
  }

  function warmUpSpeech() {
    if (!speechAvailable() || speechUnlocked) return;
    speechUnlocked = true;
    hideUnlockBanner();
    console.info("[Geekseek voice] audio unlocked by user gesture");
    if (!speechMuted) playStageVoice();
  }

  function toggleSpeech() {
    if (!speechAvailable()) return;
    speechMuted = !speechMuted;
    try {
      window.localStorage.setItem(speechMuteKey, String(speechMuted));
    } catch (error) {
      console.warn(`[Geekseek TTS] mute preference was not saved: ${error.message}`);
    }
    updateSpeechButton();
    if (speechMuted) {
      cancelSpeech();
      hideUnlockBanner();
    } else {
      if (!speechUnlocked) showUnlockBanner();
      else if (stateToStage[workflowState]) playStageVoice();
    }
  }

  function setupSpeech() {
    if (!hasFace) return;
    try {
      speechMuted = window.localStorage.getItem(speechMuteKey) === "true";
    } catch (error) {
      console.warn(`[Geekseek TTS] mute preference was not loaded: ${error.message}`);
    }
    updateSpeechButton();
    if (!speechAvailable()) return;
    preloadVoices();
    document.addEventListener("pointerdown", warmUpSpeech, {capture: true, once: true});
    document.addEventListener("touchend", warmUpSpeech, {capture: true, once: true});
    document.addEventListener("click", warmUpSpeech, {capture: true, once: true});
    $("#speech-toggle")?.addEventListener("click", toggleSpeech);
    if (!speechMuted) showUnlockBanner();
  }

  function setupShutterAudio() {
    prepareShutterSound();
    // iOS Safari only allows script-triggered <audio>.play() once THIS
    // element has successfully played from within a real user gesture —
    // do a muted play/pause/reset on the first tap purely to earn that.
    const unlockShutterElement = () => {
      const wasMuted = shutterPlayer.muted;
      shutterPlayer.muted = true;
      shutterPlayer.play().then(() => {
        shutterPlayer.pause();
        shutterPlayer.currentTime = 0;
        shutterPlayer.muted = wasMuted;
      }).catch(() => {
        shutterPlayer.muted = wasMuted;
      });
    };
    document.addEventListener("pointerdown", unlockShutterElement, {capture: true, once: true});
    document.addEventListener("touchend", unlockShutterElement, {capture: true, once: true});
    document.addEventListener("click", unlockShutterElement, {capture: true, once: true});
  }

  function clearEffects() {
    window.clearInterval(stageTimer);
    window.clearInterval(effectTimer);
    window.clearTimeout(effectTimeout);
    stageTimer = null;
    effectTimer = null;
    effectTimeout = null;
    document.body.classList.remove("wave-mode");
  }

  function setConnectionChip(mode) {
    const chip = $(".stage-chip");
    const label = $("#stage-label");
    if (!chip || !label) return;
    chip.classList.remove("online", "reconnecting", "workflow-failed");
    if (mode === "booting") label.textContent = "연결 중";
    if (mode === "reconnecting") {
      chip.classList.add("reconnecting");
      label.textContent = "재연결 중";
    }
    if (mode === "error") {
      chip.classList.add("workflow-failed");
      label.textContent = "오류 발생";
    }
    if (mode === "online") {
      chip.classList.add("online");
      label.textContent = `SCENE ${String(stage).padStart(2, "0")}`;
    }
  }

  const readyCaption = "준비되시면 손을 들어주세요!";

  function currentCaption() {
    if (workflowState === "capturing" && context.awaiting_ready) return readyCaption;
    const personalizable = workflowState === "greeting" || workflowState === "deciding";
    return (personalizable && context.greeting_line) || captions[stage - 1];
  }

  function enterStage(nextStage) {
    clearEffects();
    stage = nextStage;
    $("#caption").textContent = currentCaption();
    if (hasFace) renderFace();
    if (hasGuide) renderGuide();
  }

  function renderFace() {
    const [mood, label, note] = faceMoods[stage - 1];
    const face = $("#face");
    face.className = `robot-face ${mood}`;
    face.setAttribute("aria-label", label);
    $("#face-note").textContent = note;
    $("#stage-title").textContent = stageTitles[stage - 1];

    if (stage === 1) {
      const wave = () => {
        document.body.classList.add("wave-mode");
        effectTimeout = window.setTimeout(() => document.body.classList.remove("wave-mode"), 2600);
      };
      effectTimeout = window.setTimeout(wave, 900);
      stageTimer = window.setInterval(wave, 10000);
    }

    if (stage === 8) {
      const wave = () => {
        document.body.classList.add("wave-mode");
        effectTimeout = window.setTimeout(() => document.body.classList.remove("wave-mode"), 2500);
      };
      wave();
      stageTimer = window.setInterval(wave, 5500);
    }
  }

  function renderGuide() {
    $$(".guide-scene").forEach((scene) => {
      scene.hidden = Number(scene.dataset.scene) !== stage;
    });
    $$(".stage-progress i").forEach((dot, index) => dot.classList.toggle("active", index === stage - 1));

    if (stage === 1) {
      const wave = () => {
        $(".scene-billboard")?.classList.add("wave-mode");
        effectTimeout = window.setTimeout(() => $(".scene-billboard")?.classList.remove("wave-mode"), 2500);
      };
      effectTimeout = window.setTimeout(wave, 900);
      stageTimer = window.setInterval(wave, 10000);
    }

    if (stage === 3) updateCarousel();
    if (stage === 6) {
      syncSlideshowPhotos();
      startSlideshow();
    }
    if (stage === 7 || stage === 8) drawQrCodes();
  }

  function updateCarousel() {
    const strip = $("#example-strip");
    if (!strip) return;
    strip.style.transform = `translateX(-${carouselIndex * 100}%)`;
    $("#carousel-count").textContent = `${carouselIndex + 1} / ${poseExampleCount}`;
    $$(".carousel-dots i").forEach((dot, index) => dot.classList.toggle("active", index === carouselIndex));
  }

  function updateCaptureData(previous) {
    const count = context.photos.length;
    const burstCount = $("#burst-count");
    const progress = $("#burst-progress");
    const photoName = $("#pose-name");
    const total = context.photo_target > 0 ? context.photo_target : 3;
    if (burstCount) burstCount.textContent = `촬영 중 · ${count} / ${total}`;
    if (progress) progress.style.width = `${Math.min(count / total, 1) * 100}%`;
    if (photoName) photoName.textContent = `PHOTO ${count} / ${total}`;

    if (workflowState === "capturing" && count > previous.photos.length) {
      playShutterSound();
    }

    if (hasGuide && workflowState === "capturing" && count > previous.photos.length) {
      flashCapturedPhoto(context.photos[count - 1]);
    }

    if (hasFace && workflowState === "capturing") {
      $("#face-note").textContent = count
        ? `찰칵 · ${String(count).padStart(2, "0")} / ${String(total).padStart(2, "0")}`
        : "촬영 준비 중";
      if (count > previous.photos.length) {
        const face = $("#face");
        window.clearTimeout(effectTimeout);
        face.classList.remove("wink-now");
        window.requestAnimationFrame(() => face.classList.add("wink-now"));
        effectTimeout = window.setTimeout(() => face.classList.remove("wink-now"), 300);
      }
    }
  }

  function flashCapturedPhoto(photoUrl) {
    const flash = $("#burst-flash");
    if (!flash || !photoUrl) return;
    flash.src = photoUrl;
    flash.classList.remove("visible");
    void flash.offsetWidth; // force reflow so re-adding the class replays the transition
    flash.classList.add("visible");
    window.setTimeout(() => flash.classList.remove("visible"), 700);
  }

  function updateCountdown() {
    const overlay = $("#countdown-overlay");
    const numberEl = $("#countdown-number");
    if (!overlay || !numberEl) return;
    if (context.countdown) {
      overlay.hidden = false;
      numberEl.textContent = String(context.countdown);
      numberEl.style.animation = "none";
      void numberEl.offsetWidth; // force reflow so the pop animation replays each tick
      numberEl.style.animation = "";
    } else {
      overlay.hidden = true;
    }
  }

  function updateReadyOverlay() {
    const overlay = $("#ready-overlay");
    if (!overlay) return;
    overlay.hidden = !context.awaiting_ready;
  }

  function updateFramingGuide() {
    if (!hasGuide) return;
    const arrows = {forward: "↑", back: "↓", left: "←", right: "→", hold: "✓", align: "◎", detect: "◇"};
    const instruction = $("#framing-instruction");
    const arrow = $("#framing-arrow");
    const message = $("#framing-message");
    const detail = $("#framing-detail");
    const mode = $("#framing-mode");
    if (mode) mode.textContent = context.template_id === "upper_body" ? "상반신 구도" : "전신 구도";
    if (arrow) arrow.textContent = arrows[context.framing_direction] || "◇";
    if (message) message.textContent = context.framing_message || "몸을 실루엣에 맞춰주세요";
    if (detail) {
      detail.textContent = context.framing_required
        ? `실루엣 ${context.framing_inside}/${context.framing_required} · 크기 ${context.framing_scale.toFixed(2)} / 1.00`
        : "주황색 범위 안에 흰색 스켈레톤을 맞춰주세요";
    }
    instruction?.classList.toggle("positioned", context.framing_positioned);
  }

  function syncSlideshowPhotos() {
    const frame = $("#slideshow-frame");
    const film = $("#film-strip");
    if (!frame || !film) return;
    const signature = context.photos.join("\n");
    if (frame.dataset.signature === signature) return;
    frame.dataset.signature = signature;
    frame.replaceChildren();
    film.replaceChildren();
    slideIndex = 0;

    if (!context.photos.length) {
      const empty = document.createElement("div");
      empty.className = "slideshow-empty";
      empty.textContent = "사진을 불러오는 중이에요";
      frame.append(empty);
      $("#slide-count").textContent = "00 / 00";
      film.style.gridTemplateColumns = "1fr";
      return;
    }

    context.photos.forEach((photoUrl, index) => {
      const shot = document.createElement("figure");
      shot.className = `saved-shot${index === 0 ? " active" : ""}`;
      const backdrop = document.createElement("img");
      backdrop.className = "shot-backdrop";
      backdrop.src = photoUrl;
      backdrop.alt = "";
      backdrop.setAttribute("aria-hidden", "true");
      const image = document.createElement("img");
      image.className = "shot-photo";
      image.src = photoUrl;
      image.alt = `촬영 결과 ${index + 1}`;
      const label = document.createElement("span");
      label.textContent = `PHOTO · ${String(index + 1).padStart(2, "0")}`;
      shot.append(backdrop, image, label);
      frame.append(shot);
      const dot = document.createElement("i");
      dot.classList.toggle("active", index === 0);
      film.append(dot);
    });
    // 한 줄에 20칸까지만 — 40장을 한 줄에 밀어넣으면 칸당 13px짜리 점선이 된다.
    film.style.gridTemplateColumns = `repeat(${Math.min(context.photos.length, 20)}, 1fr)`;
    updateSlide();
  }

  function updateSlide() {
    const shots = $$("#slideshow-frame .saved-shot");
    const film = $$("#film-strip i");
    if (!shots.length) return;
    if (slideIndex >= shots.length) slideIndex = 0;
    shots.forEach((shot, index) => shot.classList.toggle("active", index === slideIndex));
    film.forEach((dot, index) => dot.classList.toggle("active", index === slideIndex));
    $("#slide-count").textContent = `${String(slideIndex + 1).padStart(2, "0")} / ${String(shots.length).padStart(2, "0")}`;
  }

  function startSlideshow() {
    updateSlide();
    effectTimer = window.setInterval(() => {
      const count = $$("#slideshow-frame .saved-shot").length;
      if (!count) return;
      slideIndex = (slideIndex + 1) % count;
      updateSlide();
    }, 288);
  }

  function updateData(previous) {
    if (hasGuide) {
      updateCountdown();
      updateReadyOverlay();
      updateFramingGuide();
      updateGestureChoice();
    }
    if (workflowState === "capturing") updateCaptureData(previous);
    if (workflowState === "previewing" && hasGuide) syncSlideshowPhotos();
    // The VLM line usually lands a second or two after the greeting caption
    // was already shown from the static list — swap it in live when it does.
    if (context.greeting_line && context.greeting_line !== previous.greeting_line) {
      $("#caption").textContent = currentCaption();
    }
    if (workflowState === "capturing" && context.awaiting_ready !== previous.awaiting_ready) {
      $("#caption").textContent = currentCaption();
    }
    // The prerecorded countdown already contains 3→2→1, so trigger it only
    // once when the backend starts the countdown instead of on every tick.
    if (workflowState === "capturing" && hasFace && context.countdown === 3 && !countdownVoicePlayed) {
      countdownVoicePlayed = true;
      playVoice(countdownVoiceFile);
    }
  }

  function updateGestureChoice() {
    $$(".shot-choice").forEach((button) => {
      const active = workflowState === "deciding" && button.dataset.template === context.gesture_choice;
      button.classList.toggle("gesture-active", active);
      button.style.setProperty("--gesture-progress", active ? context.gesture_progress : 0);
    });
  }

  function ensureFeedback() {
    let feedback = $("#action-feedback");
    if (!feedback) {
      feedback = document.createElement("div");
      feedback.id = "action-feedback";
      feedback.className = "action-feedback";
      feedback.setAttribute("role", "status");
      document.body.append(feedback);
    }
    return feedback;
  }

  function showActionError(message) {
    const feedback = ensureFeedback();
    feedback.textContent = message;
    feedback.classList.add("visible");
    window.setTimeout(() => feedback.classList.remove("visible"), 3200);
  }

  function ensureErrorOverlay() {
    let overlay = $("#workflow-error");
    if (overlay) return overlay;
    overlay = document.createElement("section");
    overlay.id = "workflow-error";
    overlay.className = "workflow-error";
    overlay.hidden = true;
    overlay.innerHTML = '<div><span>!</span><p class="eyebrow">SYSTEM PAUSED</p><h2>잠시 문제가 생겼어요</h2><p id="workflow-error-message"></p><button id="error-reset" type="button">다시 시작</button></div>';
    document.body.append(overlay);
    $("#error-reset").addEventListener("click", () => runAction("/api/reset"));
    return overlay;
  }

  function updateErrorOverlay() {
    const overlay = ensureErrorOverlay();
    const failed = workflowState === "error";
    overlay.hidden = !failed;
    if (failed) $("#workflow-error-message").textContent = context.error || "알 수 없는 오류가 발생했습니다.";
  }

  function updateControls() {
    const deciding = workflowState === "deciding";
    const asking = workflowState === "asking";
    $$(".shot-choice").forEach((button) => button.toggleAttribute("disabled", !deciding || actionPending));
    $("#decline")?.toggleAttribute("disabled", !deciding || actionPending);
    $("#review-again")?.toggleAttribute("disabled", !asking || actionPending);
    $("#review-like")?.toggleAttribute("disabled", !asking || actionPending);

    const next = $("#next-stage");
    if (!next) return;
    const enabled = deciding || asking || workflowState === "error";
    next.disabled = !enabled || actionPending;
    if (deciding) next.innerHTML = '촬영 시작 <span>→</span>';
    else if (asking) next.innerHTML = '마음에 들어요 <span>♥</span>';
    else if (workflowState === "error") next.innerHTML = '다시 시작 <span>↻</span>';
    else next.innerHTML = '자동 진행 대기 중 <span>·</span>';
  }

  async function post(path, body) {
    const options = {method: "POST"};
    if (body !== undefined) {
      options.headers = {"Content-Type": "application/json"};
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `요청에 실패했습니다. (HTTP ${response.status})`);
    }
    if (response.status === 204) return null;
    return response.json().catch(() => null);
  }

  async function runAction(path, body) {
    if (actionPending) return;
    actionPending = true;
    updateControls();
    try {
      await post(path, body);
    } catch (error) {
      showActionError(error.message);
    } finally {
      actionPending = false;
      updateControls();
    }
  }

  function runDebugAction() {
    if (workflowState === "deciding") runAction("/api/capture-started", {template_id: DEFAULT_TEMPLATE});
    if (workflowState === "asking") runAction("/api/liked");
    if (workflowState === "error") runAction("/api/reset");
  }

  function applySnapshot(snapshot) {
    if (!snapshot || typeof snapshot.state !== "string") return;
    const previous = context;
    const next = {
      state: snapshot.state,
      template_id: snapshot.template_id ?? null,
      photo_target: snapshot.photo_target ?? 0,
      gallery_url: snapshot.gallery_url ?? "",
      photos: Array.isArray(snapshot.photos) ? snapshot.photos : [],
      hint: snapshot.hint || "",
      error: snapshot.error || "",
      greeting_line: snapshot.greeting_line ?? null,
      countdown: snapshot.countdown ?? null,
      awaiting_ready: Boolean(snapshot.awaiting_ready),
      framing_message: snapshot.framing_message || "사람을 기다리는 중",
      framing_direction: snapshot.framing_direction || "detect",
      framing_scale: Number(snapshot.framing_scale) || 0,
      framing_inside: Number(snapshot.framing_inside) || 0,
      framing_required: Number(snapshot.framing_required) || 0,
      framing_positioned: Boolean(snapshot.framing_positioned),
      gesture_choice: snapshot.gesture_choice || "",
      gesture_progress: Math.max(0, Math.min(1, Number(snapshot.gesture_progress) || 0)),
      revision: Number.isFinite(snapshot.revision) ? snapshot.revision : 0,
    };
    const stateChanged = next.state !== workflowState;
    context = next;
    workflowState = next.state;

    if (stateToStage[workflowState]) {
      if (stateChanged) {
        if (workflowState === "deciding") carouselIndex = 0;
        if (workflowState === "capturing") countdownVoicePlayed = false;
        enterStage(stateToStage[workflowState]);
        if (hasFace) playStageVoice();
      }
      updateData(previous);
      setConnectionChip("online");
    } else if (workflowState === "booting") {
      if (stage !== 1) enterStage(1);
      setConnectionChip("booting");
    } else if (workflowState === "error") {
      setConnectionChip("error");
    }
    if (stateChanged && !stateToStage[workflowState] && hasFace) cancelSpeech();
    updateErrorOverlay();
    updateControls();
  }

  function drawQrCodes() {
    // QR은 서버가 context.gallery_url을 인코딩해 SVG로 내려준다. 링크가 아직
    // 없으면(갤러리 미설정, 또는 촬영 전) 코드가 아니라 안내 문구를 띄운다 —
    // 스캔되지 않는 그림을 QR처럼 보여주면 손님이 계속 찍어보게 된다.
    const ready = Boolean(context.gallery_url);
    $$(".qr-card").forEach((card) => {
      const image = card.querySelector(".qr-image");
      if (!image) return;
      let pending = card.querySelector(".qr-pending");
      if (!pending) {
        pending = document.createElement("div");
        pending.className = "qr-pending";
        image.after(pending);
      }
      image.hidden = !ready;
      pending.hidden = ready;
      if (ready) {
        const wanted = `/api/qr.svg?rev=${encodeURIComponent(context.gallery_url)}`;
        if (image.getAttribute("src") !== wanted) image.setAttribute("src", wanted);
      } else {
        pending.textContent = "사진 링크를 준비하고 있어요";
      }
    });
  }

  function setupCameraFeeds() {
    // <img>-driven MJPEG streams don't auto-reconnect the way EventSource
    // does — if the server restarts (or the connection just drops) the
    // stream dies silently and stays dead until something resets `src`.
    // Retry with a cache-busting query param instead of giving up.
    $$(".camera-feed").forEach((img) => {
      let retryTimer = null;
      const reconnect = () => {
        img.style.visibility = "hidden";
        window.clearTimeout(retryTimer);
        retryTimer = window.setTimeout(() => {
          img.src = `/live/camera?retry=${Date.now()}`;
        }, 1500);
      };
      img.addEventListener("error", reconnect);
      img.addEventListener("load", () => {
        img.style.visibility = "visible";
      });
    });
  }

  $("#carousel-prev")?.addEventListener("click", () => { carouselIndex = (carouselIndex + poseExampleCount - 1) % poseExampleCount; updateCarousel(); });
  $("#carousel-next")?.addEventListener("click", () => { carouselIndex = (carouselIndex + 1) % poseExampleCount; updateCarousel(); });
  $$(".shot-choice").forEach((button) => button.addEventListener("click", () => {
    runAction("/api/capture-started", {template_id: button.dataset.template});
  }));
  $("#decline")?.addEventListener("click", () => runAction("/api/decline"));
  $("#review-again")?.addEventListener("click", () => runAction("/api/replay"));
  $("#review-like")?.addEventListener("click", () => runAction("/api/liked"));
  $("#next-stage")?.addEventListener("click", runDebugAction);

  setupSpeech();
  setupShutterAudio();
  setupCameraFeeds();
  enterStage(1);
  updateCaptureData(context);
  updateErrorOverlay();
  updateControls();
  setConnectionChip("booting");

  const events = new EventSource("/events");
  events.addEventListener("state", (event) => {
    try {
      applySnapshot(JSON.parse(event.data));
    } catch (error) {
      showActionError(`상태 데이터를 읽지 못했습니다: ${error.message}`);
    }
  });
  events.addEventListener("error", () => setConnectionChip("reconnecting"));
})();
