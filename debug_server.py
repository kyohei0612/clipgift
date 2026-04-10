"""
debug_server.py - YouTubeクリップツール 自律デバッグAI
使い方: python debug_server.py
"""
import os
import webbrowser
import threading
import time
from flask import Flask, request, jsonify, render_template_string
import requests as _requests
from dotenv import load_dotenv

load_dotenv()  # .envファイルから自動読み込み

app = Flask(__name__)
_results_store = {}  # フェーズごとの解析結果を蓄積
_terminal_logs = []  # ターミナル出力を全キャプチャ
_terminal_lock = threading.Lock()

# printをフックしてターミナルログを蓄積
import sys
import io

class _LogCapture:
    def __init__(self, original):
        self._original = original
    def write(self, msg):
        if msg.strip():
            with _terminal_lock:
                _terminal_logs.append(msg.rstrip())
        self._original.write(msg)
    def flush(self):
        self._original.flush()

sys.stdout = _LogCapture(sys.stdout)
sys.stderr = _LogCapture(sys.stderr)

# ============================================================
# コードサマリー（解析対象アプリの情報）
# ============================================================

# 解析対象ファイル（debug_server.py自身は除外）
TARGET_FILES = [
    "app.py",
    "downloader.py",
    "mp4inchatnagasi.py",
    "auto_update.py",
    "index.html",
    "index2.html",
    "index2.js",
    "index2.css",
]

def build_code_context():
    """testseverフォルダのファイルを読み込んでコードサマリーを生成"""
    base = os.path.abspath(os.path.dirname(__file__))
    parts = []
    for fname in TARGET_FILES:
        fpath = os.path.join(base, fname)
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            # 1ファイルあたり最大3000文字に制限（Ollamaのトークン上限対策）
            if len(content) > 3000:
                content = content[:3000] + "\n... (省略)"
            parts.append(f"=== {fname} ===\n{content}")
        except Exception as e:
            parts.append(f"=== {fname} === (読み込みエラー: {e})")
    result = "\n\n".join(parts)
    print(f"📂 コードコンテキスト生成完了: {len(TARGET_FILES)}ファイル, {len(result)}文字", flush=True)
    return result

# 起動時に一度だけ読み込む
CODE_CONTEXT = build_code_context()


PHASES = [
    {
        "key": "static",
        "label": "静的解析",
        "desc": "コード品質・未使用変数・None チェック漏れ・import の問題",
        "prompt": "静的解析の視点で解析してください。未使用変数、グローバル変数の競合、型の不一致、None/nullチェック漏れ、importの問題、デッドコードなどを重点的に。"
    },
    {
        "key": "security",
        "label": "セキュリティ",
        "desc": "インジェクション・パストラバーサル・認証・外部アクセス検証",
        "prompt": "セキュリティの視点で解析してください。パストラバーサル、コマンドインジェクション、認証・認可の欠如、ファイル操作の危険性、外部URLアクセスの検証、auto_updateの署名検証なしの問題などを重点的に。"
    },
    {
        "key": "logic",
        "label": "ロジック・並行性",
        "desc": "競合状態・スレッドセーフ・メモリリーク・デッドロック",
        "prompt": "ロジック・並行性の視点で解析してください。競合状態(race condition)、スレッドセーフでない操作、メモリリーク、デッドロックリスク、グローバル変数の非同期アクセス、os._exitの問題などを重点的に。"
    },
    {
        "key": "api",
        "label": "API 耐性",
        "desc": "異常入力・境界値・大容量ファイル・DoS 耐性",
        "prompt": "API耐性の視点で解析してください。異常な入力値（空文字、巨大ファイル、不正JSON）への対応、境界値処理、エラー時のレスポンス一貫性、同時リクエスト、DoS耐性、progress_pathの検証などを重点的に。"
    },
    {
        "key": "frontend",
        "label": "フロントエンド・UX",
        "desc": "JS エラーハンドリング・ポーリング問題・進捗表示バグ",
        "prompt": "フロントエンド・UXの視点で解析してください。JSのエラーハンドリング漏れ、ポーリングのメモリリーク・多重起動、進捗表示のバグ、ハートビート停止条件、ユーザーへのフィードバック漏れ、モーダルのz-index問題などを重点的に。"
    },
]

SYSTEM_PROMPT = """あなたはFlask/Pythonアプリの上級デバッグエンジニアです。
与えられたコードサマリーを見て、指定された視点でバグ・リスク・改善点をすべて洗い出してください。

必ず以下のJSON配列だけを出力してください（前置き・後書き・マークダウン不要）:
[
  {
    "severity": "critical|warning|info",
    "category": "カテゴリ名",
    "title": "問題のタイトル（30字以内）",
    "file": "ファイル名:関数名",
    "description": "問題の詳細（2〜3文）",
    "fix": "修正案のコードまたは説明（1〜3行）"
  }
]
JSON配列のみ出力。必ず5件以上見つけること。絶対にJSON以外を出力しないこと。"""


