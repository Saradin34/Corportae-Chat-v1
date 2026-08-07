# Corporate Chat Desktop без Electron/Tauri — Edge App / PWA Shortcut

Этот вариант не требует сборки, Rust, Visual Studio Build Tools и Electron.
Он создаёт ярлык, который открывает рабочий веб-чат в отдельном окне Microsoft Edge:

```text
https://chat.kupava.by
```

Фактически пользователь получает отдельное desktop-окно `Corporate Chat`, а весь функционал работает как в вебе.

## Плюсы

- ничего не нужно собирать;
- очень быстро развернуть;
- нет Electron/Chromium внутри приложения;
- SSO/Kerberos работает так же, как в Edge;
- копирование/вставка и правая кнопка — штатные Edge;
- обновления функционала идут через сервер;
- можно раздать через GPO/logon script.

## Требования

На ПК должен быть Microsoft Edge. На Windows 10/11 он обычно уже есть.

## Ручная установка на ПК

Запустить:

```bat
Install-CorporateChat.bat
```

Или PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-CorporateChat.ps1
```

Будут созданы:

- ярлык на рабочем столе;
- ярлык в меню Пуск.

## Установка для всех пользователей

Нужны права администратора:

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-CorporateChat.ps1 -AllUsers
```

## Без автоматического запуска после установки

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-CorporateChat.ps1 -NoLaunch
```

## Только меню Пуск, без рабочего стола

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-CorporateChat.ps1 -NoDesktopShortcut
```

## Удаление ярлыков

```bat
Uninstall-CorporateChat.bat
```

Или:

```powershell
powershell -ExecutionPolicy Bypass -File .\Uninstall-CorporateChat.ps1
```

## Раздача через GPO

1. Положить папку `desktop-pwa` в сетевую шару, например:

```text
\\server\share\CorporateChatPWA\
```

2. В доменной политике добавить logon script:

```text
User Configuration → Windows Settings → Scripts → Logon
```

3. Указать:

```text
GPO-Logon-Install-CorporateChat.ps1
```

Скрипт создаст ярлык только если его ещё нет.

## Что делает ярлык

Цель ярлыка:

```text
msedge.exe --app=https://chat.kupava.by
```

Важно: отдельный `user-data-dir` не используется. Это сделано специально, чтобы:

- SSO/Kerberos работал как в обычном Edge;
- использовались те же сертификаты/политики;
- сохранялись cookie/разрешения сайта;
- уведомления и микрофон настраивались стандартно через Edge.

## Уведомления

Если уведомления не показываются:

1. Открыть обычный Edge.
2. Перейти на `https://chat.kupava.by`.
3. Разрешить уведомления для сайта.
4. Проверить настройки Windows: Параметры → Система → Уведомления.

## Микрофон

Если нужны звонки/WebRTC, разрешите микрофон для `chat.kupava.by` в Edge.
