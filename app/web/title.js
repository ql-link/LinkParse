(() => {
  const documentTitle = "LinkParse";
  const applyDocumentTitle = () => {
    if (document.title !== documentTitle) document.title = documentTitle;
  };

  applyDocumentTitle();
  window.addEventListener("pageshow", applyDocumentTitle);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) applyDocumentTitle();
  });
})();
