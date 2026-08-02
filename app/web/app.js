const state = {
  parseApiKey: sessionStorage.getItem("linkparse_parse_api_key")
    || sessionStorage.getItem("linkparse_api_key") || "",
  sessionToken: sessionStorage.getItem("linkparse_session_token") || "",
  user: null,
  info: null,
  lastResult: null,
  records: [],
  recordTotal: 0,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const fluid = window.LinkParseFluid;
let viewSequence = 0;
let guideSequence = 0;

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.setAttribute("role", error ? "alert" : "status");
  toast.classList.toggle("error", error);
  toast.style.marginLeft = "0";
  fluid.reveal(toast, true);
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => fluid.reveal(toast, false), 2600);
}

function storeParseApiKey(value) {
  state.parseApiKey = value.trim();
  sessionStorage.removeItem("linkparse_api_key");
  if (state.parseApiKey) sessionStorage.setItem("linkparse_parse_api_key", state.parseApiKey);
  else sessionStorage.removeItem("linkparse_parse_api_key");
  $("#parse-api-key").value = state.parseApiKey;
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
  const authToken = options.sessionOnly
    ? state.sessionToken
    : (state.parseApiKey || state.sessionToken);
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
  const { sessionOnly: _sessionOnly, ...fetchOptions } = options;
  return readJson(await fetch(path, { ...fetchOptions, headers }));
}

function setSession(payload = null) {
  state.sessionToken = payload?.access_token || "";
  state.user = payload?.user || null;
  if (state.sessionToken) sessionStorage.setItem("linkparse_session_token", state.sessionToken);
  else sessionStorage.removeItem("linkparse_session_token");
  renderSessionState();
}

function renderSessionState() {
  const entry = $("#user-entry");
  const signedIn = Boolean(state.user && state.sessionToken);
  document.body.classList.remove("auth-pending");
  $("#auth-gate").classList.toggle("hidden", signedIn);
  $("#app-shell").classList.toggle("hidden", !signedIn);
  $("#app-shell").setAttribute("aria-hidden", String(!signedIn));
  if (state.user) {
    const isAdmin = Boolean(state.user.is_admin);
    entry.querySelector(".user-avatar").textContent = state.user.username.slice(0, 1).toUpperCase();
    entry.querySelector("strong").textContent = state.user.username;
    entry.querySelector("small").textContent = isAdmin ? "管理员账户" : "标准账户";
    $(".admin-only")?.classList.toggle("hidden", !isAdmin);
    $("#config-admin-name").textContent = state.user.username;
    $("#parse-auth-hint").textContent = `当前：${state.user.username}`;
  } else {
    $(".admin-only")?.classList.add("hidden");
    $("#parse-auth-hint").textContent = "登录后可直接解析";
    history.replaceState(null, "", location.pathname);
    switchAuthTab("login");
  }
  $("#account-guest").classList.toggle("hidden", Boolean(state.user));
  $("#account-dashboard").classList.toggle("hidden", !state.user);
}

function openAuth(tab = "login") {
  if (state.user) return;
  switchAuthTab(tab);
  document.body.classList.remove("auth-pending");
  $("#auth-gate").classList.remove("hidden");
  $("#app-shell").classList.add("hidden");
  window.requestAnimationFrame(() => $(`#${tab}-form input`)?.focus());
}

function switchAuthTab(tab) {
  $$("[data-auth-tab]").forEach((button) => {
    const selected = button.dataset.authTab === tab;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  });
  $$(".auth-form").forEach((form) => form.classList.toggle("active", form.id === `${tab}-form`));
  $("#auth-error").textContent = "";
  if (!$("#auth-gate").classList.contains("hidden")) {
    window.requestAnimationFrame(() => $(`#${tab}-form input`)?.focus());
  }
}