# ============================================================
# HTMLテンプレート
# ============================================================
HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>自律デバッグAI</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', sans-serif; background: #0e0e12; color: #f0f0f4; min-height: 100vh; padding: 32px 20px; }
h1 { font-size: 20px; font-weight: 500; margin-bottom: 6px; }
.sub { font-size: 13px; color: #888; margin-bottom: 24px; }
.api-row { display: flex; gap: 8px; margin-bottom: 20px; }
.api-row input { flex: 1; padding: 8px 12px; background: #1a1a22; border: 1px solid #333; border-radius: 8px; color: #f0f0f4; font-size: 13px; }
.api-row input:focus { outline: none; border-color: #555; }
.metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 20px; }
.metric { background: #16161c; border-radius: 8px; padding: 12px; }
.metric-label { font-size: 11px; color: #666; margin-bottom: 4px; }
.metric-val { font-size: 24px; font-weight: 500; }
.red { color: #e24b4a; } .amber { color: #ef9f27; } .blue { color: #378add; }
.btn { background: #1e3a5f; color: #7ab8f5; border: 1px solid #2a5080; padding: 9px 24px; border-radius: 8px; font-size: 13px; font-weight: 500; cursor: pointer; }
.btn:hover { background: #254870; }
.btn:disabled { opacity: 0.4; cursor: not-allowed; }
.phase-bar { background: #16161c; border-radius: 8px; padding: 12px 16px; margin: 16px 0; display: none; }
.phase-text { font-size: 12px; color: #888; margin-bottom: 8px; }
.track { height: 3px; background: #2a2a35; border-radius: 4px; overflow: hidden; }
.fill { height: 100%; background: #378add; border-radius: 4px; width: 0%; transition: width 0.4s; }
.results { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.divider { font-size: 11px; color: #555; padding: 10px 0 4px; border-top: 1px solid #1e1e28; margin-top: 4px; }
.card { background: #16161c; border: 1px solid #222; border-radius: 10px; padding: 14px 16px; }
.card.critical { border-left: 3px solid #e24b4a; }
.card.warning { border-left: 3px solid #ef9f27; }
.card.info { border-left: 3px solid #378add; }
.card-top { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.badge { font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 4px; }
.badge.critical { background: #3a1515; color: #e24b4a; }
.badge.warning { background: #3a2a10; color: #ef9f27; }
.badge.info { background: #0e2a45; color: #378add; }
.cat { font-size: 11px; color: #555; margin-left: auto; }
.card-title { font-size: 13px; font-weight: 500; margin-bottom: 3px; }
.card-file { font-size: 11px; color: #555; font-family: monospace; margin-bottom: 6px; }
.card-desc { font-size: 12px; color: #aaa; line-height: 1.6; }
.card-fix { font-size: 11px; color: #5dbf8a; margin-top: 8px; background: #0f1f18; padding: 6px 10px; border-radius: 6px; font-family: monospace; white-space: pre-wrap; word-break: break-all; }
.dot { display: inline-block; width: 6px; height: 6px; background: #378add; border-radius: 50%; animation: p 0.8s infinite; margin-left: 6px; vertical-align: middle; }
@keyframes p { 0%,100%{opacity:1}50%{opacity:0.2} }
.empty { text-align: center; padding: 40px; color: #444; font-size: 13px; }
</style>
</head>
<body>
<h1>自律デバッグAI</h1>
<p class="sub">静的解析 / セキュリティ / ロジック / API耐性 / フロントエンド の5軸で自動解析</p>

<div class="api-row" id="apiRow" style="display:none">
  <input type="password" id="apiKey" value="">
</div>

<div class="metrics">
  <div class="metric"><div class="metric-label">重大バグ</div><div class="metric-val red" id="cnt-c">—</div></div>
  <div class="metric"><div class="metric-label">警告</div><div class="metric-val amber" id="cnt-w">—</div></div>
  <div class="metric"><div class="metric-label">情報</div><div class="metric-val blue" id="cnt-i">—</div></div>
  <div class="metric"><div class="metric-label">合計</div><div class="metric-val" id="cnt-t">—</div></div>
</div>

<button class="btn" id="startBtn" onclick="startDebug()">デバッグ開始</button>
<button class="btn" id="copyBtn" onclick="copyMarkdown()" style="display:none;background:#1a3a2a;color:#5dbf8a;border-color:#2a5a3a;margin-left:8px;">Markdownでコピー</button>

<div class="phase-bar" id="phaseBar">
  <div class="phase-text" id="phaseText">解析中...</div>
  <div class="track"><div class="fill" id="fill"></div></div>
</div>

<div class="results" id="results">
  <div class="empty">APIキーを入力して「デバッグ開始」を押してください</div>
</div>

<script>
let allIssues = [];

function updateCounts() {
  const c = allIssues.filter(i => i.severity === 'critical').length;
  const w = allIssues.filter(i => i.severity === 'warning').length;
  const inf = allIssues.filter(i => i.severity === 'info').length;
  document.getElementById('cnt-c').textContent = c;
  document.getElementById('cnt-w').textContent = w;
  document.getElementById('cnt-i').textContent = inf;
  document.getElementById('cnt-t').textContent = allIssues.length;
}

function renderIssue(issue) {
  const sev = (issue.severity || 'info').toLowerCase();
  const d = document.createElement('div');
  d.className = 'card ' + sev;
  d.innerHTML =
    '<div class="card-top">' +
      '<span class="badge ' + sev + '">' + sev.toUpperCase() + '</span>' +
      '<span class="cat">' + esc(issue.category || '') + '</span>' +
    '</div>' +
    '<div class="card-title">' + esc(issue.title || '') + '</div>' +
    '<div class="card-file">' + esc(issue.file || '') + '</div>' +
    '<div class="card-desc">' + esc(issue.description || '') + '</div>' +
    (issue.fix ? '<div class="card-fix">' + esc(issue.fix) + '</div>' : '');
  return d;
}

function copyMarkdown() {
  const c = allIssues.filter(i => i.severity === 'critical').length;
  const w = allIssues.filter(i => i.severity === 'warning').length;
  const inf = allIssues.filter(i => i.severity === 'info').length;
  let md = `# デバッグレポート\n\n`;
  md += `- 重大: ${c}件 / 警告: ${w}件 / 情報: ${inf}件 / 合計: ${allIssues.length}件\n\n---\n\n`;
  const phases = ['静的解析','セキュリティ','ロジック・並行性','API耐性','フロントエンド・UX'];
  let lastPhase = '';
  allIssues.forEach(issue => {
    const cat = issue.category || '';
    if (cat !== lastPhase) {
      md += `## ${cat}\n\n`;
      lastPhase = cat;
    }
    const sev = (issue.severity || 'info').toUpperCase();
    md += `### [${sev}] ${issue.title || ''}\n`;
    md += `**ファイル:** \`${issue.file || ''}\`\n\n`;
    md += `${issue.description || ''}\n\n`;
    if (issue.fix) md += `**修正案:**\n\`\`\`\n${issue.fix}\n\`\`\`\n\n`;
  });
  navigator.clipboard.writeText(md).then(() => {
    const btn = document.getElementById('copyBtn');
    btn.textContent = 'コピーしました！';
    setTimeout(() => btn.textContent = 'Markdownでコピー', 2000);
  });
}


function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function startDebug() {
  const apiKey = 'local';

  const btn = document.getElementById('startBtn');
  btn.disabled = true;
  btn.textContent = '解析中...';
  allIssues = [];
  document.getElementById('results').innerHTML = '';
  document.getElementById('cnt-c').textContent = '—';
  document.getElementById('cnt-w').textContent = '—';
  document.getElementById('cnt-i').textContent = '—';
  document.getElementById('cnt-t').textContent = '—';
  document.getElementById('phaseBar').style.display = 'block';

  const phases = await fetch('/phases').then(r => r.json());

  for (let i = 0; i < phases.length; i++) {
    const phase = phases[i];
    const pct = Math.round(i / phases.length * 100);
    document.getElementById('fill').style.width = pct + '%';
    document.getElementById('phaseText').innerHTML =
      '解析中: ' + phase.label + ' — ' + phase.desc +
      '<span class="dot"></span> (' + (i+1) + '/' + phases.length + ')';

    const divider = document.createElement('div');
    divider.className = 'divider';
    divider.textContent = phase.label + ' — ' + phase.desc;
    document.getElementById('results').appendChild(divider);

    try {
      const resp = await fetch('/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: apiKey, phase_key: phase.key })
      });
      const data = await resp.json();
      if (data.error) {
        const e = document.createElement('div');
        e.style.cssText = 'font-size:12px;color:#e24b4a;padding:8px;';
        e.textContent = 'エラー: ' + data.error;
        document.getElementById('results').appendChild(e);
        continue;
      }
      for (const issue of (data.issues || [])) {
        allIssues.push(issue);
        document.getElementById('results').appendChild(renderIssue(issue));
        updateCounts();
        await new Promise(r => setTimeout(r, 50));
      }
    } catch(e) {
      console.error(e);
    }
  }

  document.getElementById('fill').style.width = '100%';
  document.getElementById('phaseText').innerHTML = '✅ 解析完了';
  btn.disabled = false;
  btn.textContent = '再解析';
  document.getElementById('copyBtn').style.display = 'inline-block';
  updateCounts();

  // 自動でJSONレポートを保存
  try {
    const saveResp = await fetch('/save_report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dir: '' })
    });
    const saveData = await saveResp.json();
    if (saveData.cancelled) {
      // キャンセルされた場合は何もしない
    } else if (saveData.path) {
      const info = document.createElement('div');
      info.style.cssText = 'margin-top:12px;font-size:12px;color:#5dbf8a;background:#0f1f18;padding:8px 12px;border-radius:6px;';
      info.textContent = '📄 レポート保存済み: ' + saveData.path;
      document.getElementById('results').appendChild(info);
    }
  } catch(e) {
    console.error('保存エラー:', e);
  }
}

// サーバー再起動を検知して自動リロード
(function watchServerRestart() {
  let _restarting = false;
  setInterval(() => {
    fetch('/ping')
      .then(() => {
        if (_restarting) {
          console.log('✅ サーバー復帰 → リロード');
          location.reload();
        }
        _restarting = false;
      })
      .catch(() => {
        if (!_restarting) {
          _restarting = true;
          console.log('🔄 サーバー再起動を検知...');
        }
      });
  }, 500);
})();
</script>
</body>
</html>"""


# ============================================================
# API エンドポイント
# ============================================================
@app.route("/ping")
def ping():
    return "ok"

@app.route("/")
def index():
    saved_key = os.environ.get("GEMINI_API_KEY", "")
    return render_template_string(HTML, saved_key=saved_key)


@app.route("/phases")
def get_phases():
    return jsonify([{"key": p["key"], "label": p["label"], "desc": p["desc"]} for p in PHASES])


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json()
    api_key = data.get("api_key", "").strip()
    phase_key = data.get("phase_key", "")

    if not api_key:
        api_key = "local"  # Ollamaはキー不要

    phase = next((p for p in PHASES if p["key"] == phase_key), None)
    if not phase:
        return jsonify({"error": "不明なフェーズ"}), 400

    try:
        prompt = f"{SYSTEM_PROMPT}\n\nコードサマリー:\n{CODE_CONTEXT}\n\n指示:\n{phase['prompt']}"
        resp = _requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3.2",
                "prompt": prompt,
                "stream": False,
            },
            timeout=120,
        )
        raw = resp.json().get("response", "").strip()
        clean = raw.replace("```json", "").replace("```", "").strip()
        issues = __import__("json").loads(clean)
        _results_store.setdefault(phase_key, []).extend(issues)
        return jsonify({"issues": issues})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/save_report", methods=["POST"])
def save_report():
    import json as _json
    from datetime import datetime
    import tkinter as tk
    from tkinter import filedialog

    all_issues = []
    for phase_issues in _results_store.values():
        all_issues.extend(phase_issues)

    with _terminal_lock:
        logs = list(_terminal_logs)

    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "critical": len([i for i in all_issues if i.get("severity") == "critical"]),
            "warning":  len([i for i in all_issues if i.get("severity") == "warning"]),
            "info":     len([i for i in all_issues if i.get("severity") == "info"]),
            "total":    len(all_issues),
        },
        "issues": all_issues,
        "terminal_logs": logs,
    }

    # tkinterのダイアログをメインスレッドで開く
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"debug_report_{timestamp}.json"

    root = tk.Tk()
    root.withdraw()
    root.wm_attributes("-topmost", True)
    filepath = filedialog.asksaveasfilename(
        title="レポートの保存先を選択",
        initialfile=default_name,
        defaultextension=".json",
        filetypes=[("JSONファイル", "*.json"), ("すべてのファイル", "*.*")],
    )
    root.destroy()

    if not filepath:
        return jsonify({"cancelled": True})

    with open(filepath, "w", encoding="utf-8") as f:
        _json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"📄 レポート保存: {filepath}")
    _results_store.clear()
    with _terminal_lock:
        _terminal_logs.clear()

    return jsonify({"path": filepath})


# ============================================================
# 起動
# ============================================================
def _watch_self_restart():
    """debug_server.py自身が更新されたら再起動"""
    self_path = os.path.abspath(__file__)
    last_mtime = os.path.getmtime(self_path)
    while True:
        time.sleep(1)
        try:
            mtime = os.path.getmtime(self_path)
            if mtime != last_mtime:
                print("🔄 debug_server.py が更新されました。再起動します...", flush=True)
                os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            pass

threading.Thread(target=_watch_self_restart, daemon=True).start()


def open_browser():
    time.sleep(1.2)
    webbrowser.open("http://127.0.0.1:5001")


if __name__ == "__main__":
    print("=" * 50)
    print("  自律デバッグAI 起動中...")
    print("  http://127.0.0.1:5001")
    print("  停止: Ctrl+C")
    print("=" * 50)
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(port=5001, debug=True, use_reloader=False)
