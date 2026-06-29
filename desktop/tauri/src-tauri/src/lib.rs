// ContextSeek Desktop — Tauri Host.
//
// Responsibilities (Rust does lifecycle + platform only; all semantics stay in
// the Python sidecar):
//   1. pick a loopback port (fixed first, then a free one),
//   2. spawn the bundled `contextseek-desktop-server` sidecar,
//   3. poll GET /health until ready, then point the window at the local server,
//   4. tray + autostart + restart-on-crash, and graceful shutdown on exit.

use std::sync::Mutex;
use std::time::Duration;
use std::{collections::VecDeque, time::Instant};

use serde::Serialize;
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::{TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, WebviewWindow, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const FIXED_PORT: u16 = 8000;
const HEALTH_RETRIES: u32 = 120;
const HEALTH_INTERVAL_MS: u64 = 500;
const HEALTH_POLL_TIMEOUT_MS: u64 = 700;
const HEALTH_STATUS_TIMEOUT_MS: u64 = 180;
const SHUTDOWN_GRACE_MS: u64 = 3500;
const MAX_CRASH_RESTARTS_PER_MIN: usize = 3;
const MAX_START_ATTEMPTS: usize = 2;
const CRASH_WINDOW_SECS: u64 = 60;
const CRASH_RESTART_BACKOFF_MS: u64 = 1200;

/// Shared runtime state: the live sidecar child and the port it serves on.
#[derive(Default)]
struct SidecarRuntime {
    child: Option<CommandChild>,
    port: u16,
    generation: u64,
    intentional_stop: Option<u64>,
}

#[derive(Default)]
struct AppState {
    runtime: Mutex<SidecarRuntime>,
    crash_restarts: Mutex<VecDeque<Instant>>,
}

#[derive(Serialize)]
struct BackendStatus {
    port: u16,
    healthy: bool,
    url: Option<String>,
}

fn port_bindable(port: u16) -> bool {
    use std::net::TcpListener;
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

/// Return FIXED_PORT if bindable, otherwise an OS-assigned free port.
/// When retrying after a bind race, avoid reusing the failed port.
fn pick_port_avoiding(avoid: Option<u16>) -> u16 {
    use std::net::TcpListener;
    if avoid != Some(FIXED_PORT) && port_bindable(FIXED_PORT) {
        return FIXED_PORT;
    }
    for _ in 0..16 {
        if let Some(port) = TcpListener::bind(("127.0.0.1", 0))
            .ok()
            .and_then(|l| l.local_addr().ok())
            .map(|a| a.port())
        {
            if Some(port) != avoid {
                return port;
            }
        }
    }
    if avoid == Some(FIXED_PORT) {
        0
    } else {
        FIXED_PORT
    }
}

/// Spawn the Python sidecar on `port`; store the child in state.
fn spawn_sidecar(app: &AppHandle, port: u16) -> Result<(), String> {
    let mut sidecar = app
        .shell()
        .sidecar("contextseek-desktop-server")
        .map_err(|e| format!("sidecar resolve failed: {e}"))?
        .args(["--host", "127.0.0.1", "--port", &port.to_string()]);

    // Point the sidecar at the SPA bundled as a Tauri resource (see
    // tauri.conf.json `bundle.resources`). In dev the sidecar falls back to the
    // repo's dashboard/dist, so this is only required for packaged builds.
    if let Ok(res_dir) = app.path().resource_dir() {
        let dist = res_dir.join("dashboard-dist");
        if dist.join("index.html").is_file() {
            sidecar = sidecar.env("CTX_DASHBOARD_DIST", dist.to_string_lossy().to_string());
        }
    }

    let (mut rx, child) = sidecar
        .spawn()
        .map_err(|e| format!("sidecar spawn failed: {e}"))?;

    let generation = {
        let state = app.state::<AppState>();
        let mut runtime = state.runtime.lock().unwrap();
        runtime.generation = runtime.generation.saturating_add(1);
        runtime.child = Some(child);
        runtime.port = port;
        runtime.generation
    };

    // Drain sidecar output; surface unexpected termination to the UI.
    let app_for_events = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                    eprintln!("[sidecar] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Terminated(payload) => {
                    eprintln!("[sidecar] terminated: {:?}", payload.code);
                    let should_auto_restart_after_exit = {
                        let state = app_for_events.state::<AppState>();
                        let mut runtime = state.runtime.lock().unwrap();
                        if runtime.generation != generation {
                            false
                        } else {
                            runtime.child = None;
                            if runtime.intentional_stop == Some(generation) {
                                runtime.intentional_stop = None;
                                false
                            } else {
                                true
                            }
                        }
                    };
                    if !should_auto_restart_after_exit {
                        break;
                    }
                    if let Some(win) = app_for_events.get_webview_window("main") {
                        let _ = win.eval(
                            "window.dispatchEvent(new CustomEvent('sidecar-down'));",
                        );
                    }
                    if should_auto_restart(&app_for_events) {
                        std::thread::sleep(Duration::from_millis(CRASH_RESTART_BACKOFF_MS));
                        start_backend(&app_for_events);
                    } else if let Some(win) = app_for_events.get_webview_window("main") {
                        show_app_or_diagnostic(&win, false, current_port(&app_for_events));
                    }
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(())
}

fn current_port(app: &AppHandle) -> u16 {
    let state = app.state::<AppState>();
    let port = state.runtime.lock().unwrap().port;
    port
}

fn health_url(port: u16) -> String {
    format!("http://127.0.0.1:{port}/health")
}

fn shutdown_url(port: u16) -> String {
    format!("http://127.0.0.1:{port}/__desktop/shutdown")
}

fn is_healthy(port: u16, timeout_ms: u64) -> bool {
    if port == 0 {
        return false;
    }
    ureq::get(&health_url(port))
        .timeout(Duration::from_millis(timeout_ms))
        .call()
        .is_ok()
}

/// Poll GET /health until ready. Returns true once the server answers.
fn wait_for_health(port: u16) -> bool {
    for _ in 0..HEALTH_RETRIES {
        if is_healthy(port, HEALTH_POLL_TIMEOUT_MS) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(HEALTH_INTERVAL_MS));
    }
    false
}

/// Wait until health endpoint goes down or timeout.
fn wait_until_down(port: u16, timeout_ms: u64) -> bool {
    let url = health_url(port);
    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
    while Instant::now() < deadline {
        if ureq::get(&url)
            .timeout(Duration::from_millis(600))
            .call()
            .is_err()
        {
            return true;
        }
        std::thread::sleep(Duration::from_millis(120));
    }
    false
}

/// Point the main window at the local server, or the diagnostic page on failure.
fn show_app_or_diagnostic(win: &WebviewWindow, healthy: bool, port: u16) {
    let target = if healthy {
        format!("http://127.0.0.1:{port}/")
    } else {
        format!("diagnostic.html?port={port}")
    };
    if let Ok(url) = target.parse() {
        if win.navigate(url).is_ok() {
            let _ = win.show();
            let _ = win.set_focus();
            return;
        }
    }

    // If native navigation misses the window readiness moment, let the
    // currently loaded bootstrap page perform the same transition.
    if let Ok(js_target) = serde_json::to_string(&target) {
        let _ = win.eval(&format!("window.location.replace({js_target});"));
    }
    let _ = win.show();
    let _ = win.set_focus();
}

/// Start (or restart) the sidecar and route the window accordingly.
fn start_backend(app: &AppHandle) {
    // Kill any previous child first (used by the tray "Restart service").
    kill_sidecar(app);

    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        let mut avoid_port = None;
        for attempt in 0..MAX_START_ATTEMPTS {
            let port = pick_port_avoiding(avoid_port);
            if port == 0 {
                if let Some(win) = app_handle.get_webview_window("main") {
                    show_app_or_diagnostic(&win, false, avoid_port.unwrap_or(FIXED_PORT));
                }
                return;
            }
            if let Err(e) = spawn_sidecar(&app_handle, port) {
                eprintln!("{e}");
                if let Some(win) = app_handle.get_webview_window("main") {
                    show_app_or_diagnostic(&win, false, port);
                }
                return;
            }
            let healthy =
                tauri::async_runtime::spawn_blocking(move || wait_for_health(port))
                    .await
                    .unwrap_or(false);
            if healthy {
                if let Some(win) = app_handle.get_webview_window("main") {
                    show_app_or_diagnostic(&win, true, port);
                }
                return;
            }

            let retryable_bind_conflict = !port_bindable(port);
            eprintln!(
                "[desktop] backend failed health check on port {port}; retryable_bind_conflict={retryable_bind_conflict}"
            );
            kill_sidecar(&app_handle);
            if retryable_bind_conflict && attempt + 1 < MAX_START_ATTEMPTS {
                avoid_port = Some(port);
                continue;
            }
            if let Some(win) = app_handle.get_webview_window("main") {
                show_app_or_diagnostic(&win, false, port);
            }
            return;
        }
    });
}

