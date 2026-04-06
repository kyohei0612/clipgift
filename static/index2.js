// @ts-nocheck

console.log("🚀 index2.js 読み込み開始！");

// --- 要素取得 ---

const video = document.getElementById("videoPlayer");
const modalOverlay = document.getElementById("modalOverlay");
const modalCloseBtn = document.getElementById("modalCloseBtn");
const toggleBtn = document.getElementById("themeToggle");

// 🎨 ダークモードの初期化処理
const savedTheme = localStorage.getItem("theme");
const prefersDark =
  window.matchMedia &&
  window.matchMedia("(prefers-color-scheme: dark)").matches;

if (savedTheme === "dark" || (!savedTheme && prefersDark)) {
  document.body.classList.add("dark-mode");
  console.log("🌙 ダークモードを適用（初期化）");
} else {
  console.log("🌞 ライトモードを適用（初期化）");
}

// 🌗 テーマ切り替えボタンのイベント
if (toggleBtn) {
  toggleBtn.addEventListener("click", () => {
    const isDark = document.body.classList.toggle("dark-mode"); // トグルして状態を取得
    localStorage.setItem("theme", isDark ? "dark" : "light");
    console.log(
      `🌗 テーマを${isDark ? "ダーク" : "ライト"}モードに切り替えました`,
    );
  });
} else {
  console.warn("⚠️ テーマ切替ボタン（toggleBtn）が見つかりませんでした");
}

// 🔌 Socket.IOクライアント初期化
const socket = io();

socket.on("new_clip", (clip) => {
  console.log("新しいクリップを受信", clip);
  const id = Date.now() + Math.random();

  const startParts = clip.start.split(":").map(Number);
  const endParts = clip.end.split(":").map(Number);

  const startSec = startParts[0] * 60 + startParts[1];
  const endSec = endParts[0] * 60 + endParts[1];

  const fullClip = {
    id,
    title: clip.title || "", // サーバーから来たtitleを優先
    start: clip.start,
    end: clip.end,
    startSec,
    endSec,
  };

  if (!clips.some((c) => c.startSec === startSec && c.endSec === endSec)) {
    clips.push(fullClip);
    renderClipsList();
  }
});

toggleBtn.addEventListener("click", () => {
  document.body.classList.toggle("dark-mode");
  const isDark = document.body.classList.contains("dark-mode");
  console.log("🌗 ダークモード切り替え:", isDark);
  localStorage.setItem("theme", isDark ? "dark" : "light");
});

let chatPath = "",
  videoPath = "",
  currentClipStart = 0,
  currentClipEnd = 0,
  currentClip = null,
  clips = [],
  maxSec = 0,
  dragTarget = null,
  videoPlayer,
  videoElement = document.getElementById("videoPlayer"),
  cancelBtn,
  sliderContainer,
  endTimeDisplay,
  track,
  startHandle,
  analyzeBtn = null,
  endHandle,
  queueList,
  downloadCancelled = false,
  downloadBtn,
  isDownloading = false,
  copiedVideoPath = "",
  copiedChatPath = "",
  currentClipElement = null,
  clipStatus = {},
  polling = false,
  queue = [],
  progressBar,
  cancelledClipIds = new Set(),
  completedIds = [], // 完了順序保持用（配列）
  completedSet = new Set(), // 完了判定用（Set）
  peaksInstance = null;

// 必要であればここで関数呼び出しもOK
console.log("✅ DOMContentLoaded後の初期化完了！");

//初期状態でキャンセルボタンを無効化
if (cancelBtn) {
  cancelBtn.disabled = true; // 初期無効
}

if (cancelBtn) {
  cancelBtn.addEventListener("click", () => {
    cancelDownload(); // キャンセル処理
    cancelBtn.disabled = true; // 押したら無効
  });
}

// ★ この位置でOK（index2.jsの冒頭付近に）
function parseTimeToSeconds(timeStr) {
  if (!timeStr || typeof timeStr !== "string") return 0;
  const parts = timeStr.split(":").map((p) => parseInt(p, 10));
  if (parts.length === 2) {
    const [min, sec] = parts;
    return min * 60 + sec;
  } else if (parts.length === 3) {
    const [hr, min, sec] = parts;
    return hr * 3600 + min * 60 + sec;
  } else {
    return 0;
  }
}

function timeStrToSec(str) {
  const [min, sec] = str.split(":").map(Number);
  return min * 60 + sec;
}

function showClipResults(newClips) {
  const clipListElement = document.getElementById("clipList");
  clipListElement.innerHTML = "";

  if (!newClips || newClips.length === 0) {
    const li = document.createElement("li");
    li.textContent = "🙅‍♀️ 該当クリップはありません";
    clipListElement.appendChild(li);
    return;
  }

  newClips.forEach((clip) => {
    if (!clip.id) {
      clip.id = Date.now() + Math.random(); // ✅ 一意なIDを生成
    }
    if (!clips.some((c) => c.id === clip.id)) {
      clips.push(clip);
    }
  });
  renderClipsList();
}

function openDetailModal(clip) {
  const modal = document.getElementById("detailModal");
  const logText = document.getElementById("detailLogText");

  const logs = clip.hitLogs || ["（ログがありません）"];
  logText.textContent = logs.join("\n");

  modal.style.display = "flex";
}

function checkReady() {
  analyzeBtn.disabled = !(videoPath && chatPath);
}

