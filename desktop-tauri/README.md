# Corporate Chat — лёгкий desktop на Tauri/WebView2

Это замена Electron-обёртки. Использует системный Microsoft Edge WebView2, поэтому установщик и потребление памяти обычно намного меньше.

## Что сохраняется

- открывает `https://chat.kupava.by`;
- работает с тем же backend/frontend;
- Kerberos/SSO работает через системный WebView2/Windows, как в Edge;
- контекстное меню, выделение, копирование/вставка обеспечиваются WebView2;
- кнопка [X] сворачивает окно, а не закрывает приложение.

## Требования для сборки на Windows

1. Node.js 18+
2. Rust: https://rustup.rs/
3. Microsoft Edge WebView2 Runtime на клиентских ПК. На Windows 10/11 обычно уже установлен.

## Сборка установщика

```powershell
cd D:\49\Corportae-Chat-v1\desktop-tauri
npm install
npm run build:win
```

Готовый установщик будет в:

```text
src-tauri\target\release\bundle\nsis\
```

## Если нужен MSI

```powershell
npm run build:msi
```

## Примечание

Если у части ПК нет WebView2 Runtime, установите его через доменную политику или добавьте WebView2 Evergreen Runtime в образ Windows.