/// Terminate the sidecar child if running.
fn kill_sidecar(app: &AppHandle) {
    let state = app.state::<AppState>();
    let (port, child) = {
        let mut runtime = state.runtime.lock().unwrap();
        let child = runtime.child.take();
        if child.is_some() {
            runtime.intentional_stop = Some(runtime.generation);
        }
        (runtime.port, child)
    };
    if let Some(child) = child {
        // Ask the sidecar to shutdown gracefully first; if it does not exit
        // within the grace period, force-kill the process.
        let _ = ureq::post(&shutdown_url(port))
            .timeout(Duration::from_millis(700))
            .call();
        if !wait_until_down(port, SHUTDOWN_GRACE_MS) {
            let _ = child.kill();
        }
    }
}

fn should_auto_restart(app: &AppHandle) -> bool {
    let state = app.state::<AppState>();
    let mut hist = state.crash_restarts.lock().unwrap();
    let cutoff = Instant::now() - Duration::from_secs(CRASH_WINDOW_SECS);
    while hist.front().map(|t| *t < cutoff).unwrap_or(false) {
        let _ = hist.pop_front();
    }
    if hist.len() >= MAX_CRASH_RESTARTS_PER_MIN {
        return false;
    }
    hist.push_back(Instant::now());
    true
}

