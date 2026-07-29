const state = {
  apiKey: sessionStorage.getItem("linkparse_api_key") || "",
  info: null,
  lastResult: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const fluid = window.LinkParseFluid;
let viewSequence = 0;
let guideSequence = 0;

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.style.marginLeft = `${-toast.offsetWidth / 2}px`;
  fluid.reveal(toast, true);
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => fluid.reveal(toast, false), 2600);
}

function storeApiKey(value) {
  state.apiKey = value.trim();
  if (state.apiKey) sessionStorage.setItem("linkparse_api_key", state.apiKey);
  else sessionStorage.removeItem("linkparse_api_key");
  $("#parse-api-key").value = state.apiKey;
  $("#config-api-key").value = state.apiKey;
}

async function readJson(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload.error?.message || `请求失败（HTTP ${response.status}）`;
    const error = new Error(message);
    error.code = payload.error?.code;
    throw error;
  }
  return payload;
}

async function apiRequest(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.apiKey) headers.set("Authorization", `Bearer ${state.apiKey}`);
  return readJson(await fetch(path, { ...options, headers }));
}

function dockPositionFor(tab) {
  const dockBox = $("#fluid-dock").getBoundingClientRect();
  const tabBox = tab.getBoundingClientRect();
  return { y: tabBox.top - dockBox.top, height: tabBox.height };
}

function moveDockIndicator(name, immediate = false, velocityY = 0) {
  const tab = $(`.dock-item[data-view="${name}"]`);
  const indicator = $(".dock-indicator");
  if (!tab || !indicator) return;
  const { y, height } = dockPositionFor(tab);
  indicator.style.height = `${height}px`;
  if (immediate || fluid.reducedMotion.matches) {
    fluid.setNow(indicator, { y, opacity: 1 });
  } else {
    fluid.springTo(indicator, { y, opacity: 1 }, {
      stiffness: velocityY ? 430 : 760,
      damping: velocityY ? 34 : 55,
      velocity: { y: velocityY },
    });
  }
}

function setViewOrigin(view, sourceTab) {
  if (!view || !sourceTab) return;
  const sourceBox = sourceTab.getBoundingClientRect();
  const stageBox = $("#stage").getBoundingClientRect();
  view.style.transformOrigin = `${sourceBox.left + sourceBox.width / 2 - stageBox.left}px 0`;
}

async function activateView(name, updateHash = true, immediate = false, velocityX = 0) {
  const target = $(`#view-${name}`);
  if (!target) return;
  const sequence = ++viewSequence;
  const current = $(".view.active");
  const sourceTab = $(`.dock-item[data-view="${name}"]`);
  $$(".dock-item").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === name));
  moveDockIndicator(name, immediate, velocityX);
  setViewOrigin(current, sourceTab);
  setViewOrigin(target, sourceTab);
  if (updateHash) history.replaceState(null, "", `#${name}`);
  if (current === target) {
    fluid.springTo(target, { opacity: 1, x: 0, y: 0, scale: 1 }, { stiffness: 720, damping: 54 });
    return;
  }
  if (current) {
    await fluid.springTo(current, { opacity: 0, y: 14, scale: .988 }, { stiffness: 720, damping: 54 });
    if (sequence !== viewSequence) return;
    current.classList.remove("active");
  }
  target.classList.add("active");
  if (immediate) {
    fluid.setNow(target, { opacity: 1, x: 0, y: 0, scale: 1 });
    window.scrollTo({ top: 0, behavior: "auto" });
    return;
  }
  fluid.setNow(target, fluid.reducedMotion.matches
    ? { opacity: 0, x: 0, y: 0, scale: 1 }
    : { opacity: 0, x: 0, y: 14, scale: .988 });
  window.scrollTo({ top: 0, behavior: fluid.reducedMotion.matches ? "auto" : "smooth" });
  await fluid.springTo(target, { opacity: 1, x: 0, y: 0, scale: 1 }, { stiffness: 720, damping: 54 });
}

