# Исправление SSO / Active Directory — что было сломано и что сделано

Дата: 2026-06-10. Домен: `kupava.by` / `KUPAVA.BY`, DC: `forseti.kupava.by`.

## TL;DR
Главная причина, по которой «не работал вход через AD», — **баг в коде**, а не
в твоём `.env`. Любая попытка логина падала с `AttributeError`, который в логах
превращался в `503 — Ошибка аутентификации AD`. Плюс был сломан Kerberos-acceptor
для SSO Negotiate. Всё исправлено.

---

## 1. 🔴 Критический баг: падал КАЖДЫЙ вход по AD

**Файл:** `backend/app/ldap_auth.py` → `_make_connection()`

Код обращался к `settings.LDAP_AUTH_MECHANISM`, но этого параметра **не было**
в `backend/app/config.py`. Результат — `AttributeError` на каждом `POST /api/auth/login`.
Ошибку «съедал» широкий `except Exception` в `routers/auth.py`, и пользователь
видел `503`.

Вдобавок прежняя «заплатка» при `NEGOTIATE` ставила механизм `KERBEROS` (SASL/GSSAPI).
Это **неправильно** для проверки пароля: GSSAPI-bind использует тикет-кэш сервера,
а не пароль, введённый пользователем, — то есть проверить логин/пароль так нельзя.

**Исправлено:**
- Добавлен параметр `LDAP_AUTH_MECHANISM` в `config.py` (`""` | `SIMPLE` | `NTLM` | `KERBEROS`).
- Логика выбора bind переписана (`_pick_auth_mechanism`):
  - `SIMPLE` → bind как `user@kupava.by` (рекомендуется для AD);
  - `NTLM` → bind как `KUPAVA\user`;
  - пусто → авто-выбор.
- Проброшен в `docker-compose.yml`.

## 2. 🟠 SSO Negotiate: неверное имя acceptor

**Файл:** `backend/app/sso_auth.py` → `_new_server_context()`

В `spnego.server(hostname=...)` передавался **полный SPN**
`HTTP/chat.kupava.by@KUPAVA.BY`, тогда как нужен только FQDN `chat.kupava.by`.
Из-за этого Kerberos-рукопожатие не совпадало с принципалом в keytab и браузер
бесконечно получал `401`.

**Исправлено:** добавлена `_spn_to_hostname()`, которая вырезает `HTTP/` и `@REALM`.
`SSO_SERVICE_NAME` в `.env` можно оставлять полным SPN — код сам приведёт его к FQDN.

## 3. 🟠 `LDAP_GROUP_FILTER` был в неправильном формате

В коде (`ldap_auth.selectable_groups`) этот параметр — **список подстрок через
запятую**, которые ищутся в DN группы, а НЕ LDAP-фильтр. У тебя стояло
`(&(|(objectclass=group))(description=*)...)` — так оно не сработает.

**Исправлено в `.env`:** `LDAP_GROUP_FILTER=OU=Groups` (зеркалить только группы из OU=Groups).
Поставь пусто, если нужно зеркалить все группы пользователя.

## 4. 🟡 `LDAP_BASE_DN` был слишком узким

Было: `OU=MAZ-Kupava,DC=kupava,DC=by`. Поиск пользователей идёт от этого DN —
это ок для юзеров, но безопаснее искать от корня домена, чтобы гарантированно
находить и пользователей, и членов групп (особенно при вложенных группах).

**Исправлено в `.env`:** `LDAP_BASE_DN=DC=kupava,DC=by`.

---

## Как применить и проверить

```bash
cp .env .env            # уже создан исправленный .env в корне
# при необходимости поправь POSTGRES_PASSWORD и SECRET_KEY:
#   openssl rand -base64 48

docker compose down
docker compose up -d --build
docker compose logs -f backend     # смотри ошибки старта
```

### Проверка 1 — обычный вход по AD (без SSO)
На странице логина введи `ivanov` (или `ivanov@kupava.by`) и доменный пароль.
В логах backend должно появиться: `LDAP bind OK for ivanov@kupava.by`.

Если видишь `Сервер Active Directory недоступен (503)` — это сеть/сервисная учётка,
а не пароль. Проверь доступность DC из контейнера:

```bash
docker exec cc_backend python - <<'PY'
from ldap3 import Server, Connection, ALL, SIMPLE
s = Server("forseti.kupava.by", port=389, use_ssl=False, get_info=ALL, connect_timeout=8)
# сервисная учётка
c = Connection(s, user="CN=testopenfire,OU=Services,OU=Users,OU=MAZ-Kupava,DC=kupava,DC=by",
               password="123qweASD", authentication=SIMPLE)
print("service bind:", c.bind(), c.result)
# проверка пароля пользователя через UPN
u = Connection(s, user="ИМЯ_ПОЛЬЗОВАТЕЛЯ@kupava.by", password="ПАРОЛЬ", authentication=SIMPLE)
print("user bind:", u.bind(), u.result)
PY
```

### Проверка 2 — SSO (Negotiate) через keytab
```bash
docker exec cc_backend ls -la /etc/krb5.keytab     # должен существовать, 600
docker exec cc_backend sh -c 'klist -k /etc/krb5.keytab 2>/dev/null || true'
# принципал в keytab: HTTP/chat.kupava.by@KUPAVA.BY  (kvno=4)
```
Затем в браузере (Chrome/Edge): добавь `http://chat.kupava.by` в
**Local Intranet** и включи **Integrated Windows Authentication**
(см. `SSO_SETUP.md`, раздел 3.3). Открой `http://chat.kupava.by` → кнопка
**«Вход через Windows (SSO)»** → в DevTools должно быть два запроса к
`/api/auth/sso`: `401 Negotiate` → `200 OK`.

> ВАЖНО для keytab: SPN в keytab — `HTTP/chat.kupava.by`. Значит браузер
> должен открывать сайт ИМЕННО по имени **chat.kupava.by** (не по IP и не
> по localhost), иначе Kerberos-тикет не выпишется. DNS-запись `chat.kupava.by`
> должна указывать на хост с этим приложением, а SPN — быть привязан к той
> учётке, из которой сгенерирован keytab (`ktpass /mapuser ...`).

### Проверка 3 — Reverse-proxy SSO (самый надёжный)
Если keytab/Negotiate капризничает, используй nginx с `auth_gss` (mod_auth_kerb),
который кладёт `X-Remote-User`. Backend это уже умеет (`SSO_ALLOW_PROXY=true`).
См. `SSO_SETUP.md`, раздел 2.

---

## Изменённые файлы
- `backend/app/config.py` — добавлен `LDAP_AUTH_MECHANISM`.
- `backend/app/ldap_auth.py` — исправлен выбор bind-механизма (убран сломанный KERBEROS-путь).
- `backend/app/sso_auth.py` — добавлена `_spn_to_hostname()`, исправлен acceptor для SPNEGO.
- `docker-compose.yml` — проброшен `LDAP_AUTH_MECHANISM`.
- `.env` — создан исправленный конфиг (формат GROUP_FILTER, BASE_DN, механизм bind).