async function fetchWithRetry(url, options = {}, retries = 2, backoff = 2000) {
  for (let i = 0; i <= retries; i++) {
    try {
      const response = await fetch(url, options);
      if (!response.ok)
        throw new Error(`HTTP error! status: ${response.status}`);
      return response;
    } catch (error) {
      if (i === retries) throw error;
      await new Promise((resolve) => setTimeout(resolve, backoff * 2 ** i));
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  console.log("✅ DOMContentLoaded: 初期化開始");

  // --- 共通の要素取得 ---
  analyzeBtn = document.getElementById("analyzeBtn");
  videoPlayer = document.getElementById("videoPlayer");
  queueList = document.getElementById("queueList");
  startDownloadBtn = document.getElementById("startDownload");
  cancelDownloadBtn = document.getElementById("cancelDownload");

  const videoFile = document.getElementById("videoFile");
  const chatFile = document.getElementById("chatFile");
  const videoInfo = document.getElementById("videoInfo");
  const chatInfo = document.getElementById("chatInfo");
  const cancelModalBtn = document.getElementById("cancelModalBtn");
  const queueAddBtn = document.getElementById("queueAddBtn");
  const clipListContainer = document.getElementById("clipList");
  const progressBarElem = document.querySelector(".progress-bar-inner");
  progressText = document.getElementById("progressText");

  startHandle = document.getElementById("startHandle");
  endHandle = document.getElementById("endHandle");
  sliderContainer = document.getElementById("rangeSliderContainer");
  endTimeDisplay = document.getElementById("endTimeDisplay");
  track = document.getElementById("rangeSliderTrack");

  // --- 要素存在チェック ---
  if (
    !startHandle ||
    !endHandle ||
    !analyzeBtn ||
    !videoFile ||
    !chatFile ||
    !videoInfo ||
    !chatInfo ||
    !queueList ||
    !startDownloadBtn ||
    !cancelDownloadBtn
  ) {
    console.warn("⚠️ 初期化時に必要なDOM要素が一部見つかりません");
    return;
  }

  console.log("✅ 要素取得完了");

  // --- ドラッグイベント ---
  [startHandle, endHandle].forEach((handle) => {
    handle.addEventListener("mousedown", onDragStart);
    handle.addEventListener("touchstart", onDragStart, { passive: false });
  });

  // --- 解析ボタン ---
  analyzeBtn.addEventListener("click", () => {
    console.log("🔍 解析ボタンがクリックされました");

    const chatFileObj = chatFile.files[0];
    const keywords = document.getElementById("keywordsInput")?.value.trim();
    const startThreshold = document.getElementById("start_threshold")?.value;
    const endThreshold = document.getElementById("end_threshold")?.value;

    if (!chatFileObj) {
      alert("チャットファイルを選択してください！");
      return;
    }
    if (!keywords) {
      alert("キーワードを入力してください！");
      return;
    }

    const formData = new FormData();
    formData.append("chatFile", chatFile.files[0]);
    formData.append("keywords", keywords);
    formData.append("start_threshold", startThreshold);
    formData.append("end_threshold", endThreshold);
    formData.append("clip_offset", 30);

    const videoFileObj = videoFile.files[0];
    if (!videoFileObj) {
      alert("動画ファイルを選択してください！");
      return;
    }

    const tempVideo = document.createElement("video");
    tempVideo.src = URL.createObjectURL(videoFileObj);

    tempVideo.onloadedmetadata = () => {
      const duration = Math.floor(tempVideo.duration);
      console.log("動画長さ（秒）:", duration);

      formData.append("videoDuration", duration);

      fetch("/analyze_chat_csv", {
        method: "POST",
        body: formData,
      })
        .then((res) => {
          if (!res.ok) throw new Error("サーバー応答エラー");
          return res.json();
        })
        .then((data) => {
          console.log("✅ 解析結果を受信:", data);
          clips.length = 0;

          data.clips.forEach((clip, i) => {
            clip.id = `clip_${i}`;
            clip.title = clip.title ?? "";
            clip.startSec =
              typeof clip.start === "number"
                ? clip.start
                : parseTimeToSeconds(clip.start);
            clip.endSec =
              typeof clip.end === "number"
                ? clip.end
                : parseTimeToSeconds(clip.end);
            clips.push(clip);
          });

          if (data.success && data.clips) {
            renderClipsList();
          } else {
            alert("解析結果が空です。");
          }
        })
        .catch((err) => {
          console.error("❌ 解析リクエスト失敗:", err);
          alert("解析に失敗しました。もう一度お試しください。");
        });
    };
  });

  // --- 動画ファイル選択 ---
  // --- 動画ファイル選択 ---
  videoFile.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) {
      videoPath = "";
      videoInfo.textContent = "未選択";
      videoInfo.title = "";
      videoPlayer.removeAttribute("src");
      console.log("⚠️ 動画ファイルが未選択");
    } else {
      videoPath = file;
      videoInfo.textContent = formatFileName(file.name);
      videoInfo.title = file.name;
      console.log("🎞️ 動画ファイル選択:", file.name);

      // 🎬 動画をセット（プレビュー用）
      videoPlayer.src = URL.createObjectURL(file);

      // 📊 JSONファイル名だけ記録しておく（まだ attachWaveform は呼ばない）
      const jsonFileName = file.name.replace(/\.[^/.]+$/, ".json");
      videoPath.jsonCandidate = `/videos/${jsonFileName}`;
      console.log("📊 JSON候補:", videoPath.jsonCandidate);
    }
    checkReady();
  });

  // --- チャットファイル選択 ---
  chatFile.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) {
      chatPath = "";
      chatInfo.textContent = "未選択";
      chatInfo.title = "";
      console.log("⚠️ チャットファイルが未選択");
    } else {
      chatPath = file;
      chatInfo.textContent = formatFileName(file.name);
      chatInfo.title = file.name;
      console.log("💬 チャットファイル選択:", file.name);
    }
    checkReady();
  });

  // --- キュー追加ボタン ---
  if (queueAddBtn) {
    queueAddBtn.addEventListener("click", () => {
      if (isDownloading) {
        console.warn("⚠️ ダウンロード中のためキュー追加できません");
        return;
      }

      console.log("queue変数はこれ:", queue);
      console.log("📥 キュー追加ボタンが押されました");
      const startText = document.getElementById("startTimeDisplay").textContent;
      const endText = document.getElementById("endTimeDisplay").textContent;
      const startSec = parseTimeToSeconds(startText);
      const endSec = parseTimeToSeconds(endText);
      console.log(`➡️ currentClipStart=${startSec}, currentClipEnd=${endSec}`);
      cancelBtn = document.getElementById("cancelDownload");

      if (currentClipId == null) {
        console.warn("❗ currentClipIdが未設定です");
        return;
      }

      const clip = clips.find((c) => c.id === currentClipId);
      if (!clip) {
        console.warn("❗ 該当するクリップが見つかりません");
        return;
      }

      clip.title = modalTitleInput?.value.trim() || "";

      if (!clip.title) {
        alert("⚠️ タイトルを入力してください！");
        return;
      }

      clip.startSec = startSec;
      clip.endSec = endSec;

      if (!queue.some((c) => c.id === clip.id)) {
        queue.push({
          id: `clip_${Date.now()}_${Math.floor(Math.random() * 1000)}`, // ← 毎回新しいIDにする
          title: clip.title ?? "",
          startSec: clip.startSec, // ←これでOK
          endSec: clip.endSec, // ←これでOK
        });
        console.log("✅ キューに追加:", queue);
      }

      renderQueueList();
      closeModal();
    });
  }

  if (cancelModalBtn) {
    cancelModalBtn.addEventListener("click", closeModal);
  } else {
    console.warn("❗ キャンセルボタンが見つかりません");
  }



  // ダウンロード中にボタンを無効化
  if (downloadBtn) {
    downloadBtn.addEventListener("click", () => {
      console.log("⬇️ ダウンロード開始ボタンクリック");

      if (cancelBtn) {
        cancelBtn.disabled = true;
        console.log("✅ キャンセルボタンを無効化");
      } else {
        console.warn("⚠️ cancelBtnが取得できていません");
      }

      startDownload();
      console.log("⬇️ ダウンロード開始処理を呼び出しました");

      isDownloading = true;

      if (analyzeBtn) {
        analyzeBtn.disabled = true;
        console.log("✅ 解析ボタンを無効化");
      } else {
        console.warn("⚠️ analyzeBtnが取得できていません");
      }

      if (queueAddBtn) {
        queueAddBtn.disabled = true;
        console.log("✅ キュー追加ボタンを無効化");
      } else {
        console.warn("⚠️ queueAddBtnが取得できていません");
      }
    });
  }

  // ダウンロード完了後にボタンを有効化する関数
  function onDownloadComplete() {
    console.log("✅ ダウンロード完了");
    isDownloading = false;

    // キャンセルボタンを無効化
    if (cancelBtn) cancelBtn.disabled = true;

    // スタートボタンを元に戻す
    startDownloadBtn.textContent = "⬇️ 一括生成開始";
    startDownloadBtn.disabled = false;

    // 分析ボタンを有効化
    if (analyzeBtn) analyzeBtn.disabled = false;

    // ダウンロード完了時にタイトルを再チェック
    if (queueAddBtn) {
      if (modalTitleInput?.value.trim()) {
        queueAddBtn.disabled = false;
      } else {
        queueAddBtn.disabled = true;
      }
    }

    // ステータス表示をリセット
    const statusLabel = document.getElementById("downloadStatusLabel");
    if (statusLabel) {
      statusLabel.textContent = "";
    }

    // 削除ボタンを有効化
    const deleteButtons = document.querySelectorAll(".deleteBtn");
    deleteButtons.forEach((btn) => {
      btn.disabled = false;
    });
  }

  // モーダルのタイトル入力監視で「キューに追加」ボタンの有効/無効を切り替え
  if (modalTitleInput) {
    modalTitleInput.addEventListener("input", () => {
      if (queueAddBtn) {
        if (isDownloading) {
          queueAddBtn.disabled = true;
        } else if (modalTitleInput.value.trim()) {
          queueAddBtn.disabled = false;
        } else {
          queueAddBtn.disabled = true;
        }
      }
    });
  }

  // --- 一括生成開始ボタン ---
  // グローバル変数としてキャンセルされたクリップIDセットを用意
  let cancelledClipIds = new Set();

  cancelDownloadBtn.addEventListener("click", async () => {
    console.log("キャンセルボタンが押されました");
    downloadCancelled = true;

    try {
      const res = await fetch("/cancel_process", { method: "POST" });
      const data = await res.json();
      if (data.success) {
        console.log("キャンセルリクエスト成功:", data.message);
      } else {
        console.warn("キャンセルリクエスト失敗:", data.message);
      }
    } catch (err) {
      console.error("キャンセルリクエストエラー:", err);
    }

    // キャンセルされたクリップIDをキュー中で記憶
    // ここはcurrentClipIndexを管理している場合はそれを利用してもいいですが
    // 一応、未完了のクリップすべてをキャンセル対象に登録します
    queue.forEach((clip) => {
      if (!completedSet.has(clip.id)) {
        cancelledClipIds.add(clip.id);
      }
    });

    // UIを未処理状態に戻す
    queue.forEach((clip) => {
      if (!completedSet.has(clip.id)) {
        const li = document.getElementById(`queue-item-${clip.id}`);
        if (li) {
          li.innerHTML = `
          <div class="status">⏸️ 未処理</div>
          <div><strong>${clip.title}</strong></div>
          <div>${formatTime(clip.startSec)} ～ ${formatTime(
            clip.endSec,
          )} の時間が取れました</div>
        `;
        }
      }
    });

    // ボタンの状態を戻す
    cancelDownloadBtn.style.display = "none";
    startDownloadBtn.disabled = false;
  });

  deleteCancelledBtn.addEventListener("click", () => {
    // キャンセルしたクリップだけキューから除外する
    queue = queue.filter((clip) => !cancelledClipIds.has(clip.id));

    // キャンセルリストをクリア
    cancelledClipIds.clear();

    // キューリストを再描画
    renderQueueList();

    console.log("キャンセルしたクリップをキューから削除しました");
  });

  startDownloadBtn.addEventListener("click", async () => {
    console.log("🚀 一括生成ボタンがクリックされました");
    if (!videoFile.files[0] || !chatFile.files[0]) {
      alert("動画ファイルとチャットCSVを選択してください😊");
      return;
    }

    downloadCancelled = false;

    startDownloadBtn.disabled = true;
    cancelDownloadBtn.style.display = "inline-block";

    // 未完了のクリップのみ対象
    const pendingClips = queue.filter((clip) => !completedSet.has(clip.id));

    // 完了済みはUIだけ更新
    queue.forEach((clip) => {
      if (completedSet.has(clip.id)) {
        const li = document.getElementById(`queue-item-${clip.id}`);
        if (li) {
          li.innerHTML = `
      <div class="status done">✅ すでに完了済み</div>
      <div><strong>${clip.title}</strong></div>
      <div>${formatTime(clip.startSec)} ～ ${formatTime(clip.endSec)} の時間が取れました</div>
      `;
        }
      }
    });

    if (pendingClips.length === 0) {
      cancelDownloadBtn.style.display = "none";
      startDownloadBtn.disabled = false;
      return;
    }

    // 未完了クリップを「待機中」表示に初期化
    pendingClips.forEach((clip, i) => {
      const li = document.getElementById(`queue-item-${clip.id}`);
      if (!li) return;
      li.innerHTML = `
      <div class="status">⏳ 待機中 (${i + 1}/${pendingClips.length})</div>
      <div><strong>${clip.title}</strong></div>
      <div>${formatTime(clip.startSec)} ～ ${formatTime(clip.endSec)} の時間が取れました</div>
      <div class="progress-bar"><div class="progress-bar-inner"></div></div>
      <div class="progress-text">0%</div>
    `;
    });

    try {
      // 全クリップを1回のPOSTで送信
      const formData = new FormData();
      formData.append("video", videoFile.files[0]);
      formData.append("chat", chatFile.files[0]);
      formData.append(
        "clips",
        JSON.stringify(
          pendingClips.map((clip) => ({
            start: clip.startSec,
            end: clip.endSec,
            title: clip.title,
          }))
        )
      );

      const res = await fetchWithRetry("/process_clips", {
        method: "POST",
        body: formData,
      });

      if (!res.ok) throw new Error(await res.text());

      const data = await res.json();
      console.log("✅ process_clips応答:", data);

      if (!data.success) throw new Error(data.message || "処理の開始に失敗しました");

      const progressPath = data.progress_path;
      if (!progressPath) throw new Error("進捗パスが取得できません");

      let lastClipIdx = -1; // 前回ポーリング時のcurrent_clip（-1=未開始）

      // ポーリングループ
      while (!downloadCancelled) {
        await new Promise((r) => setTimeout(r, 500));

        const pRes = await fetchWithRetry(
          `/progress?path=${encodeURIComponent(progressPath)}&t=${Date.now()}`
        );
        if (!pRes.ok) throw new Error("進捗取得に失敗しました");
        const pData = await pRes.json();
        console.log("📘 進捗ポーリング:", pData);

        // キャンセルチェック（最優先）
        if (downloadCancelled) {
          pendingClips.forEach((clip) => {
            if (!completedSet.has(clip.id)) {
              const li = document.getElementById(`queue-item-${clip.id}`);
              if (li) {
                li.innerHTML = `
                <div class="status" style="color: orange;">⏸️ キャンセルされました</div>
                <div><strong>${clip.title}</strong></div>
                <div>${formatTime(clip.startSec)} ～ ${formatTime(clip.endSec)} の時間が取れました</div>
                `;
              }
            }
          });
          break;
        }

        // エラーチェック
        if (pData.progress < 0) {
          throw new Error(pData.message || "処理中にエラーが発生しました");
        }

        const rawIdx = pData.current_clip; // 1-indexed、0は未開始
        const currentIdx = rawIdx > 0 ? rawIdx - 1 : 0; // 0-indexed
        const percent = Math.min(Math.max(pData.progress, 0), 100);

        // current_clipが変わったら前のクリップを完了UIに、新しいクリップを0%にリセット
        if (currentIdx !== lastClipIdx) {
          // lastClipIdx以前を完了扱いに（飛び番対応）
          for (let i = Math.max(lastClipIdx, 0); i < currentIdx; i++) {
            const clip = pendingClips[i];
            if (!clip) continue;
            if (!completedSet.has(clip.id)) {
              completedIds.push(clip.id);
              completedSet.add(clip.id);
            }
            const li = document.getElementById(`queue-item-${clip.id}`);
            if (li) {
              li.innerHTML = `
              <div class="status done">✅ 完了</div>
              <div><strong>${clip.title}</strong></div>
              <div>${formatTime(clip.startSec)} ～ ${formatTime(clip.endSec)} の時間が取れました</div>
              `;
            }
          }
          // 新しいクリップを0%でリセット
          if (currentIdx < pendingClips.length) {
            const newClip = pendingClips[currentIdx];
            const newLi = document.getElementById(`queue-item-${newClip.id}`);
            if (newLi) {
              newLi.innerHTML = `
              <div class="status">⬇️ 処理中 (${currentIdx + 1}/${pendingClips.length})</div>
              <div><strong>${newClip.title}</strong></div>
              <div>${formatTime(newClip.startSec)} ～ ${formatTime(newClip.endSec)} の時間が取れました</div>
              <div class="progress-bar"><div class="progress-bar-inner" style="width:0%"></div></div>
              <div class="progress-text">0%</div>
              `;
            }
          }
          lastClipIdx = currentIdx;
        }

        // 現在処理中のクリップのUIを更新
        if (currentIdx >= 0 && currentIdx < pendingClips.length) {
          const clip = pendingClips[currentIdx];
          const li = document.getElementById(`queue-item-${clip.id}`);
          if (li) {
            const bar = li.querySelector(".progress-bar-inner");
            const text = li.querySelector(".progress-text");
            const status = li.querySelector(".status");
            if (bar) bar.style.width = `${percent}%`;
            if (text) text.textContent = `${pData.message || ""} (${percent}%)`;
            if (status) status.textContent = `⬇️ 処理中 (${currentIdx + 1}/${pendingClips.length})`;
          }
        }

        // 全体完了
        if (pData.all_done) {
          pendingClips.forEach((clip) => {
            if (!completedSet.has(clip.id)) {
              completedIds.push(clip.id);
              completedSet.add(clip.id);
            }
            const li = document.getElementById(`queue-item-${clip.id}`);
            if (li) {
              li.innerHTML = `
              <div class="status done">✅ 完了</div>
              <div><strong>${clip.title}</strong></div>
              <div>${formatTime(clip.startSec)} ～ ${formatTime(clip.endSec)} の時間が取れました</div>
              `;
            }
          });
          break;
        }
      }
    } catch (e) {
      console.error(e);
      // 未完了のクリップを失敗表示
      queue.forEach((clip) => {
        if (!completedSet.has(clip.id)) {
          const li = document.getElementById(`queue-item-${clip.id}`);
          if (li) {
            li.innerHTML = `
            <div class="status" style="color:red;">❌ 失敗: ${e.message}</div>
            <div><strong>${clip.title}</strong></div>
            <div>${formatTime(clip.startSec)} ～ ${formatTime(clip.endSec)} の時間が取れました</div>
            `;
          }
        }
      });
    }

    if (!downloadCancelled) {
      cancelDownloadBtn.style.display = "none";
      startDownloadBtn.disabled = false;
    }
  });
});

