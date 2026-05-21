// 共通テーマ管理（index.html / index2.html で共用）
// - デフォルト: ライト
// - localStorage キー: "theme"
// - body に "light-mode" / "dark-mode" の両方を同時管理
//   → どちらの CSS（body.light-mode / body.dark-mode）でも動作する

(function () {
  function applyTheme(mode) {
    const onCls = mode === "dark" ? "dark-mode" : "light-mode";
    const offCls = mode === "dark" ? "light-mode" : "dark-mode";

    // <html> 要素にも付与（FOUC 防止のため、head 内 inline script と整合させる）
    document.documentElement.classList.add(onCls);
    document.documentElement.classList.remove(offCls);

    // <body> にも従来通り（既存 CSS セレクタ互換）
    if (document.body) {
      document.body.classList.add(onCls);
      document.body.classList.remove(offCls);
    }

    const btn = document.getElementById("themeToggle");
    if (btn) {
      btn.textContent = mode === "dark" ? "ライトモード" : "ダークモード";
    }
  }

  function initTheme() {
    const saved = localStorage.getItem("theme");
    // デフォルト: ライト（kyohei さん指示 2026-05-07）
    const initial = saved === "dark" ? "dark" : "light";
    applyTheme(initial);

    const btn = document.getElementById("themeToggle");
    if (btn) {
      btn.addEventListener("click", function () {
        const isDark = document.body.classList.contains("dark-mode");
        const next = isDark ? "light" : "dark";
        applyTheme(next);
        localStorage.setItem("theme", next);
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTheme);
  } else {
    initTheme();
  }
})();