function updateHealth(payload) {
  const healthy = payload.status === "ok";
  const status = $("#service-status");
  status.className = `service-orb ${healthy ? "" : "down"}`;
  status.innerHTML = `<i></i><span>${healthy ? "服务正常" : "服务降级"}</span>`;
  $("#system-state").textContent = healthy ? "全部引擎就绪" : "部分能力不可用";
  Object.entries(payload.engines || {}).forEach(([engine, available]) => {
    $(`.engine-node[data-engine="${engine}"]`)?.classList.toggle("online", available);
  });
}

async function loadPublicInfo() {
  try {
    const [health, info] = await Promise.all([apiRequest("/health"), apiRequest("/v1/info")]);
    state.info = info;
    updateHealth(health);
    $("#service-version").textContent = `v${info.version}`;
    $("#limit-upload").textContent = `${info.limits.max_upload_mb} MB`;
    $("#limit-pages").textContent = `${info.limits.max_pdf_pages} 页`;
    $("#limit-dpi").textContent = `${info.limits.default_dpi} DPI`;
    $("#limit-ttl").textContent = `${info.limits.result_ttl_hours} 小时`;
    $("#drop-limit").textContent = `${info.limits.max_upload_mb}MB`;
    $("#parse-dpi").value = info.limits.default_dpi;
    $("#parse-dpi").max = info.limits.max_dpi;
  } catch {
    const status = $("#service-status");
    status.className = "service-orb down";
    status.innerHTML = "<i></i><span>连接失败</span>";
    $("#system-state").textContent = "无法连接服务";
  }
}

function updateFileLabel(file) {
  if (!file) {
    $("#file-label").textContent = "选择或拖入文档";
    return;
  }
  const size = file.size < 1024 * 1024
    ? `${Math.ceil(file.size / 1024)} KB`
    : `${(file.size / 1024 / 1024).toFixed(1)} MB`;
  $("#file-label").textContent = `${file.name} · ${size}`;
}

function renderResult(payload) {
  state.lastResult = payload;
  $("#result-empty").classList.add("hidden");
  $("#result-content").classList.remove("hidden");
  $("#copy-result").disabled = false;
  const summary = $("#result-summary");
  summary.replaceChildren();
  [
    `ENGINE ${payload.engine || "—"}`,
    `TYPE ${payload.detected_type || "—"}`,
    `PAGES ${payload.meta?.page_count ?? "—"}`,
    `TIME ${payload.meta?.duration_ms ?? "—"}ms`,
  ].forEach((value) => {
    const chip = document.createElement("span");
    chip.textContent = value;
    summary.append(chip);
  });
  $("#result-json").textContent = JSON.stringify(payload, null, 2);
  fluid.setNow($("#result-content"), { opacity: 0, y: 12, scale: .99 });
  fluid.springTo($("#result-content"), { opacity: 1, y: 0, scale: 1 }, { stiffness: 700, damping: 53 });
}

async function submitParse(event) {
  event.preventDefault();
  storeApiKey($("#parse-api-key").value);
  const file = $("#parse-file").files[0];
  if (!file || !state.apiKey) {
    showToast("请选择文件并填写 API Key", true);
    return;
  }
  const formats = $$('#parse-formats input[type="checkbox"]:checked').map((item) => item.value);
  if (!formats.length) {
    showToast("至少选择一种输出格式", true);
    return;
  }
  const body = new FormData();
  body.append("file", file);
  body.append("engine", $("#parse-engine").value);
  body.append("ocr", $("#parse-ocr").value);
  body.append("dpi", $("#parse-dpi").value);
  body.append("output_formats", formats.join(","));
  body.append("include_bbox", String($("#parse-bbox").checked));
  const button = $("#parse-submit");
  button.disabled = true;
  button.textContent = "正在解析…";
  try {
    renderResult(await apiRequest("/v1/parse", { method: "POST", body }));
    showToast("解析完成");
  } catch (error) {
    renderResult({ error: { code: error.code || "REQUEST_FAILED", message: error.message } });
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "开始解析";
  }
}