async function submitAuth(event, mode) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const button = $("button[type=submit]", formElement);
  const form = new FormData(formElement);
  $("#auth-error").textContent = "";
  button.disabled = true;
  try {
    const payload = await apiRequest(`/v1/auth/${mode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.fromEntries(form)),
      sessionOnly: true,
    });
    setSession(payload);
    storeParseApiKey("");
    formElement.reset();
    await loadAccount();
    const requestedView = location.hash.slice(1);
    activateView($(`#view-${requestedView}`) ? requestedView : "playground", false, true);
    showToast(mode === "register" ? "账户创建成功" : "登录成功");
  } catch (error) {
    $("#auth-error").textContent = error.message;
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function restoreSession() {
  if (!state.sessionToken) {
    renderSessionState();
    return;
  }
  try {
    state.user = await apiRequest("/v1/auth/me", { sessionOnly: true });
    renderSessionState();
    await loadAccount();
  } catch {
    setSession();
  }
}

async function logout() {
  try {
    await apiRequest("/v1/auth/logout", { method: "POST", sessionOnly: true });
  } catch { /* Expired sessions are already effectively logged out. */ }
  setSession();
  showToast("已退出登录");
}

function renderKeys(payload) {
  const list = $("#key-list");
  list.replaceChildren();
  if (!payload.items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = "还没有 API Key，点击右上角申请。";
    list.append(empty);
    return;
  }
  payload.items.forEach((item) => {
    const row = document.createElement("article");
    row.className = `key-row ${item.status}`;
    const copy = document.createElement("div");
    const name = document.createElement("strong");
    const meta = document.createElement("small");
    name.textContent = item.name;
    meta.textContent = `${item.prefix}•••• · ${item.status === "active" ? "有效" : "已撤销"} · ${new Date(item.created_at).toLocaleDateString()}`;
    copy.append(name, meta);
    row.append(copy);
    if (item.status === "active") {
      const revoke = document.createElement("button");
      revoke.className = "text-action danger-text";
      revoke.textContent = "撤销";
      revoke.addEventListener("click", () => revokeKey(item.id));
      row.append(revoke);
    }
    list.append(row);
  });
}

function renderRecords(payload) {
  state.records = payload.items;
  state.recordTotal = payload.total;
  const tbody = $("#record-list");
  tbody.replaceChildren();
  $("#record-empty").classList.toggle("hidden", Boolean(payload.items.length));
  payload.items.forEach((item) => {
    const row = document.createElement("tr");
    const values = [
      item.filename,
      item.status === "succeeded" ? "成功" : item.status === "failed" ? "失败" : "处理中",
      item.engine,
      item.page_count ?? "—",
      item.duration_ms == null ? "—" : `${item.duration_ms}ms`,
      new Date(item.created_at).toLocaleString(),
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 1) cell.className = `record-status ${item.status}`;
      if (index === 0 && item.error) cell.title = `${item.error.code}: ${item.error.message}`;
      row.append(cell);
    });
    tbody.append(row);
  });
  const recentBody = $("#recent-record-list");
  recentBody.replaceChildren();
  payload.items.slice(0, 5).forEach((item) => {
    const row = document.createElement("tr");
    const values = [
      item.filename,
      item.engine,
      item.status === "succeeded" ? "已完成" : item.status === "failed" ? "失败" : "处理中",
      item.page_count ?? "—",
      item.duration_ms == null ? "—" : `${item.duration_ms}ms`,
      new Date(item.created_at).toLocaleString(),
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 2) cell.className = `record-status ${item.status}`;
      row.append(cell);
    });
    recentBody.append(row);
  });
  $("#recent-record-empty").classList.toggle("hidden", Boolean(payload.items.length));
  $("#record-progress").textContent = `已显示 ${state.records.length} / ${state.recordTotal}`;
  $("#load-more-records").classList.toggle("hidden", state.records.length >= state.recordTotal);
}

async function loadRecords(append = false) {
  if (!state.sessionToken) return;
  const offset = append ? state.records.length : 0;
  const payload = await apiRequest(`/v1/account/records?limit=20&offset=${offset}`, {
    sessionOnly: true,
  });
  if (append) payload.items = [...state.records, ...payload.items];
  renderRecords(payload);
}

