using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using System.Diagnostics;
using System.Text.Json;
using System.Runtime.InteropServices;
using Velopack;
using Velopack.Exceptions;

namespace CorporateChatWebView2;

public sealed class MainForm : Form
{
    private const string DefaultServer = "https://chat.kupava.by";
    private const string DefaultUpdateSource = "https://chat.kupava.by/desktop-updates";
    private readonly WebView2 _web = new();
    private readonly string _appDir;
    private readonly string _configPath;
    private readonly string _userDataDir;
    private AppConfig _config;
    private bool _reallyExit;
    private int _chatUnread;
    private int _callUnread;
    private readonly Dictionary<string, Icon?> _stateIcons = new();

    public MainForm()
    {
        Text = "Corporate Chat";
        Width = 1200;
        Height = 800;
        MinimumSize = new Size(360, 560);
        StartPosition = FormStartPosition.CenterScreen;
        SetWindowIcon(GetStateIcon("base") ?? TryLoadIcon("icon.ico"));

        _appDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Corporate Chat WebView2");
        Directory.CreateDirectory(_appDir);
        _configPath = Path.Combine(_appDir, "config.json");
        _userDataDir = Path.Combine(_appDir, "WebView2Profile");
        _config = LoadConfig();

        MainMenuStrip = BuildMenu();
        Controls.Add(MainMenuStrip);

        _web.Dock = DockStyle.Fill;
        _web.CreationProperties = new CoreWebView2CreationProperties
        {
            UserDataFolder = _userDataDir,
            AdditionalBrowserArguments = "--auth-server-whitelist=*.kupava.by --auth-negotiate-delegate-whitelist=*.kupava.by"
        };
        Controls.Add(_web);
        _web.BringToFront();

        Shown += async (_, _) => await InitWebViewAsync();
        FormClosing += OnFormClosing;
    }

    private Icon? TryLoadIcon(string fileName = "app-main.png")
    {
        try
        {
            var p = Path.Combine(AppContext.BaseDirectory, fileName);
            if (!File.Exists(p)) return null;
            if (Path.GetExtension(p).Equals(".ico", StringComparison.OrdinalIgnoreCase)) return new Icon(p);

            using var bmp = new Bitmap(p);
            // Clone the icon so it remains valid after bitmap disposal.
            using var tmp = Icon.FromHandle(bmp.GetHicon());
            return (Icon)tmp.Clone();
        }
        catch { return null; }
    }

    private Icon? GetStateIcon(string mode)
    {
        if (_stateIcons.TryGetValue(mode, out var cached)) return cached;
        var file = mode switch
        {
            "both" => "app-both.png",
            "call" => "app-call.png",
            "unread" => "app-unread.png",
            _ => "app-main.png",
        };
        var icon = TryLoadIcon(file) ?? TryLoadIcon("app-main.png") ?? TryLoadIcon("icon.ico");
        _stateIcons[mode] = icon;
        return icon;
    }

    private static ToolStripMenuItem MenuItem(string text, EventHandler onClick, Keys shortcut = Keys.None)
    {
        var item = new ToolStripMenuItem(text, null, onClick);
        if (shortcut != Keys.None) item.ShortcutKeys = shortcut;
        return item;
    }