function fillConfig(payload) {
  Object.entries(payload.values).forEach(([name, value]) => {
    const input = $(`[name="${name}"]`, $("#config-form"));
    if (input) input.value = value;
  });
  $("#config-fields").disabled = false;
  const form = $("#config-form");
  form.classList.remove("locked");
  fluid.springTo(form, { opacity: 1, scale: 1 }, { stiffness: 700, damping: 53 });
  const source = $("#config-source");
  source.textContent = payload.source === "runtime" ? "运行时配置" : "环境默认值";
  source.classList.toggle("runtime", payload.source === "runtime");
  const access = $("#config-access-status");
  access.textContent = payload.updated_at
    ? `已连接 · 更新于 ${new Date(payload.updated_at).toLocaleString()}`
    : "已连接 · 使用环境默认值";
  access.className = "access-status ok";
}

async function loadConfig() {
  storeApiKey($("#config-api-key").value);
  if (!state.apiKey) {
    showToast("请先填写 API Key", true);
    return;
  }
  const button = $("#load-config");
  button.disabled = true;
  try {
    fillConfig(await apiRequest("/v1/admin/config"));
    showToast("配置已读取");
  } catch (error) {
    $("#config-access-status").textContent = error.code === "UNAUTHORIZED" ? "API Key 验证失败" : error.message;
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function configPayload() {
  return Object.fromEntries(
    $$('.config-grid input[type="number"]', $("#config-form")).map((input) => [input.name, Number(input.value)])
  );
}

async function saveConfig(event) {
  event.preventDefault();
  try {
    const payload = await apiRequest("/v1/admin/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(configPayload()),
    });
    fillConfig(payload);
    await loadPublicInfo();
    showToast("运行配置已保存");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function resetConfig() {
  if (!window.confirm("确定恢复环境变量中的默认配置吗？")) return;
  try {
    fillConfig(await apiRequest("/v1/admin/config", { method: "DELETE" }));
    await loadPublicInfo();
    showToast("已恢复环境默认值");
  } catch (error) {
    showToast(error.message, true);
  }
}

function moveGuideIndicator(button, immediate = false) {
  const rail = $(".guide-rail");
  const indicator = $(".guide-indicator");
  if (!rail || !indicator || !button) return;
  const railBox = rail.getBoundingClientRect();
  const buttonBox = button.getBoundingClientRect();
  indicator.style.height = `${buttonBox.height}px`;
  const y = buttonBox.top - railBox.top;
  if (immediate || fluid.reducedMotion.matches) fluid.setNow(indicator, { y, opacity: 1 });
  else fluid.springTo(indicator, { y, opacity: 1 }, { stiffness: 760, damping: 55 });
}

async function activateGuide(button) {
  const target = $(`#guide-${button.dataset.guide}`);
  const current = $(".guide-article.active");
  if (!target || current === target) return;
  const sequence = ++guideSequence;
  $$(".guide-rail button").forEach((item) => item.classList.toggle("active", item === button));
  moveGuideIndicator(button);
  if (current) {
    await fluid.springTo(current, { opacity: 0, y: 10, scale: .99 }, { stiffness: 720, damping: 54 });
    if (sequence !== guideSequence) return;
    current.classList.remove("active");
  }
  target.classList.add("active");
  fluid.setNow(target, fluid.reducedMotion.matches
    ? { opacity: 0, y: 0, scale: 1 }
    : { opacity: 0, y: 10, scale: .99 });
  fluid.springTo(target, { opacity: 1, y: 0, scale: 1 }, { stiffness: 720, damping: 54 });
}

function transferToParser(files) {
  if (!files?.length) return;
  $("#parse-file").files = files;
  updateFileLabel(files[0]);
  activateView("playground");
}

function bindDropSurface(surface, onDrop) {
  ["dragenter", "dragover"].forEach((name) => surface.addEventListener(name, (event) => {
    event.preventDefault();
    surface.classList.add("dragging");
    fluid.springTo(surface, { scale: .985 }, { stiffness: 700, damping: 53 });
  }));
  ["dragleave", "drop"].forEach((name) => surface.addEventListener(name, (event) => {
    event.preventDefault();
    surface.classList.remove("dragging");
    fluid.springTo(surface, { scale: 1 }, { stiffness: 700, damping: 53 });
  }));
  surface.addEventListener("drop", (event) => onDrop(event.dataTransfer.files));
}

function bindSurfacePress(surface) {
  const release = () => fluid.springTo(surface, { scale: 1 }, { stiffness: 700, damping: 53 });
  surface.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    surface.setPointerCapture?.(event.pointerId);
    fluid.setNow(surface, { scale: .985 });
  });
  surface.addEventListener("pointerup", release);
  surface.addEventListener("pointercancel", release);
  surface.addEventListener("lostpointercapture", release);
}

function bindInteractions() {
  $$(".dock-item").forEach((tab) => tab.addEventListener("click", () => activateView(tab.dataset.view)));
  $$('[data-go]').forEach((button) => button.addEventListener("click", () => activateView(button.dataset.go)));
  $$(".guide-rail button").forEach((button) => button.addEventListener("click", () => activateGuide(button)));
  $("#parse-form").addEventListener("submit", submitParse);
  $("#parse-file").addEventListener("change", (event) => updateFileLabel(event.target.files[0]));
  $("#hero-file").addEventListener("change", (event) => transferToParser(event.target.files));
  $("#parse-api-key").addEventListener("change", (event) => storeApiKey(event.target.value));
  $("#config-api-key").addEventListener("change", (event) => storeApiKey(event.target.value));
  $("#load-config").addEventListener("click", loadConfig);
  $("#config-form").addEventListener("submit", saveConfig);
  $("#reset-config").addEventListener("click", resetConfig);
  $("#copy-result").addEventListener("click", async () => {
    if (!state.lastResult) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(state.lastResult, null, 2));
      showToast("结果已复制");
    } catch {
      showToast("浏览器未授予剪贴板权限", true);
    }
  });

  bindDropSurface($("#hero-drop"), transferToParser);
  bindDropSurface($("#dropzone"), (files) => {
    if (!files.length) return;
    $("#parse-file").files = files;
    updateFileLabel(files[0]);
  });
  bindSurfacePress($("#hero-drop"));

  window.addEventListener("hashchange", () => activateView(location.hash.slice(1) || "overview", false));
  window.addEventListener("resize", () => {
    moveDockIndicator(location.hash.slice(1) || "overview", true);
    moveGuideIndicator($(".guide-rail button.active"), true);
  });
  $$(".engine-node").forEach((card) => {
    card.addEventListener("pointerenter", () => fluid.springTo(card, { y: -4, scale: 1.012 }, { stiffness: 600, damping: 49 }));
    card.addEventListener("pointerleave", () => fluid.springTo(card, { y: 0, scale: 1 }, { stiffness: 600, damping: 49 }));
  });
  fluid.bindPress(document);
}

function initialize() {
  storeApiKey(state.apiKey);
  bindInteractions();
  const initialView = location.hash.slice(1) || "overview";
  $$(".view").forEach((view) => view.classList.remove("active"));
  activateView(initialView, false, true);
  const initialGuide = $(".guide-article.active");
  if (initialGuide) fluid.setNow(initialGuide, { opacity: 1, y: 0, scale: 1 });
  moveGuideIndicator($(".guide-rail button.active"), true);
  $$(".guide-origin").forEach((node) => { node.textContent = location.origin; });
  loadPublicInfo();
  window.setInterval(loadPublicInfo, 30_000);
}

initialize();