async function loadAccount() {
  if (!state.sessionToken) return;
  try {
    const [profile, keys, records] = await Promise.all([
      apiRequest("/v1/auth/me", { sessionOnly: true }),
      apiRequest("/v1/account/keys", { sessionOnly: true }),
      apiRequest("/v1/account/records", { sessionOnly: true }),
    ]);
    state.user = profile;
    renderSessionState();
    $("#profile-avatar").textContent = profile.username.slice(0, 1).toUpperCase();
    $("#profile-name").textContent = profile.username;
    $("#profile-email").textContent = profile.email;
    $("#profile-key-count").textContent = profile.stats.active_keys;
    $("#profile-record-count").textContent = profile.stats.parse_records;
    $("#profile-created").textContent = new Date(profile.created_at).toLocaleDateString();
    renderKeys(keys);
    renderRecords(records);
  } catch (error) {
    if (error.code === "UNAUTHORIZED" || error.code === "SESSION_REQUIRED") setSession();
    else showToast(error.message, true);
  }
}

async function createKey() {
  $("#key-name-form").reset();
  $("#key-name-error").textContent = "";
  $("#key-name-dialog").showModal();
  window.requestAnimationFrame(() => $('#key-name-form input[name="name"]')?.focus());
}

async function submitCreateKey(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("button[type=submit]", form);
  const name = new FormData(form).get("name")?.toString().trim();
  if (!name) return;
  $("#key-name-error").textContent = "";
  button.disabled = true;
  try {
    const payload = await apiRequest("/v1/account/keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
      sessionOnly: true,
    });
    $("#key-name-dialog").close();
    $("#new-key-value").textContent = payload.key;
    $("#key-dialog").showModal();
    await loadAccount();
  } catch (error) {
    $("#key-name-error").textContent = error.message;
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function revokeKey(id) {
  if (!window.confirm("撤销后使用该 Key 的调用会立即失效，确定继续吗？")) return;
  try {
    await apiRequest(`/v1/account/keys/${id}`, { method: "DELETE", sessionOnly: true });
    await loadAccount();
    showToast("API Key 已撤销");
  } catch (error) {
    showToast(error.message, true);
  }
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
  if (!state.user) {
    openAuth();
    return;
  }
  if (name === "configuration" && !state.user.is_admin) {
    name = "playground";
    history.replaceState(null, "", "#playground");
    if (!immediate) showToast("当前账户没有运行策略管理权限", true);
  }
  const target = $(`#view-${name}`);
  if (!target) return;
  const sequence = ++viewSequence;
  const current = $(".view.active");
  const sourceTab = $(`.dock-item[data-view="${name}"]`);
  $$(".dock-item").forEach((tab) => {
    const selected = tab.dataset.view === name;
    tab.classList.toggle("active", selected);
    if (selected) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  });
  if (sourceTab && window.matchMedia("(max-width: 900px)").matches) {
    sourceTab.scrollIntoView({
      behavior: immediate || fluid.reducedMotion.matches ? "auto" : "smooth",
      block: "nearest",
      inline: "center",
    });
  }
  moveDockIndicator(name, immediate, velocityX);
  setViewOrigin(current, sourceTab);
  setViewOrigin(target, sourceTab);
  if (updateHash) history.replaceState(null, "", `#${name}`);
  if (current === target) {
    fluid.springTo(target, { opacity: 1, x: 0, y: 0, scale: 1 }, { stiffness: 720, damping: 54 });
    if (name === "configuration") loadConfig(false);
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
    if (name === "configuration") loadConfig(false);
    return;
  }
  fluid.setNow(target, fluid.reducedMotion.matches
    ? { opacity: 0, x: 0, y: 0, scale: 1 }
    : { opacity: 0, x: 0, y: 14, scale: .988 });
  window.scrollTo({ top: 0, behavior: fluid.reducedMotion.matches ? "auto" : "smooth" });
  await fluid.springTo(target, { opacity: 1, x: 0, y: 0, scale: 1 }, { stiffness: 720, damping: 54 });
  if (name === "configuration") loadConfig(false);
}

function updateHealth(payload) {
  const healthy = payload.status === "ok";
  const status = $("#service-status");
  status.className = `service-orb ${healthy ? "" : "down"}`;
  status.innerHTML = `<i></i><span>${healthy ? "服务正常" : "服务降级"}</span>`;
  const authStatus = $("#auth-service-status");
  authStatus.className = `auth-service service-orb ${healthy ? "" : "down"}`;
  authStatus.innerHTML = `<i></i><span>${healthy ? "服务正常，可以登录" : "服务当前降级"}</span>`;
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
    const authStatus = $("#auth-service-status");
    authStatus.className = "auth-service service-orb down";
    authStatus.innerHTML = "<i></i><span>服务连接失败</span>";
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
  const gallery = $("#asset-gallery");
  gallery.replaceChildren();
  const assets = Array.isArray(payload.assets) ? payload.assets : [];
  gallery.classList.toggle("hidden", !assets.length);
  assets.forEach((asset) => {
    const link = document.createElement("a");
    link.href = asset.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const image = document.createElement("img");
    image.src = asset.url;
    image.alt = asset.filename || "解析图片";
    image.loading = "lazy";
    const label = document.createElement("span");
    label.textContent = `${asset.page ? `P${asset.page} · ` : ""}${asset.filename}`;
    link.append(image, label);
    gallery.append(link);
  });
  $("#result-json").textContent = JSON.stringify(payload, null, 2);
  fluid.setNow($("#result-content"), { opacity: 0, y: 12, scale: .99 });
  fluid.springTo($("#result-content"), { opacity: 1, y: 0, scale: 1 }, { stiffness: 700, damping: 53 });
}

async function submitParse(event) {
  event.preventDefault();
  storeParseApiKey($("#parse-api-key").value);
  const file = $("#parse-file").files[0];
  if (!file) {
    showToast("请先选择要解析的文件", true);
    return;
  }
  if (!state.parseApiKey && !state.sessionToken) {
    openAuth();
    showToast("请先登录，或填写 API Key", true);
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
  body.append("include_images", String($("#parse-images").checked));
  const button = $("#parse-submit");
  button.disabled = true;
  button.innerHTML = '<i class="ph ph-spinner-gap" aria-hidden="true"></i><span>正在解析…</span>';
  try {
    renderResult(await apiRequest("/v1/parse", { method: "POST", body }));
    showToast("解析完成");
    if (state.sessionToken) loadAccount();
  } catch (error) {
    renderResult({ error: { code: error.code || "REQUEST_FAILED", message: error.message } });
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.innerHTML = '<i class="ph ph-play" aria-hidden="true"></i><span>开始解析</span>';
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
  const concurrency = payload.concurrency;
  const available = concurrency?.available;
  const ocr = concurrency?.engines?.rapidocr;
  const odl = concurrency?.engines?.opendataloader;
  $("#ocr-concurrency-state").textContent = available && ocr
    ? `当前占用 ${ocr.active} / ${ocr.limit}`
    : "Redis 状态暂不可用";
  $("#odl-concurrency-state").textContent = available && odl
    ? `当前占用 ${odl.active} / ${odl.limit}`
    : "Redis 状态暂不可用";
  const access = $("#config-access-status");
  access.textContent = payload.updated_at
    ? `已连接 · 更新于 ${new Date(payload.updated_at).toLocaleString()}`
    : "已连接 · 使用环境默认值";
  access.className = "config-status ok";
}

async function loadConfig(notify = true) {
  if (!state.user?.is_admin) {
    activateView("playground");
    return;
  }
  const button = $("#load-config");
  button.disabled = true;
  $("#config-access-status").textContent = "正在读取当前配置…";
  try {
    fillConfig(await apiRequest("/v1/admin/config", { sessionOnly: true }));
    if (notify) showToast("运行策略已刷新");
  } catch (error) {
    $("#config-access-status").textContent = error.code === "ADMIN_REQUIRED" ? "当前账户没有管理员权限" : error.message;
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
      sessionOnly: true,
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
    fillConfig(await apiRequest("/v1/admin/config", { method: "DELETE", sessionOnly: true }));
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

function bindInteractions() {
  $$(".dock-item").forEach((tab) => tab.addEventListener("click", () => activateView(tab.dataset.view)));
  $$('[data-go]').forEach((button) => button.addEventListener("click", () => activateView(button.dataset.go)));
  $$(".guide-rail button").forEach((button) => button.addEventListener("click", () => activateGuide(button)));
  $("#parse-form").addEventListener("submit", submitParse);
  $("#parse-file").addEventListener("change", (event) => updateFileLabel(event.target.files[0]));
  $("#parse-api-key").addEventListener("change", (event) => storeParseApiKey(event.target.value));
  $("#load-config").addEventListener("click", () => loadConfig(true));
  $("#config-form").addEventListener("submit", saveConfig);
  $("#reset-config").addEventListener("click", resetConfig);
  $("#account-session-action").addEventListener("click", logout);
  $$("[data-auth-tab]").forEach((button) => button.addEventListener("click", () => switchAuthTab(button.dataset.authTab)));
  $("#login-form").addEventListener("submit", (event) => submitAuth(event, "login"));
  $("#register-form").addEventListener("submit", (event) => submitAuth(event, "register"));
  $("#create-key").addEventListener("click", createKey);
  $("#key-name-form").addEventListener("submit", submitCreateKey);
  $("#key-name-close").addEventListener("click", () => $("#key-name-dialog").close());
  $("#refresh-records").addEventListener("click", () => loadRecords(false));
  $("#load-more-records").addEventListener("click", () => loadRecords(true));
  $("#key-close").addEventListener("click", () => $("#key-dialog").close());
  $("#copy-new-key").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("#new-key-value").textContent);
      showToast("API Key 已复制");
    } catch {
      showToast("浏览器未授予剪贴板权限", true);
    }
  });
  $("#copy-result").addEventListener("click", async () => {
    if (!state.lastResult) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(state.lastResult, null, 2));
      showToast("结果已复制");
    } catch {
      showToast("浏览器未授予剪贴板权限", true);
    }
  });

  bindDropSurface($("#dropzone"), (files) => {
    if (!files.length) return;
    $("#parse-file").files = files;
    updateFileLabel(files[0]);
  });
  $("#advanced-toggle").addEventListener("click", () => {
    const button = $("#advanced-toggle");
    const expanded = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", String(!expanded));
    $("#advanced-options").hidden = expanded;
  });

  window.addEventListener("hashchange", () => {
    if (!state.user) return;
    const requested = location.hash.slice(1) || "playground";
    activateView($(`#view-${requested}`) ? requested : "playground", false);
  });
  window.addEventListener("resize", () => {
    moveDockIndicator(location.hash.slice(1) || "playground", true);
    moveGuideIndicator($(".guide-rail button.active"), true);
  });
  fluid.bindPress(document);
}

async function initialize() {
  storeParseApiKey(state.parseApiKey);
  bindInteractions();
  $$(".view").forEach((view) => view.classList.remove("active"));
  const initialGuide = $(".guide-article.active");
  if (initialGuide) fluid.setNow(initialGuide, { opacity: 1, y: 0, scale: 1 });
  moveGuideIndicator($(".guide-rail button.active"), true);
  $$(".guide-origin").forEach((node) => { node.textContent = location.origin; });
  loadPublicInfo();
  await restoreSession();
  if (state.user) {
    const requestedView = location.hash.slice(1) || "playground";
    activateView($(`#view-${requestedView}`) ? requestedView : "playground", false, true);
  }
  window.setInterval(loadPublicInfo, 30_000);
}

initialize();