#[tauri::command]
fn restart_service(app: AppHandle) {
    start_backend(&app);
}

#[tauri::command]
fn backend_status(app: AppHandle) -> BackendStatus {
    let port = current_port(&app);
    let healthy = is_healthy(port, HEALTH_STATUS_TIMEOUT_MS);
    BackendStatus {
        port,
        healthy,
        url: healthy.then(|| format!("http://127.0.0.1:{port}/")),
    }
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let show = MenuItemBuilder::with_id("show", "Show").build(app)?;
    let hide = MenuItemBuilder::with_id("hide", "Hide").build(app)?;
    let restart = MenuItemBuilder::with_id("restart", "Restart service").build(app)?;
    let quit = MenuItemBuilder::with_id("quit", "Quit").build(app)?;
    let menu = MenuBuilder::new(app)
        .items(&[&show, &hide, &restart, &quit])
        .build()?;

    TrayIconBuilder::with_id("main-tray")
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&menu)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
            "hide" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
            }
            "restart" => start_backend(app),
            "quit" => {
                kill_sidecar(app);
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { .. } = event {
                let app = tray.app_handle();
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
        })
        .build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .invoke_handler(tauri::generate_handler![restart_service, backend_status])
        .manage(AppState::default())
        .setup(|app| {
            let handle = app.handle();
            build_tray(handle)?;
            start_backend(handle);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building ContextSeek desktop app")
        .run(|app, event| match event {
            // Keep running in the tray when the main window is closed.
            RunEvent::WindowEvent {
                label,
                event: WindowEvent::CloseRequested { api, .. },
                ..
            } if label == "main" => {
                api.prevent_close();
                if let Some(w) = app.get_webview_window(&label) {
                    let _ = w.hide();
                }
            }
            RunEvent::Exit => {
                kill_sidecar(app);
            }
            _ => {}
        });
}