    private MenuStrip BuildMenu()
    {
        var menu = new MenuStrip();

        var file = new ToolStripMenuItem("Файл");
        file.DropDownItems.Add(MenuItem("Сменить сервер...", async (_, _) => await ChangeServerAsync()));
        file.DropDownItems.Add(MenuItem("Проверить обновления...", async (_, _) => await CheckForUpdatesAsync(false)));
        file.DropDownItems.Add(new ToolStripSeparator());
        file.DropDownItems.Add(MenuItem("Перезагрузить", (_, _) => _web.CoreWebView2?.Reload(), Keys.Control | Keys.R));
        file.DropDownItems.Add(MenuItem("Жёстко обновить", async (_, _) => await HardReloadAsync(), Keys.Control | Keys.Shift | Keys.R));
        file.DropDownItems.Add(MenuItem("Очистить кэш", async (_, _) => await ClearCacheAsync()));
        file.DropDownItems.Add(MenuItem("Свернуть", (_, _) => WindowState = FormWindowState.Minimized, Keys.Control | Keys.M));
        file.DropDownItems.Add(new ToolStripSeparator());
        file.DropDownItems.Add(MenuItem("Закрыть приложение", (_, _) => { _reallyExit = true; Close(); }, Keys.Control | Keys.Q));

        var edit = new ToolStripMenuItem("Правка");
        edit.DropDownItems.Add(MenuItem("Отменить", (_, _) => ExecScript("document.execCommand('undo')"), Keys.Control | Keys.Z));
        edit.DropDownItems.Add(MenuItem("Повторить", (_, _) => ExecScript("document.execCommand('redo')"), Keys.Control | Keys.Y));
        edit.DropDownItems.Add(new ToolStripSeparator());
        edit.DropDownItems.Add(MenuItem("Вырезать", (_, _) => ExecScript("document.execCommand('cut')"), Keys.Control | Keys.X));
        edit.DropDownItems.Add(MenuItem("Копировать", (_, _) => ExecScript("document.execCommand('copy')"), Keys.Control | Keys.C));
        edit.DropDownItems.Add(MenuItem("Вставить", (_, _) => ExecScript("document.execCommand('paste')"), Keys.Control | Keys.V));
        edit.DropDownItems.Add(MenuItem("Выделить всё", (_, _) => ExecScript("document.execCommand('selectAll')"), Keys.Control | Keys.A));

        var view = new ToolStripMenuItem("Вид");
        view.DropDownItems.Add(MenuItem("Масштаб 100%", (_, _) => _web.ZoomFactor = 1.0));
        view.DropDownItems.Add(MenuItem("Увеличить", (_, _) => _web.ZoomFactor = Math.Min(_web.ZoomFactor + .1, 3.0), Keys.Control | Keys.Oemplus));
        view.DropDownItems.Add(MenuItem("Уменьшить", (_, _) => _web.ZoomFactor = Math.Max(_web.ZoomFactor - .1, .5), Keys.Control | Keys.OemMinus));
        view.DropDownItems.Add(MenuItem("Полный экран", (_, _) => ToggleFullscreen(), Keys.F11));
        view.DropDownItems.Add(new ToolStripSeparator());
        var iconTest = new ToolStripMenuItem("Тест состояния иконки");
        iconTest.DropDownItems.Add(MenuItem("Обычная", (_, _) => ApplyUnreadState(0, 0, false)));
        iconTest.DropDownItems.Add(MenuItem("Непрочитанное сообщение", (_, _) => ApplyUnreadState(1, 0, true)));
        iconTest.DropDownItems.Add(MenuItem("Пропущенный звонок", (_, _) => ApplyUnreadState(0, 1, true)));
        iconTest.DropDownItems.Add(MenuItem("Сообщение + звонок", (_, _) => ApplyUnreadState(1, 1, true)));
        view.DropDownItems.Add(iconTest);

        menu.Items.Add(file);
        menu.Items.Add(edit);
        menu.Items.Add(view);
        return menu;
    }

