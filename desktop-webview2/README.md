# Corporate Chat Lightweight Desktop — .NET WebView2

Лёгкая альтернатива Electron без Rust/Tauri. Это обычное Windows-приложение на .NET WinForms + Microsoft WebView2.

## Почему это проще

- не нужен Electron;
- не нужен Rust;
- не нужен Visual Studio Build Tools / link.exe;
- сборка через .NET SDK;
- использует системный Edge WebView2;
- работает с тем же вебом `https://chat.kupava.by`.

## Требования для сборки

- .NET 8 SDK. Если `dotnet` не найден, `Build-Desktop.ps1` попробует скачать локальный SDK в `.dotnet` без установки в систему.

## Требования на ПК пользователей

- Microsoft Edge WebView2 Runtime. На Windows 10/11 обычно уже есть.
- Если собирать без `-SelfContained`, нужен .NET Desktop Runtime 8.
- Если собирать с `-SelfContained`, .NET Runtime на ПК пользователей не нужен, но размер будет больше.

## Сборка маленькой версии

```powershell
cd D:\53\Corportae-Chat-v1\desktop-webview2
powershell -ExecutionPolicy Bypass -File .\Build-Desktop.ps1
```

Результат:

```text
desktop-webview2\dist\CorporateChat-WebView2\CorporateChat.exe
```

## Сборка автономной версии без .NET Runtime на клиенте

```powershell
powershell -ExecutionPolicy Bypass -File .\Build-Desktop.ps1 -SelfContained
```

## Что работает

- открывает `https://chat.kupava.by`;
- SSO/Kerberos через WebView2;
- WebSocket;
- уведомления/микрофон/камера разрешаются приложением;
- контекстное меню и вставка — штатные WebView2;
- внешние ссылки открываются в браузере;
- меню: смена сервера, перезагрузка, очистка кэша, масштаб, полный экран;
- кнопка [X] сворачивает окно, а не закрывает.

## Раздача

Скопируйте папку:

```text
desktop-webview2\dist\CorporateChat-WebView2\
```

на ПК пользователя и запустите `CorporateChat.exe`.

Можно упаковать ZIP:

```text
desktop-webview2\dist\CorporateChat-WebView2-win-x64.zip
```
