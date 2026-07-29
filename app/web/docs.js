const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const formatExamples = {
  markdown: {
    path: "outputs.markdown",
    value: `{
  "outputs": {
    "markdown": "# 项目方案\\n\\n正文内容……"
  }
}`,
  },
  json: {
    path: "outputs.json",
    value: `{
  "outputs": {
    "json": {
      "pages": [{
        "page": 1,
        "text": "正文内容……",
        "blocks": []
      }]
    }
  }
}`,
  },
  text: {
    path: "outputs.text",
    value: `{
  "outputs": {
    "text": "项目方案\\n\\n正文内容……"
  }
}`,
  },
  html: {
    path: "outputs.html",
    value: `{
  "outputs": {
    "html": "<section data-page=\\"1\\">…</section>"
  }
}`,
  },
};

function showDocsToast(message) {
  const toast = document.querySelector("#docs-toast");
  toast.textContent = message;
  toast.style.marginLeft = `${-toast.offsetWidth / 2}px`;
  window.LinkParseFluid.reveal(toast, true);
  window.clearTimeout(showDocsToast.timer);
  showDocsToast.timer = window.setTimeout(() => window.LinkParseFluid.reveal(toast, false), 1800);
}

async function writeClipboard(value, successMessage) {
  try {
    await navigator.clipboard.writeText(value);
    showDocsToast(successMessage);
    return true;
  } catch {
    showDocsToast("浏览器未授予剪贴板权限");
    return false;
  }
}

async function copyExample(id, button) {
  const source = document.querySelector(`#${id}`)?.innerText || "";
  if (await writeClipboard(source, "请求示例已复制")) {
    button.textContent = "已复制";
    window.setTimeout(() => { button.textContent = "复制"; }, 1600);
  }
}

function observeSections() {
  const links = $$(".docs-sidebar nav a");
  const jump = document.querySelector("#section-jump");
  const byId = new Map(links.map((link) => [link.hash.slice(1), link]));
  const sections = $$(".doc-section");
  let frame = 0;
  const update = () => {
    frame = 0;
    const current = sections.reduce((selected, section) => (
      section.getBoundingClientRect().top <= 150 ? section : selected
    ), sections[0]);
    links.forEach((link) => link.classList.toggle("active", link === byId.get(current.id)));
    if (jump && [...jump.options].some((option) => option.value === current.id)) jump.value = current.id;
  };
  window.addEventListener("scroll", () => {
    if (!frame) frame = window.requestAnimationFrame(update);
  }, { passive: true });
  update();
}

function bindFormatTabs() {
  const preview = document.querySelector(".format-preview");
  const path = document.querySelector("#format-path");
  const example = document.querySelector("#format-example");
  $$(".format-tab").forEach((tab) => {
    tab.addEventListener("click", async () => {
      if (tab.classList.contains("active")) return;
      $$(".format-tab").forEach((item) => {
        const selected = item === tab;
        item.classList.toggle("active", selected);
        item.setAttribute("aria-selected", String(selected));
      });
      await window.LinkParseFluid.springTo(preview, { opacity: .2, y: 8 });
      const selected = formatExamples[tab.dataset.format];
      path.textContent = selected.path;
      example.textContent = selected.value;
      window.LinkParseFluid.springTo(preview, { opacity: 1, y: 0 });
    });
  });
}

async function loadStatus() {
  const status = document.querySelector("#docs-status");
  try {
    const response = await fetch("/health");
    if (!response.ok) throw new Error("unhealthy");
    const payload = await response.json();
    status.classList.add("online");
    status.querySelector("span").textContent = payload.status === "ok" ? "服务正常" : "服务降级";
  } catch {
    status.classList.add("offline");
    status.querySelector("span").textContent = "服务不可用";
  }
}

function initializeDocs() {
  document.querySelector("#base-url").textContent = location.origin;
  $$(".origin-token").forEach((node) => { node.textContent = location.origin; });
  $$(".copy-code").forEach((button) => {
    button.addEventListener("click", () => copyExample(button.dataset.copy, button));
  });
  document.querySelector(".copy-base").addEventListener("click", () => {
    writeClipboard(location.origin, "Base URL 已复制");
  });
  document.querySelector("#section-jump")?.addEventListener("change", (event) => {
    document.querySelector(`#${event.target.value}`)?.scrollIntoView({ behavior: "auto", block: "start" });
  });
  bindFormatTabs();
  window.LinkParseFluid.bindPress(document);
  observeSections();
  loadStatus();
}

initializeDocs();
