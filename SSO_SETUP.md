# Настройка SSO (NTLM / Kerberos / Reverse Proxy) — Corporate Chat v2.0

В Corporate Chat реализовано два способа единого входа (SSO):

1. **Reverse-Proxy SSO** — самый надёжный для production.  
   nginx / IIS / Apache с `mod_auth_kerb` проверяет Kerberos/NTLM-тикет и передаёт имя пользователя в заголовке `X-Remote-User`.  
   Backend читает этот заголовок — keytab **не нужен** в контейнере.

2. **Direct SPNEGO (Negotiate)** — backend сам обрабатывает рукопожатие NTLM/Kerberos.  
   На Linux требуется **keytab** (`krb5.keytab`) и переменная `SSO_SERVICE_NAME`.  
   На Windows (SSPI) keytab **не нужен**, но в Docker-контейнере (Alpine Linux) он **нужен**.

---

## 1. Быстрый старт на Windows + Docker Desktop

### 1.1. Подготовьте файлы

В корневой папке проекта (`D:\...\Corporate-chat-v1`) должны лежать:

```
Corporate-chat-v1\
  docker-compose.yml
  .env
  krb5.keytab          <-- ваш keytab (если есть)
  krb5.conf            <-- конфиг Kerberos (уже в проекте)
  start.ps1            <-- скрипт запуска (уже в проекте)
```

> Если у вас **нет keytab** — SSO Negotiate работать не будет. Но reverse-proxy SSO и LDAP-вход будут работать.

### 1.2. Запуск

```powershell
cd D:\...\Corporate-chat-v1
.\start.ps1
```

Скрипт автоматически:
- Остановит старые контейнеры.
- Соберёт и запустит новые.
- **Проверит**, что `krb5.keytab` и `krb5.conf` попали внутрь контейнера `cc_backend`.
- Если Docker Desktop не смонтировал файлы (частая проблема на Windows с WSL2), скрипт скопирует их через `docker cp` и перезапустит backend.
- Дождётся `http://localhost/api/health` (200 OK).

### 1.3. Проверка внутри контейнера

```powershell
docker exec cc_backend ls -la /etc/krb5.keytab
docker exec cc_backend ls -la /etc/krb5.conf
```

Оба файла должны быть видны. Если `ls` показывает `No such file` — запустите `.\start.ps1` ещё раз.

### 1.4. Проверка в браузере

Откройте `http://localhost` → **Ctrl+Shift+R** → на странице логина должна появиться кнопка **«Вход через Windows (SSO)»**.

---

## 2. Reverse-Proxy SSO (рекомендуется для production)

### nginx + `mod_auth_kerb` (Linux)

```nginx
server {
    listen 80;
    server_name chat.kupava.by;

    location / {
        auth_gss on;
        auth_gss_keytab /etc/krb5.keytab;
        proxy_pass http://backend:8000;
        proxy_set_header X-Remote-User $remote_user;
        proxy_hide_header WWW-Authenticate;
    }
}
```

### IIS (Windows)

1. Включите **Windows Authentication** для сайта.
2. Отключите **Anonymous Authentication**.
3. URL Rewrite автоматически прокинет `X-Remote-User`.

Backend читает заголовки: `remote-user`, `x-remote-user`, `x-webauth-user`, `ad_login`, `x-ms-client-principal-name`.

---

## 3. Direct SPNEGO (без reverse proxy)

### 3.1. Создание keytab (на Windows Server с AD)

```powershell
# PowerShell (от имени администратора на DC)
setspn -A HTTP/chat.kupava.by svc_chat_account

ktpass /princ HTTP/chat.kupava.by@KUPAVA.BY `
  /mapuser svc_chat_account@kupava.by `
  /crypto ALL `
  /ptype KRB5_NT_PRINCIPAL `
  /pass YourStrongPassword `
  /out krb5.keytab
```

### 3.2. Настройка `.env`

```env
SSO_ENABLED=true
SSO_ALLOW_PROXY=true
SSO_ALLOW_NEGOTIATE=true
SSO_SERVICE_NAME=HTTP/chat.kupava.by@KUPAVA.BY
SSO_KEYTAB_PATH=/etc/krb5.keytab
```

### 3.3. Настройка клиентского ПК (Windows)

1. **Internet Options** (`inetcpl.cpl`) → вкладка **Security** → **Local Intranet** → **Sites** → **Advanced**.
2. Добавьте `http://chat.kupava.by` (или `https://chat.kupava.by`).
3. Вкладка **Advanced** → внизу раздел **Security** → ✅ **Enable Integrated Windows Authentication**.
4. Перезапустите браузер (Chrome/Edge).

> Без этих настроек браузер **не отправит** Kerberos/NTLM тикет на `GET /api/auth/sso`, и вы будете видеть бесконечные `401` в DevTools.

---

## 4. Частые ошибки

| Ошибка | Причина | Решение |
|--------|---------|---------|
| `krb5.keytab: No such file` в контейнере | Docker Desktop не смонтировал файл из D:\ | Запустите `.\start.ps1` — он сделает `docker cp` |
| Кнопка SSO не появляется | `SSO_ENABLED=false` в `.env` или nginx отдаёт старый `index.html` | Проверьте `.env` → `SSO_ENABLED=true`; нажмите Ctrl+Shift+R |
| `500` на `/api/chats` | Старая БД без колонки `group_id` | `docker compose down -v && docker compose up -d --build` (сотрёт сообщения!) |
| `SSO: no ticket yet (401)` | Браузер не отправляет Negotiate-тикет | Добавьте сайт в **Local Intranet** + включите **Integrated Windows Authentication** |
| `Conflict. The container name ... already in use` | Старый контейнер завис | `docker rm -f cc_redis cc_db cc_backend cc_nginx` |
| `unsupported hash type MD4` | Alpine Linux + OpenSSL 3.0 не поддерживает NTLM-MD4 | Используйте вход `user@domain` (UPN) вместо `DOMAIN\user` |

---

## 5. Проверка через DevTools (F12)

1. Откройте страницу логина → **F12** → вкладка **Network**.
2. Кликните **«Вход через Windows (SSO)»**.
3. Вы должны увидеть **два** запроса к `/api/auth/sso`:
   - Первый: `401 Unauthorized` + заголовок `WWW-Authenticate: Negotiate`
   - Второй: `200 OK` + заголовок `Authorization: Negotiate <длинный токен>`

Если второго запроса нет — браузер не считает сайт внутренним (см. раздел 3.3).
