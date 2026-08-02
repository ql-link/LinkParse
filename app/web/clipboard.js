window.LinkParseClipboard = (() => {
  function legacyCopy(value) {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.setAttribute("aria-hidden", "true");
    Object.assign(textarea.style, {
      height: "1px",
      left: "-9999px",
      opacity: "0",
      position: "fixed",
      top: "0",
      width: "1px",
    });
    document.body.append(textarea);
    textarea.focus({ preventScroll: true });
    textarea.select();
    textarea.setSelectionRange(0, value.length);
    try {
      return document.execCommand("copy");
    } finally {
      textarea.remove();
    }
  }

  async function copyText(value) {
    const text = String(value ?? "");
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch {
        // HTTP origins and restricted browser policies can reject the modern API.
      }
    }
    try {
      return legacyCopy(text);
    } catch {
      return false;
    }
  }

  return { copyText };
})();