    private async Task InitWebViewAsync()
    {
        try
        {
            var env = await CoreWebView2Environment.CreateAsync(null, _userDataDir);
            await _web.EnsureCoreWebView2Async(env);
            _web.CoreWebView2.Settings.IsWebMessageEnabled = true;

            _web.CoreWebView2.WebMessageReceived += (_, e) => HandleWebMessage(e.WebMessageAsJson);
            await _web.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(@"
(() => {
  if (window.CorporateChatDesktop) return;
  function post(type, data) {
    try { window.chrome.webview.postMessage(Object.assign({ type: type }, data || {})); } catch (e) {}
    return Promise.resolve({ ok: true });
  }
  window.CorporateChatDesktop = {
    isDesktop: true,
    version: '2.0.0-webview2',
    platform: 'win32-webview2',
    setUnreadState: (chatUnread, callUnread, flash) => post('setUnreadState', { chatUnread: chatUnread, callUnread: callUnread, flash: !!flash }),
    setUnread: (count, flash) => post('setUnreadState', { chatUnread: count, callUnread: 0, flash: !!flash }),
    clearFlash: () => post('clearFlash'),
    hardReload: () => post('hardReload'),
    clearCache: () => post('clearCache'),
    changeServer: () => post('changeServer'),
    openLocalPath: (path) => post('openLocalPath', { path: path })
  };
  try { window.dispatchEvent(new Event('CorporateChatDesktopReady')); } catch (e) {}
})();");

            _web.CoreWebView2.PermissionRequested += (_, e) =>
            {
                // Make the desktop shell behave like the browser after site permission prompt.
                // These permissions are needed for notifications, microphone/WebRTC and clipboard.
                if (e.PermissionKind is CoreWebView2PermissionKind.Notifications
                    or CoreWebView2PermissionKind.Microphone
                    or CoreWebView2PermissionKind.Camera
                    or CoreWebView2PermissionKind.ClipboardRead)
                {
                    e.State = CoreWebView2PermissionState.Allow;
                }
            };

            _web.CoreWebView2.NewWindowRequested += (_, e) =>
            {
                e.Handled = true;
                var uri = e.Uri ?? "";
                if (IsSameHost(uri, _config.Server)) _web.CoreWebView2.Navigate(uri);
                else OpenExternal(uri);
            };

            _web.CoreWebView2.NavigationStarting += (_, e) =>
            {
                var uri = e.Uri ?? "";
                if (uri.StartsWith("cchatlocal://open", StringComparison.OrdinalIgnoreCase))
                {
                    e.Cancel = true;
                    OpenLocalPath(LocalPathFromCustomUri(uri));
                }
            };

            _web.CoreWebView2.NavigationCompleted += (_, e) =>
            {
                if (!e.IsSuccess)
                {
                    Text = "Corporate Chat — ошибка подключения";
                }
                else
                {
                    ApplyUnreadState(_chatUnread, _callUnread, false);
                }
            };

            _web.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
            _web.CoreWebView2.Settings.AreDevToolsEnabled = false;
            _web.CoreWebView2.Settings.IsStatusBarEnabled = false;

            _web.CoreWebView2.Navigate(NormalizeServer(_config.Server));
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, ex.Message, "Corporate Chat", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }


    private void HandleWebMessage(string json)
    {
        try
        {
            var msg = JsonSerializer.Deserialize<DesktopMessage>(json, new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
            if (msg == null || string.IsNullOrWhiteSpace(msg.Type)) return;
            switch (msg.Type)
            {
                case "setUnreadState":
                    ApplyUnreadState(msg.ChatUnread, msg.CallUnread, msg.Flash);
                    break;
                case "clearFlash":
                    Flash(false);
                    break;
                case "hardReload":
                    _ = HardReloadAsync();
                    break;
                case "clearCache":
                    _ = ClearCacheAsync();
                    break;
                case "changeServer":
                    _ = ChangeServerAsync();
                    break;
                case "openLocalPath":
                    OpenLocalPath(msg.Path ?? "");
                    break;
            }
        }
        catch { }
    }

    private void ApplyUnreadState(int chatUnread, int callUnread, bool flash)
    {
        chatUnread = Math.Max(0, chatUnread);
        callUnread = Math.Max(0, callUnread);
        _chatUnread = chatUnread;
        _callUnread = callUnread;
        var total = chatUnread + callUnread;
        var mode = chatUnread > 0 && callUnread > 0 ? "both" : callUnread > 0 ? "call" : chatUnread > 0 ? "unread" : "base";

        var icon = GetStateIcon(mode);
        // Do NOT use taskbar overlay here: overlay draws on top of the app icon
        // and causes icon stacking. We clear any previous overlay and replace
        // the actual window/taskbar icon via WM_SETICON instead.
        SetTaskbarOverlay(null, "");
        SetWindowIcon(icon);

        Text = total > 0 ? $"({Math.Min(total, 99)}) Corporate Chat" : "Corporate Chat";
        if (total > 0 && flash && !Focused) Flash(true);
        if (total == 0) Flash(false);
    }


    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    private static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);

    private const int WM_SETICON = 0x0080;
    private static readonly IntPtr ICON_SMALL = new(0);
    private static readonly IntPtr ICON_BIG = new(1);

    private void SetWindowIcon(Icon? icon)
    {
        if (icon == null) return;
        try
        {
            Icon = icon;
            if (IsHandleCreated)
            {
                SendMessage(Handle, WM_SETICON, ICON_SMALL, icon.Handle);
                SendMessage(Handle, WM_SETICON, ICON_BIG, icon.Handle);
            }
        }
        catch { }
    }

    private void SetTaskbarOverlay(Icon? icon, string description)
    {
        try
        {
            if (!IsHandleCreated) return;
            var taskbar = (ITaskbarList3)new CTaskbarList();
            taskbar.HrInit();
            taskbar.SetOverlayIcon(Handle, icon == null ? IntPtr.Zero : icon.Handle, description ?? "");
        }
        catch { }
    }

    [ComImport]
    [Guid("56FDF344-FD6D-11d0-958A-006097C9A090")]
    [ClassInterface(ClassInterfaceType.None)]
    private class CTaskbarList { }

    [ComImport]
    [Guid("EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface ITaskbarList3
    {
        void HrInit();
        void AddTab(IntPtr hwnd);
        void DeleteTab(IntPtr hwnd);
        void ActivateTab(IntPtr hwnd);
        void SetActiveAlt(IntPtr hwnd);
        void MarkFullscreenWindow(IntPtr hwnd, bool fFullscreen);
        void SetProgressValue(IntPtr hwnd, ulong ullCompleted, ulong ullTotal);
        void SetProgressState(IntPtr hwnd, int tbpFlags);
        void RegisterTab(IntPtr hwndTab, IntPtr hwndMDI);
        void UnregisterTab(IntPtr hwndTab);
        void SetTabOrder(IntPtr hwndTab, IntPtr hwndInsertBefore);
        void SetTabActive(IntPtr hwndTab, IntPtr hwndMDI, uint dwReserved);
        void ThumbBarAddButtons(IntPtr hwnd, uint cButtons, IntPtr pButton);
        void ThumbBarUpdateButtons(IntPtr hwnd, uint cButtons, IntPtr pButton);
        void ThumbBarSetImageList(IntPtr hwnd, IntPtr himl);
        void SetOverlayIcon(IntPtr hwnd, IntPtr hIcon, [MarshalAs(UnmanagedType.LPWStr)] string pszDescription);
        void SetThumbnailTooltip(IntPtr hwnd, [MarshalAs(UnmanagedType.LPWStr)] string pszTip);
        void SetThumbnailClip(IntPtr hwnd, IntPtr prcClip);
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct FLASHWINFO
    {
        public uint cbSize;
        public IntPtr hwnd;
        public uint dwFlags;
        public uint uCount;
        public uint dwTimeout;
    }

    [DllImport("user32.dll")]
    private static extern bool FlashWindowEx(ref FLASHWINFO pwfi);

    private const uint FLASHW_STOP = 0;
    private const uint FLASHW_ALL = 3;
    private const uint FLASHW_TIMERNOFG = 12;

    private void Flash(bool on)
    {
        try
        {
            var f = new FLASHWINFO
            {
                cbSize = Convert.ToUInt32(Marshal.SizeOf<FLASHWINFO>()),
                hwnd = Handle,
                dwFlags = on ? (FLASHW_ALL | FLASHW_TIMERNOFG) : FLASHW_STOP,
                uCount = on ? uint.MaxValue : 0,
                dwTimeout = 0
            };
            FlashWindowEx(ref f);
        }
        catch { }
    }

    private async Task CheckForUpdatesAsync(bool silent)
    {
        var source = string.IsNullOrWhiteSpace(_config.UpdateSource) ? DefaultUpdateSource : _config.UpdateSource.Trim();
        try
        {
            var mgr = new UpdateManager(source);
            var update = await mgr.CheckForUpdatesAsync();
            if (update == null)
            {
                if (!silent) MessageBox.Show(this, "Установлена последняя версия.", "Corporate Chat", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            var version = update.TargetFullRelease?.Version?.ToString() ?? "новая версия";
            if (silent || MessageBox.Show(this,
                    $"Доступно обновление {version}. Скачать, установить и перезапустить приложение?",
                    "Corporate Chat — обновление",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Question) == DialogResult.Yes)
            {
                await mgr.DownloadUpdatesAsync(update);
                mgr.ApplyUpdatesAndRestart(update);
            }
        }
        catch (NotInstalledException)
        {
            if (!silent)
            {
                MessageBox.Show(this,
                    "Автообновление работает только если приложение установлено через Velopack Setup.exe.\n" +
                    "Если запущена распакованная папка dist, установите приложение из desktop-updates Setup.exe.",
                    "Corporate Chat", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
        }
        catch (Exception ex)
        {
            if (!silent) MessageBox.Show(this, ex.Message, "Ошибка обновления", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    private async Task ChangeServerAsync()
    {
        using var dlg = new ServerDialog(_config.Server);
        if (dlg.ShowDialog(this) == DialogResult.OK)
        {
            _config.Server = NormalizeServer(dlg.Server);
            SaveConfig(_config);
            if (_web.CoreWebView2 != null) _web.CoreWebView2.Navigate(_config.Server);
        }
    }

    private async Task HardReloadAsync()
    {
        await ClearCacheAsync(showMessage: false);
        _web.CoreWebView2?.Reload();
    }

    private async Task ClearCacheAsync(bool showMessage = true)
    {
        try
        {
            if (_web.CoreWebView2?.Profile != null)
            {
                await _web.CoreWebView2.Profile.ClearBrowsingDataAsync();
            }
            if (showMessage) MessageBox.Show(this, "Кэш очищен", "Corporate Chat", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        catch
        {
            if (showMessage) MessageBox.Show(this, "Не удалось очистить кэш", "Corporate Chat", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    private void ExecScript(string script)
    {
        try { _ = _web.CoreWebView2?.ExecuteScriptAsync(script); } catch { }
    }

    private void ToggleFullscreen()
    {
        if (FormBorderStyle == FormBorderStyle.None)
        {
            FormBorderStyle = FormBorderStyle.Sizable;
            WindowState = FormWindowState.Normal;
        }
        else
        {
            FormBorderStyle = FormBorderStyle.None;
            WindowState = FormWindowState.Maximized;
        }
    }

    private void OnFormClosing(object? sender, FormClosingEventArgs e)
    {
        if (_reallyExit) return;
        e.Cancel = true;
        WindowState = FormWindowState.Minimized;
    }

    private AppConfig LoadConfig()
    {
        try
        {
            if (File.Exists(_configPath))
            {
                var cfg = JsonSerializer.Deserialize<AppConfig>(File.ReadAllText(_configPath));
                if (cfg != null && !string.IsNullOrWhiteSpace(cfg.Server)) return cfg;
            }
        }
        catch { }
        return new AppConfig { Server = DefaultServer };
    }

    private void SaveConfig(AppConfig cfg)
    {
        try
        {
            Directory.CreateDirectory(_appDir);
            File.WriteAllText(_configPath, JsonSerializer.Serialize(cfg, new JsonSerializerOptions { WriteIndented = true }));
        }
        catch { }
    }

    private static string NormalizeServer(string? s)
    {
        s = (s ?? "").Trim().TrimEnd('/');
        if (string.IsNullOrWhiteSpace(s)) s = DefaultServer;
        if (!s.StartsWith("http://", StringComparison.OrdinalIgnoreCase) && !s.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            s = "https://" + s;
        return s;
    }

    private static bool IsSameHost(string uri, string server)
    {
        try { return new Uri(uri).Host.Equals(new Uri(server).Host, StringComparison.OrdinalIgnoreCase); }
        catch { return false; }
    }

    private static string LocalPathFromCustomUri(string uriText)
    {
        try
        {
            var uri = new Uri(uriText);
            var query = uri.Query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries);
            foreach (var part in query)
            {
                var kv = part.Split('=', 2);
                if (kv.Length == 2 && kv[0].Equals("path", StringComparison.OrdinalIgnoreCase))
                    return Uri.UnescapeDataString(kv[1].Replace('+', ' '));
            }
        }
        catch { }
        return "";
    }

    private static void OpenLocalPath(string rawPath)
    {
        try
        {
            var path = NormalizeLocalPath(rawPath);
            if (string.IsNullOrWhiteSpace(path)) return;

            static bool IsAllowed(string value)
            {
                return (value.Length >= 3 && char.IsLetter(value[0]) && value[1] == ':' && value[2] == '\\')
                    || value.StartsWith("\\\\");
            }

            if (!IsAllowed(path))
            {
                MessageBox.Show("Разрешены только локальные Windows-пути и UNC-пути.", "Corporate Chat", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            static string CleanTail(string value) => (value ?? "").Trim().Trim('"', '\'', '.', ',', ';', ')', ']', '}', '»');

            var candidates = new List<string>();
            var cur = CleanTail(path);
            while (!string.IsNullOrWhiteSpace(cur))
            {
                if (IsAllowed(cur) && !candidates.Contains(cur, StringComparer.OrdinalIgnoreCase)) candidates.Add(cur);
                var lastSpace = cur.LastIndexOf(' ');
                if (lastSpace < 0) break;
                cur = CleanTail(cur[..lastSpace]);
            }

            foreach (var candidate in candidates)
            {
                if (File.Exists(candidate) || Directory.Exists(candidate))
                {
                    Process.Start(new ProcessStartInfo(candidate) { UseShellExecute = true });
                    return;
                }
            }

            MessageBox.Show("Путь не найден или нет доступа:\n" + path, "Corporate Chat", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
        catch (Exception ex)
        {
            MessageBox.Show("Не удалось открыть путь:\n" + ex.Message, "Corporate Chat", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    private static string NormalizeLocalPath(string? rawPath)
    {
        var path = (rawPath ?? "").Trim().Trim('"', '\'', '«', '»');
        if (string.IsNullOrWhiteSpace(path)) return "";

        if (path.StartsWith("file:", StringComparison.OrdinalIgnoreCase))
        {
            try
            {
                var uri = new Uri(path);
                path = uri.LocalPath;
            }
            catch
            {
                path = path.Replace("file:///", "", StringComparison.OrdinalIgnoreCase)
                           .Replace("file://", "", StringComparison.OrdinalIgnoreCase);
            }
        }

        try { path = Uri.UnescapeDataString(path); } catch { }
        path = path.Replace('/', '\\').Trim();

        // Sometimes a UNC path is pasted with one leading slash: \kupava.by\share.
        // Normalize it to a real UNC path: \\kupava.by\share.
        if (path.StartsWith("\\") && !path.StartsWith("\\\\"))
        {
            var rest = path.TrimStart('\\');
            var firstSep = rest.IndexOf('\\');
            var host = firstSep >= 0 ? rest[..firstSep] : rest;
            if (host.Contains('.')) path = "\\" + path;
        }
        return path;
    }


    private static void OpenExternal(string url)
    {
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); } catch { }
    }

    private sealed class DesktopMessage
    {
        public string? Type { get; set; }
        public int ChatUnread { get; set; }
        public int CallUnread { get; set; }
        public bool Flash { get; set; }
        public string? Path { get; set; }
    }

    private sealed class AppConfig
    {
        public string Server { get; set; } = DefaultServer;
        public string UpdateSource { get; set; } = DefaultUpdateSource;
    }
}
