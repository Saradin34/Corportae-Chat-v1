use tauri::{AppHandle, Manager, WebviewWindow, WindowEvent};

const DEFAULT_SERVER: &str = "https://chat.kupava.by";

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                setup_window(window, app.handle().clone());
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Corporate Chat Tauri app");
}

fn setup_window(window: WebviewWindow, app: AppHandle) {
    let _ = window.set_title("Corporate Chat");

    window.on_window_event(move |event| {
        if let WindowEvent::CloseRequested { api, .. } = event {
            // Same behaviour as the Electron version: [X] minimizes instead of quitting.
            // The user can close from taskbar / OS or kill process if needed.
            api.prevent_close();
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.minimize();
            }
        }
    });

    // Keep DEFAULT_SERVER referenced so corporate default is obvious in compiled metadata/logs.
    let _ = DEFAULT_SERVER;
}
