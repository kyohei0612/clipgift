// ClipGift Tauri shell.
//
// Architecture (hybrid A):
//   - Spawns Flask backend (pythonw app.py) as a child process
//   - Waits for http://127.0.0.1:5001 to become ready
//   - Opens a Tauri WebviewWindow pointing at the local Flask server
//   - On window close, terminates the Flask child process
//
// Existing Python assets (chat analyzer, mp4inchatnagasi, downloader, ffmpeg,
// pytubefix, curl_cffi) remain untouched. This binary only replaces the launcher
// + window frame layer (formerly launcher.vbs + Chrome --app / pywebview).

use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};

const SERVER_PORT: u16 = 5001;
const SERVER_URL: &str = "http://127.0.0.1:5001";

struct FlaskProcess(Mutex<Option<Child>>);

/// Resolve the directory where app.py lives.
///
/// Layout assumption (production install):
///   {install}\ClipGift.exe   <-- current_exe
///   {install}\app.py
///   {install}\bin\python_path.txt
fn resolve_base_dir() -> PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            return parent.to_path_buf();
        }
    }
    PathBuf::from(".")
}

/// Resolve the pythonw.exe path.
///
/// 1. Read `bin/python_path.txt` written by the installer (Inno Setup [Run] step)
/// 2. Fall back to "pythonw" on PATH
fn resolve_pythonw(base_dir: &PathBuf) -> String {
    let path_file = base_dir.join("bin").join("python_path.txt");
    if let Ok(content) = std::fs::read_to_string(&path_file) {
        let recorded = content.trim().to_string();
        let pythonw = recorded.replace("python.exe", "pythonw.exe");
        if std::path::Path::new(&pythonw).exists() {
            return pythonw;
        }
        if std::path::Path::new(&recorded).exists() {
            return recorded;
        }
    }
    "pythonw".to_string()
}

fn server_is_up() -> bool {
    let addr = format!("127.0.0.1:{}", SERVER_PORT);
    let socket: std::net::SocketAddr = match addr.parse() {
        Ok(s) => s,
        Err(_) => return false,
    };
    std::net::TcpStream::connect_timeout(&socket, Duration::from_millis(800)).is_ok()
}

fn wait_for_server(timeout_sec: u64) -> bool {
    let deadline = Instant::now() + Duration::from_secs(timeout_sec);
    while Instant::now() < deadline {
        if server_is_up() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    false
}

fn spawn_flask(base_dir: &PathBuf) -> std::io::Result<Child> {
    let pythonw = resolve_pythonw(base_dir);
    let app_py = base_dir.join("app.py");
    let mut cmd = Command::new(&pythonw);
    cmd.arg(&app_py)
        .current_dir(base_dir)
        .env("CLIPGEN_PORT", SERVER_PORT.to_string())
        .env("LAUNCHED_BY_VBS", "1");

    // Suppress console window on Windows (CREATE_NO_WINDOW = 0x08000000).
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000);
    }

    cmd.spawn()
}

fn kill_flask(state: &FlaskProcess) {
    if let Ok(mut guard) = state.0.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(FlaskProcess(Mutex::new(None)))
        .setup(|app| {
            let base_dir = resolve_base_dir();

            // Start Flask immediately if not already running.
            if !server_is_up() {
                match spawn_flask(&base_dir) {
                    Ok(child) => {
                        let state = app.state::<FlaskProcess>();
                        let mut guard = state.0.lock().expect("FlaskProcess mutex poisoned");
                        *guard = Some(child);
                    }
                    Err(e) => {
                        eprintln!("Failed to spawn Flask: {}", e);
                    }
                }
            }

            // Build the main window. WebView2 will retry until the server responds,
            // but we wait for readiness in a background thread to keep startup snappy
            // without blocking the Tauri event loop.
            let app_handle = app.handle().clone();
            std::thread::spawn(move || {
                let server_up = wait_for_server(30);
                let url = WebviewUrl::External(SERVER_URL.parse().expect("valid URL"));
                let builder = WebviewWindowBuilder::new(&app_handle, "main", url)
                    .title("ClipGift")
                    .inner_size(1280.0, 820.0)
                    .min_inner_size(1080.0, 640.0)
                    .resizable(true)
                    .center();
                if let Err(e) = builder.build() {
                    eprintln!("Failed to build window: {}", e);
                }

                // Flask が終了したら（「閉じる」→ /api/shutdown の os._exit など）
                // Tauri アプリ本体も終了させる。以前はここが無く、Flask だけ死んで
                // ウィンドウが真っ白のまま残る（＝アプリが終了しない）バグだった。
                // 一時的な接続ブレで誤終了しないよう、3 回連続でポートが落ちたら終了する。
                if server_up {
                    let mut misses = 0;
                    loop {
                        std::thread::sleep(Duration::from_secs(1));
                        if server_is_up() {
                            misses = 0;
                        } else {
                            misses += 1;
                            if misses >= 3 {
                                app_handle.exit(0);
                                break;
                            }
                        }
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                let state = window.state::<FlaskProcess>();
                kill_flask(&state);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