// --- 各種関数 ---

function formatFileName(name, front = 5) {
  const ext = name.split(".").pop();
  const base = name.replace(/\.[^/.]+$/, "");
  if (base.length <= front + 3) return name;
  return base.slice(0, front) + "…" + "." + ext;
}

function formatTime(sec) {
  sec = Math.floor(sec); // 小数切り捨て
  const hours = Math.floor(sec / 3600);
  const minutes = Math.floor((sec % 3600) / 60);
  const seconds = sec % 60;

  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, "0")}:${seconds
      .toString()
      .padStart(2, "0")}`;
  } else {
    return `${minutes}:${seconds.toString().padStart(2, "0")}`;
  }
}

function updateSliderUI() {
  if (
    typeof sliderMin !== "number" ||
    typeof sliderMax !== "number" ||
    sliderMin >= sliderMax
  ) {
    console.warn("⚠️ sliderMin または sliderMax が無効です", {
      sliderMin,
      sliderMax,
    });
    return;
  }

  if (typeof clipStartSec !== "number" || typeof clipEndSec !== "number") {
    console.warn("⚠️ clipStartSec または clipEndSec が未定義です");
    return;
  }

  let adjustedEndSec = clipEndSec;
  if (clipEndSec >= sliderMax) {
    adjustedEndSec = Math.max(sliderMax - 1, sliderMin);
  }

  // バッファー込みの範囲でパーセンテージを計算
  const totalRange = sliderMax - sliderMin;
  const startPercent = ((clipStartSec - sliderMin) / totalRange) * 100;
  const endPercent = ((adjustedEndSec - sliderMin) / totalRange) * 100;

  // ⛑️ DOM要素チェック（なければ即return）
  const trackEl = track || document.getElementById("rangeSliderTrack");
  const rangeEl = document.getElementById("rangeSliderRange");

  if (!startHandle || !endHandle || !trackEl || !rangeEl) {
    console.warn("⚠️ 必要な要素が見つかりません", {
      startHandle,
      endHandle,
      trackEl,
      rangeEl,
    });
    return;
  }

  // 🔧 ハンドルと帯の位置更新
  startHandle.style.left = `${startPercent}%`;
  endHandle.style.left = `${endPercent}%`;
  trackEl.style.left = `${startPercent.toFixed(2)}%`;
  trackEl.style.width = `${(endPercent - startPercent).toFixed(2)}%`;
  rangeEl.style.left = `${startPercent.toFixed(2)}%`;
  rangeEl.style.width = `${(endPercent - startPercent).toFixed(2)}%`;

  // ここに追加
  if (endPercent >= 99.9) {
    endHandle.style.transform = "translateX(-100%)";
  } else {
    endHandle.style.transform = "translateX(-50%)";
  }

  // ⏱️ 時間表示の更新
  if (startTimeDisplay && endTimeDisplay) {
    startTimeDisplay.textContent = formatTime(clipStartSec);
    endTimeDisplay.textContent = formatTime(clipEndSec);
  } else {
    console.warn("⚠️ startTimeDisplay または endTimeDisplay が未定義です");
  }

  // 📋 ログ（最後に一回だけ）
  console.log("🔄 updateSliderUI 実行", {
    clipStartSec,
    clipEndSec,
    sliderMin,
    sliderMax,
    startPercent,
    endPercent,
  });
}

console.log("✅ clips = ", clips);

function resetQueueAddButtonState() {
  if (queueAddBtn) {
    if (isDownloading) {
      queueAddBtn.disabled = true;
      return;
    }
    if (modalTitleInput?.value.trim()) {
      queueAddBtn.disabled = false;
    } else {
      queueAddBtn.disabled = true;
    }
  }
}

function renderClipsList() {
  const clipsList = document.getElementById("clipList");
  clipsList.innerHTML = "";

  clips.forEach((clip) => {
    if (!clip || !clip.id) {
      console.warn("🚫 無効なclipがスキップされました:", clip);
      return;
    }

    // ⏱️ start / end を秒に変換（数値・文字列どちらも対応）
    clip.startSec =
      typeof clip.start === "number"
        ? clip.start
        : parseTimeToSeconds(String(clip.start || "0").trim());

    clip.endSec =
      typeof clip.end === "number"
        ? clip.end
        : parseTimeToSeconds(String(clip.end || "0").trim());

    // 不正値（NaNや負数）の場合は0で初期化
    if (isNaN(clip.startSec) || clip.startSec < 0) clip.startSec = 0;
    if (isNaN(clip.endSec) || clip.endSec < 0) clip.endSec = clip.startSec;

    // endSec が startSec より小さい場合は同じ位置にする
    if (clip.endSec < clip.startSec) clip.endSec = clip.startSec;

    const li = document.createElement("li");
    li.className = "clipBox";
    li.textContent = `クリップを再生する: ${formatTime(
      clip.startSec,
    )} ～ ${formatTime(clip.endSec)}`;

    li.addEventListener("click", (e) => {
      currentClip = clip;

      if (!clip || !clip.id) {
        console.warn("❗ 無効なclipクリック:", clip);
        return;
      }

      // 古い選択クラスを外す
      document.querySelectorAll(".clipBox.selected").forEach((el) => {
        el.classList.remove("selected");
      });

      // 現在の要素をセット&選択クラス
      currentClipElement = e.currentTarget;
      currentClipElement.classList.add("selected");
      currentClipId = clip.id;

      // 🎬 モーダルを開く
      openModal(clip.id, e);

      const video = document.getElementById("videoPlayer");
      if (!video) {
        console.warn("🎥 video要素が見つかりません");
        return;
      }

      // 秒数で再生位置を指定
      video.pause();
      video.currentTime = clip.startSec;

      setTimeout(() => {
        video.muted = true;
        video
          .play()
          .then(() => {
            setTimeout(() => {
              video.muted = false;
            }, 200);
          })
          .catch((e) => {
            console.warn("🎥 再生失敗:", e);
          });

        const interval = setInterval(() => {
          if (video.currentTime >= clip.endSec || video.paused) {
            video.pause();
            clearInterval(interval);
          }
        }, 500);
      }, 500);

      if (!video.hasClickHandler) {
        video.addEventListener("click", () => {
          if (video.paused) {
            video.play();
          } else {
            video.pause();
          }
        });
        video.hasClickHandler = true;
      }
    });

    clipsList.appendChild(li);
  });
}

function closeModal() {
  const modal = document.getElementById("modalOverlay");
  if (modal) {
    modal.style.display = "none";
  } else {
    console.warn("❗ モーダル要素が見つかりません");
  }
}

function renderQueueList() {
  queueList.innerHTML = "";

  queue.forEach((clip, index) => {
    const li = document.createElement("li");
    li.id = `queue-item-${clip.id}`;
    li.dataset.startSec = clip.startSec;
    li.dataset.endSec = clip.endSec;
    li.style.display = "flex";
    li.style.alignItems = "center";
    li.style.justifyContent = "space-between";
    li.style.padding = "6px 10px";
    li.style.borderBottom = "1px solid #ddd";
    li.style.minHeight = "40px";
    li.style.gap = "10px";

    // タイトルと時間をまとめたラッパー
    const infoWrapper = document.createElement("div");
    infoWrapper.className = "infoWrapper";
    infoWrapper.style.flexGrow = "1";
    infoWrapper.style.display = "flex";
    infoWrapper.style.flexDirection = "column";
    infoWrapper.style.justifyContent = "center";
    infoWrapper.style.minWidth = "0";
    infoWrapper.style.flexShrink = "1";

    const titleText =
      clip.title.trim() === "" ? "タイトルを入力してください" : clip.title;

    const titleElem = document.createElement("div");
    titleElem.className = "clipTitle";
    titleElem.textContent = titleText;
    titleElem.style.whiteSpace = "nowrap";
    titleElem.style.overflow = "hidden";
    titleElem.style.textOverflow = "ellipsis";
    titleElem.style.color = clip.title.trim() === "" ? "#aaa" : "#444";
    titleElem.style.fontStyle = clip.title.trim() === "" ? "italic" : "normal";
    titleElem.style.fontWeight = "bold";
    titleElem.style.flexShrink = "1";
    titleElem.style.minWidth = "0";

    infoWrapper.appendChild(titleElem);

    const timeElem = document.createElement("div");
    timeElem.className = "clipTime";
    timeElem.textContent = `${formatTime(clip.startSec)} ～ ${formatTime(
      clip.endSec,
    )} の時間が取れました`;
    timeElem.style.marginTop = "2px";
    timeElem.style.fontSize = "0.85em";
    timeElem.style.color = "#666";
    infoWrapper.appendChild(timeElem);

    li.appendChild(infoWrapper);

    // --- 進捗バー ---
    progressBar = document.createElement("div");
    progressBar.className = "progress-bar";
    progressBar.style.width = "100%";
    progressBar.style.height = "8px";
    progressBar.style.background = "#eee";
    progressBar.style.borderRadius = "4px";
    progressBar.style.overflow = "hidden";
    progressBar.style.marginTop = "6px";

    const progressBarInner = document.createElement("div");
    progressBarInner.className = "progress-bar-inner";
    progressBarInner.style.width = "0%";
    progressBarInner.style.height = "100%";
    progressBarInner.style.background = "#4caf50";
    progressBarInner.style.transition = "width 0.3s";

    progressBar.appendChild(progressBarInner);
    infoWrapper.appendChild(progressBar);
    // --- 進捗バーここまで ---

    // 「削除」ボタン
    const deleteBtn = document.createElement("button");
    deleteBtn.textContent = "削除";
    deleteBtn.style.color = "red";
    deleteBtn.style.border = "none";
    deleteBtn.style.background = "transparent";
    deleteBtn.style.cursor = "pointer";
    deleteBtn.style.fontWeight = "bold";
    deleteBtn.style.padding = "4px 8px";
    deleteBtn.style.borderRadius = "4px";
    deleteBtn.style.flexShrink = "0";

    deleteBtn.addEventListener("mouseenter", () => {
      deleteBtn.style.backgroundColor = "#fdd";
    });
    deleteBtn.addEventListener("mouseleave", () => {
      deleteBtn.style.backgroundColor = "transparent";
    });

    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      queue.splice(index, 1);
      renderQueueList();
    });

    li.appendChild(deleteBtn);

    queueList.appendChild(li);
  });
}

console.log("✅ 送信直前のqueue", JSON.stringify(queue));
console.log("✅ 送信直前のwindow.clips", JSON.stringify(window.clips));

function startDownload() {
  console.log("🚀 startDownload()呼び出し", new Date().toISOString());
  console.trace("🚀 startDownload()呼び出しトレース");

  // ダウンロード中フラグチェック
  if (isDownloading) {
    console.warn("⚠️ すでにダウンロード処理中です。多重送信を防止します。");
    return;
  }

  // ここでqueueをwindow.clipsにセット
  window.clips = Array.isArray(queue) ? [...queue] : [];
  console.log("✅ startDownload()内のqueue:", JSON.stringify(queue));
  console.log(
    "✅ startDownload()内のwindow.clips:",
    JSON.stringify(window.clips),
  );

  // キューが空の場合
  if (!window.clips.length) {
    console.warn("⚠️ キューが空です。処理を中断します。");
    alert("キューにクリップが追加されていません！");
    return;
  }

  isDownloading = true;

  // ボタン表示
  startDownloadBtn.textContent = "解析中…";
  startDownloadBtn.disabled = true;

  // 10秒後にキャンセルボタンを有効化
  setTimeout(() => {
    if (cancelBtn) {
      cancelBtn.disabled = false;
      console.log("✅ キャンセルボタンを有効化しました");
    }
  }, 10000);

  // 送信するFormDataを確認
  const clipsJson = JSON.stringify(window.clips);
  console.log("✅ 送信FormData clips JSON:", clipsJson);

  if (!clipsJson || clipsJson === "[]") {
    console.error("❌ 送信するclipsが空です。");
    alert("送信するクリップが設定されていません！");
    resetDownloadUI();
    return;
  }

  // フォームデータ
  const formData = new FormData();
  const videoInput = document.getElementById("videoFile");
  const chatInput = document.getElementById("chatFile");

  if (!videoInput.files[0] || !chatInput.files[0]) {
    console.error("❌ 動画ファイルまたはチャットファイルが未選択です");
    alert("動画ファイルとチャットファイルを選択してください！");
    resetDownloadUI();
    return;
  }

  formData.append("video", videoInput.files[0]);
  formData.append("chat", chatInput.files[0]);
  formData.append("clips", clipsJson);

  fetch("/process_clips", { method: "POST", body: formData })
    .then((res) => {
      if (res.status === 429) {
        console.warn("⚠️ サーバー処理中（429）。UIを戻します。");
        resetDownloadUI();
        return Promise.resolve();
      }
      if (!res.ok) {
        console.error("❌ サーバーから不正応答:", res.status);
        resetDownloadUI();
        return;
      }
      return res.json();
    })
    .then((data) => {
      if (!data) return;
      console.log("✅ サーバー応答:", data);

      if (data.success) {
        console.log("✅ ダウンロード処理を開始します（pollProgress）");
        pollProgress(data.progress_path);
      } else {
        console.error(
          "❌ ダウンロード処理が失敗:",
          data.message || "不明なエラー",
        );
        resetDownloadUI();
      }
    })
    .catch((err) => {
      console.error("❌ ダウンロード処理エラー:", err);
      resetDownloadUI();
    });

  // UIリセット共通処理
  function resetDownloadUI() {
    startDownloadBtn.textContent = "⬇️ 一括生成開始";
    startDownloadBtn.disabled = false;
    isDownloading = false;
  }
}

function pollProgress(progressPath) {
  const interval = setInterval(() => {
    fetch(
      `/get-progress-file?path=${encodeURIComponent(
        progressPath,
      )}&t=${Date.now()}`,
    )
      .then((response) => response.json())
      .then((data) => {
        console.log("📘 [進捗データ]:", data);

        if (data.progress >= 0 && data.message) {
          // 任意で画面表示を更新したい場合ここに書く
        }

        // 🔹全体完了の場合
        if (data.all_done) {
          clearInterval(interval);
          console.log("✅ 全クリップ処理が完了しました");
          onDownloadComplete();
          return;
        }

        // 🔹このクリップだけ完了
        if (data.progress >= 100) {
          console.log("✅ このクリップが完了しました（次の処理を待機）");
          // 全体完了でなければここでは止めない
        }

        // 🔹キャンセルやエラー
        if (data.progress === -1) {
          clearInterval(interval);
          console.error("❌ エラーまたはキャンセル:", data.message);
          onDownloadComplete();
        }
      })
      .catch((err) => {
        console.error("❌ 進捗取得エラー:", err);
        clearInterval(interval);
        onDownloadComplete();
      });
  }, 1000);
}

function formatVideoFileName(fileName) {
  const extIndex = fileName.lastIndexOf(".");
  const ext = fileName.slice(extIndex);
  const namePart = fileName.slice(0, extIndex);

  if (namePart.length > 20) {
    return namePart.slice(0, 7) + "…" + namePart.slice(-7) + ext;
  } else {
    return fileName;
  }
}

function updateClipDurationDisplay() {
  const duration = clipEndSec - clipStartSec;
  const minutes = Math.floor(duration / 60);
  const seconds = Math.floor(duration % 60);

  const displayText =
    minutes > 0
      ? `クリップ長さ：${minutes}分${seconds}秒`
      : `クリップ長さ：${seconds}秒`;

  const clipDurationDisplay = document.getElementById("clipDurationDisplay");
  if (clipDurationDisplay) {
    clipDurationDisplay.textContent = displayText;
  }
}

async function openModal(id, event) {
  const clip = clips.find((c) => c.id === id);
  if (!clip) return;

  currentClipId = id; // モーダル開いたらIDセット

  if (currentClipElement) {
    currentClipElement.classList.remove("highlight");
    currentClipElement = null;
  }

  currentClipElement =
    event?.currentTarget ?? document.getElementById(`clip-${id}`);

  if (clip.title.trim() === "") {
    modalTitleInput.value = "";
    modalTitleInput.placeholder = "ここにタイトルを入力してください";
  } else {
    modalTitleInput.value = clip.title;
    modalTitleInput.placeholder = "";
  }

  // 前回の onloadedmetadata を解除
  videoPlayer.onloadedmetadata = null;

  if (videoPath) {
    videoURL = URL.createObjectURL(videoPath);
    videoPlayer.src = videoURL;
    videoPlayer.muted = false;
  }

  // onloadedmetadata を1回だけセット
  videoPlayer.onloadedmetadata = async () => {
    videoPlayer.onloadedmetadata = null;

    maxSec = Math.floor(videoPlayer.duration);
    const bufferSec = 600; // 10分バッファ

    const playStartSec = Math.max(clip.startSec - bufferSec, 0);
    let playEndSec = Math.min(clip.endSec + bufferSec, maxSec);

    if (playEndSec <= playStartSec) {
      playEndSec = Math.min(playStartSec + 10, maxSec);
    }

    sliderMin = playStartSec;
    sliderMax = playEndSec;

    clipStartSec = Math.max(sliderMin, Math.min(clip.startSec, sliderMax - 1));
    clipEndSec = Math.max(sliderMin + 1, Math.min(clip.endSec, sliderMax));

    updateSliderUI();
    updateClipDurationDisplay();

    videoPlayer.currentTime = clipStartSec;

    // 🎯 波形をモーダルが可視状態になってから生成する
    setTimeout(async () => {
      if (videoPath?.name) {
        const fileName = videoPath.name; // 例: "Minecraftソロ鯖~〇〇.mp4"
        const folderName = fileName.replace(/\.[^/.]+$/, ""); // "Minecraftソロ鯖~〇〇"
        const jsonUrl = `/downloads/${folderName}/waveform.json`;

        console.log(
          "📊 attachWaveform呼び出し:",
          jsonUrl,
          clipStartSec,
          clipEndSec,
        );
        try {
          await attachWaveform(jsonUrl, clipStartSec, clipEndSec);
        } catch (err) {
          console.error("❌ attachWaveform 失敗:", err);
        }
      }
    }, 100); // ⚠️ 非表示だと幅=0なので少し待つ
  };

  modalOverlay.style.display = "flex";
  resetQueueAddButtonState();
}

// 波形ロジック
// ★ 波形の表示幅（秒）をグローバルで保持
const WAVEFORM_VIEW_DURATION = 30; // 30秒表示

async function attachWaveform(jsonUrl, clipStart, clipEnd) {
  const video = document.getElementById("videoPlayer");
  if (peaksInstance) {
    peaksInstance.destroy();
    document.getElementById("waveform-container").innerHTML = "";
  }

  const options = {
    containers: {
      zoomview: document.getElementById("waveform-container"),
    },
    mediaElement: video,
    dataUri: {
      json: jsonUrl,
    },
    zoomWaveformColor: "#2196f3",
    overviewWaveformColor: "#2196f3",
    zoomLevels: [256, 512, 1024, 2048, 4096],
  };

  return new Promise((resolve, reject) => {
    window.peaks.init(options, (err, instance) => {
      if (err) {
        console.error("❌ Peaks.js 初期化エラー:", err);
        reject(err);
        return;
      }
      peaksInstance = instance;

      // clip範囲を赤帯で表示（IDを付与）
      peaksInstance.segments.add({
        id: "clipSegment",
        startTime: clipStart,
        endTime: clipEnd,
        labelText: "Clip",
        color: "rgba(255, 0, 0, 0.4)",
      });

      // ★ 5分表示でズーム設定
      const zoomview = peaksInstance.views.getView("zoomview");
      if (zoomview) {
        try {
          // 5分（300秒）を表示
          zoomview.setZoom({ seconds: WAVEFORM_VIEW_DURATION });
          // クリップ開始位置を中心に表示（開始の2.5分前から）
          const viewStart = Math.max(clipStart - WAVEFORM_VIEW_DURATION / 2, 0);
          zoomview.setStartTime(viewStart);
          console.log("🔍 波形ビュー: 5分表示, 開始位置:", viewStart);
        } catch (err) {
          console.warn("⚠️ 波形ズーム設定エラー:", err);
        }
      }

      console.log("✅ Peaks.js 波形初期化完了");
      resolve(peaksInstance);
    });
  });
}

function closeModal() {
  // 再描画後でも新しい要素を取得してハイライト
  if (currentClipId) {
    const el = document.getElementById(`clip-${currentClipId}`);
    if (el) {
      // 一旦 .selected を消す
      el.classList.remove("selected");
      // ハイライト付与
      el.classList.add("highlight");
    }
  }

  // 動画を確実に停止
  if (videoPlayer) {
    videoPlayer.pause();
    // Blob URLの開放
    if (videoPlayer.src.startsWith("blob:")) {
      URL.revokeObjectURL(videoPlayer.src);
    }
    videoPlayer.src = "";
  }

  modalOverlay.style.display = "none";

  // 状態リセット
  currentClipElement = null;
  currentClipId = null;
}

// ========================================
// index2.js 修正箇所 - バーの挙動修正
// ========================================
// 以下の3つの関数を置き換えてください
// 場所: 1441行目〜1544行目付近

let isDragging = false;
let originalClipStartSec = 0; // ★ 追加: ドラッグ開始時の元の値を保持
let originalClipEndSec = 0; // ★ 追加

function onDragStart(e) {
  if (!e) {
    console.warn("⚠️ イベントが渡されていません");
    return;
  }

  if (e.cancelable) {
    e.preventDefault();
  }

  const target = e.touches?.[0]?.target || e.target;
  if (!target) {
    console.warn("⚠️ dragTarget が取得できませんでした");
    return;
  }

  dragTarget = target;
  dragTarget.classList.add("dragging");
  isDragging = true;

  // ★ バーを触った瞬間に動画を一時停止
  if (videoPlayer) {
    videoPlayer.pause();
  }

  // ★ 元の位置を保存
  originalClipStartSec = clipStartSec;
  originalClipEndSec = clipEndSec;

  // ★ startHandleを触ったら波形と再生位置をそのバーの位置に移動
  if (dragTarget.id === "startHandle") {
    // Peaks.jsのplayheadを開始位置に移動
    if (peaksInstance && peaksInstance.player) {
      try {
        peaksInstance.player.seek(clipStartSec);
      } catch (err) {
        console.warn("⚠️ player.seekエラー:", err);
      }
    }
    // 波形も開始位置を真ん中に表示
    if (peaksInstance) {
      try {
        const zoomview = peaksInstance.views.getView("zoomview");
        if (zoomview && typeof zoomview.setStartTime === "function") {
          const waveStart = Math.max(
            clipStartSec - WAVEFORM_VIEW_DURATION / 2,
            0,
          );
          zoomview.setStartTime(waveStart);
        }
      } catch (err) {
        console.warn("⚠️ 波形スクロールエラー:", err);
      }
    }
  }

  document.removeEventListener("mousemove", onDragMove);
  document.removeEventListener("mouseup", onDragEnd);
  document.removeEventListener("touchmove", onDragMove);
  document.removeEventListener("touchend", onDragEnd);

  document.addEventListener("mousemove", onDragMove);
  document.addEventListener("mouseup", onDragEnd);
  document.addEventListener("touchmove", onDragMove, { passive: false });
  document.addEventListener("touchend", onDragEnd);

  console.log("🎯 onDragStart:", dragTarget.id);
}

function onDragMove(e) {
  if (!dragTarget) {
    return;
  }

  if (
    typeof sliderMin !== "number" ||
    typeof sliderMax !== "number" ||
    sliderMin >= sliderMax
  ) {
    return;
  }

  const rect = sliderContainer?.getBoundingClientRect?.();
  if (!rect || rect.width === 0) {
    return;
  }

  if (e.cancelable) {
    e.preventDefault();
  }

  const clientX = e.touches?.[0]?.clientX ?? e.clientX;
  let percent = (clientX - rect.left) / rect.width;
  percent = Math.min(Math.max(percent, 0), 1);

  let time = sliderMin + percent * (sliderMax - sliderMin);
  // 0.1秒単位で丸める（より滑らかな動き）
  time = Math.round(time * 10) / 10;

  if (dragTarget.id === "startHandle") {
    clipStartSec = Math.min(time, clipEndSec - 0.5);
    clipStartSec = Math.max(clipStartSec, sliderMin);
    // ★ startHandleドラッグ中は波形のplayheadも連動
    if (peaksInstance && peaksInstance.player) {
      try {
        peaksInstance.player.seek(clipStartSec);
      } catch (err) {}
    }
  } else if (dragTarget.id === "endHandle") {
    clipEndSec = Math.max(time, clipStartSec + 0.5);
    clipEndSec = Math.min(clipEndSec, sliderMax);
  }

  // UIを更新
  updateSliderUI();
  updateClipDurationDisplay();

  // ★ 波形のセグメント（赤帯）をリアルタイム更新
  updateWaveformSegment();

  // ★ バーが波形の表示範囲外に行ったらページ送り
  scrollWaveformIfNeeded(time);
}

function onDragEnd(e) {
  if (!dragTarget) {
    return;
  }

  const handleId = dragTarget.id;

  dragTarget.classList.remove("dragging");
  dragTarget = null;
  isDragging = false;

  document.removeEventListener("mousemove", onDragMove);
  document.removeEventListener("mouseup", onDragEnd);
  document.removeEventListener("touchmove", onDragMove);
  document.removeEventListener("touchend", onDragEnd);

  // ★ startHandleのみ再生位置を変更、endHandleは再生位置に関与しない
  if (videoPlayer) {
    if (handleId === "startHandle") {
      videoPlayer.currentTime = clipStartSec;
      videoPlayer.play();
    } else if (handleId === "endHandle") {
      // endHandleは再生位置を変更せず、そのまま再生継続
      videoPlayer.play();
    }
  }

  console.log(
    "🎯 onDragEnd:",
    handleId,
    "start:",
    clipStartSec,
    "end:",
    clipEndSec,
  );
}

// ★ 波形セグメント更新用の関数
function updateWaveformSegment() {
  if (!peaksInstance) return;

  try {
    const segment = peaksInstance.segments.getSegment("clipSegment");
    if (segment) {
      segment.update({ startTime: clipStartSec, endTime: clipEndSec });
    }
  } catch (err) {
    // フォールバック: 全削除して再追加
    try {
      peaksInstance.segments.removeAll();
      peaksInstance.segments.add({
        id: "clipSegment",
        startTime: clipStartSec,
        endTime: clipEndSec,
        labelText: "Clip",
        color: "rgba(255, 0, 0, 0.4)",
      });
    } catch (e) {
      console.warn("⚠️ 波形セグメント更新エラー:", e);
    }
  }
}

// ★ バーが画面外に行ったら波形をページ送り
function scrollWaveformIfNeeded(currentTime) {
  if (!peaksInstance) return;

  try {
    const zoomview = peaksInstance.views.getView("zoomview");
    if (!zoomview) return;

    const viewStart = zoomview.getStartTime();
    const viewEnd = viewStart + WAVEFORM_VIEW_DURATION;

    // 現在のバー位置が表示範囲外なら移動
    if (currentTime < viewStart) {
      // 左に行き過ぎた → 前のページへ
      const newStart = Math.max(currentTime - WAVEFORM_VIEW_DURATION * 0.1, 0);
      zoomview.setStartTime(newStart);
      console.log("⬅️ 波形を前へ:", newStart);
    } else if (currentTime > viewEnd) {
      // 右に行き過ぎた → 次のページへ
      const newStart = currentTime - WAVEFORM_VIEW_DURATION * 0.9;
      zoomview.setStartTime(newStart);
      console.log("➡️ 波形を次へ:", newStart);
    }
  } catch (err) {
    console.warn("⚠️ 波形スクロールエラー:", err);
  }
}
