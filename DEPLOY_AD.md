# Развёртывание Corporate Chat на сервере организации (вход через Active Directory)

Сценарий: **внутренний Linux-сервер + Docker**, аутентификация по **доменному
логину/паролю** (on-prem Active Directory). Это уже встроено — нужно только
настроить и развернуть.

---

## 0. Что понадобится (чек-лист для администратора)

| Что | Зачем | Пример |
|---|---|---|
| Linux-сервер с Docker + Docker Compose | хостинг приложения | Ubuntu 22.04, 2 vCPU / 4 ГБ |
| Сетевой доступ сервер → контроллеры домена | проверка паролей | TCP 636 (LDAPS) или 389 (LDAP) |
| DNS-имена контроллеров домена | подключение к AD | `dc1.company.local` |
| Base DN домена | поиск пользователей | `DC=company,DC=local` |
| NetBIOS-имя домена | вход `DOMAIN\user` | `COMPANY` |
| (реком.) Сервисная учётка AD | чтение профилей/групп | `svc_chat` |
| AD-группа админов приложения | роль «администратор» | `CN=ChatAdmins,OU=Groups,...` |
| (реком.) TLS-сертификат на DNS-имя сервиса | HTTPS | `chat.company.local` |

---

## 1. Установка Docker (если ещё нет)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # перелогиньтесь после этого
```

## 2. Скопировать проект на сервер

Залейте папку `corporate-chat/` на сервер (git, scp, rsync — как удобно).

```bash
cd corporate-chat
```

## 3. Настроить окружение под ваш AD

```bash
cp .env.production.example .env
nano .env      # подставьте значения вашей организации
```

Минимум, что нужно заполнить:

```env
SECRET_KEY=<openssl rand -base64 48>
POSTGRES_PASSWORD=<длинный пароль>
ADMIN_PASSWORD=<сильный пароль аварийного админа>

LDAP_ENABLED=true
LDAP_SERVERS=ldaps://dc1.company.local:636,ldaps://dc2.company.local:636
LDAP_USE_SSL=true
LDAP_BASE_DN=DC=company,DC=local
LDAP_DOMAIN=company.local
LDAP_NETBIOS=COMPANY
LDAP_BIND_DN=CN=svc_chat,OU=Service Accounts,DC=company,DC=local
LDAP_BIND_PASSWORD=<пароль сервисной учётки>
LDAP_ADMIN_GROUP=CN=ChatAdmins,OU=Groups,DC=company,DC=local
```

> Узнать свои значения:
> - Base DN / DN группы: оснастка **«Active Directory — пользователи и
>   компьютеры»** → View → Advanced Features → свойства объекта → вкладка
>   **Attribute Editor** → `distinguishedName`.
> - NetBIOS-имя: на доменной машине `echo %USERDOMAIN%`.
> - Проверить связь с DC: `nc -vz dc1.company.local 636`.

## 4а. Запуск по HTTP (быстрый старт / тест во внутренней сети)

```bash
python setup.py        # пункт 1 (Docker)
# или вручную:
docker compose up -d --build
```
Откройте `http://<ip-сервера>/`.

> ⚠️ По HTTP доменные пароли идут в открытом виде между браузером и сервером.
> Это допустимо только во внутренней доверенной сети для теста. Для боевого
> использования включите HTTPS (шаг 4б).

## 4б. Запуск по HTTPS (ОБЯЗАТЕЛЬНО для уведомлений на других ПК)

> ⚠️ Браузерные уведомления (и доступ к ним на сторонних ПК) работают только
> в «защищённом контексте» — это **HTTPS** или `localhost`. Поэтому на машине,
> где вы запустили сервер, уведомления есть (localhost), а на подключающихся
> по `http://<ip>` — нет. Включите HTTPS — и уведомления заработают везде.

**Самый простой путь — через установщик:**
```bash
python setup.py     # пункт 2: «🔒 Docker (HTTPS)»
```
Он сам предложит сгенерировать самоподписанный сертификат и поднимет HTTPS.

**Вариант А — у вас УЖЕ есть сертификат и ключ** (например, `cret.txt` и
`private.txt` от вашего УЦ). Импортируйте их одной командой — скрипт проверит,
что ключ подходит к сертификату, и положит файлы куда надо:
```bash
python import_cert.py cret.txt private.txt
# если есть отдельный файл цепочки (intermediate):
python import_cert.py cret.txt private.txt chain.txt
```

**Вариант Б — сгенерировать самоподписанный** (с правильными SAN — именем и IP,
чтобы подходил и для подключающихся ПК):
```bash
python gen_cert.py chat.company.local 192.168.1.50
```
В обоих случаях появятся `nginx/certs/fullchain.pem` и `nginx/certs/privkey.pem`.

2. Поднимите с HTTPS-оверлеем:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build
   ```
   Откройте `https://chat.company.local/`. HTTP автоматически редиректит на HTTPS.

> Самоподписанный сертификат вызовет предупреждение браузера один раз. Чтобы
> убрать его на всех ПК — добавьте `fullchain.pem` в доверенные корневые
> сертификаты (например, через групповые политики Active Directory / GPO).
> Идеально — выдать сертификат вашим корпоративным УЦ на DNS-имя сервиса.

### Если браузер пишет ERR_CONNECTION_REFUSED на https://localhost

Сделайте чистый перезапуск (важно — с `down`, чтобы пересоздать контейнер
nginx с новыми портами):
```bash
docker compose -f docker-compose.yml -f docker-compose.https.yml down
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build
```
Проверка, что nginx слушает 443 и не падает:
```bash
docker compose -f docker-compose.yml -f docker-compose.https.yml ps
docker compose -f docker-compose.yml -f docker-compose.https.yml logs --tail 30 nginx
# должно быть: контейнер cc_nginx в статусе Up, порты 0.0.0.0:80->80, 0.0.0.0:443->443
```
Если в логах nginx «address already in use» на :80 или :443 — этот порт занят
другой программой (старый контейнер, IIS, Skype). Освободите его или поменяйте
внешний порт.

## 5. Первый вход

- **Доменный пользователь:** логин `ivanov`, `COMPANY\ivanov` или
  `ivanov@company.local` + доменный пароль.
- **Аварийный админ:** `admin` + `ADMIN_PASSWORD` (работает даже при недоступном AD).
- Члены `LDAP_ADMIN_GROUP` автоматически получают роль администратора.

## 6. Десктоп-приложение для сотрудников (опционально)

Соберите установщик и раздайте сотрудникам — при первом запуске он спросит
адрес сервера (`https://chat.company.local`):

```bash
cd desktop && npm install && npm run dist:win   # .exe в desktop/dist/
```

---

## Как это работает (для безопасников)

1. Пользователь вводит доменный логин/пароль в окне входа.
2. Backend выполняет **bind** к контроллеру домена этими учётными данными —
   успешный bind означает верный пароль. **Пароль нигде не хранится.**
3. Из AD читаются `displayName`, `mail`, `memberOf` (членство в группах).
4. Если задан `LDAP_REQUIRED_GROUP` — проверяется доступ (allow-list).
5. Пользователь создаётся при первом входе (JIT), роль = admin, если он в
   `LDAP_ADMIN_GROUP`. Группы/отделы зеркалируются в групповые чаты.
6. Выдаётся обычный JWT для сессии в приложении.

Особенности безопасности:
- Поддержка **LDAPS (636)** и **StartTLS** — пароли к AD идут шифрованно.
- Экранирование LDAP-фильтров (защита от LDAP-инъекций).
- Несколько контроллеров домена (отказоустойчивость).
- Сервисная учётка может быть с минимальными правами (только чтение).
- Рекомендуется HTTPS между браузером и сервером (шаг 4б).

---

## Эксплуатация

```bash
docker compose ps                 # статус сервисов
docker compose logs -f backend    # логи бэкенда (видно процесс LDAP-входа)
docker compose down               # остановить
docker compose up -d --build      # обновить/перезапустить

# Резервная копия БД:
docker compose exec db pg_dump -U chat corporate_chat > backup_$(date +%F).sql

# Восстановление:
cat backup.sql | docker compose exec -T db psql -U chat corporate_chat
```

## Диагностика входа через AD

```bash
# 1) Доступность контроллера домена с сервера:
nc -vz dc1.company.local 636        # LDAPS
nc -vz dc1.company.local 389        # LDAP

# 2) Проверить bind вручную (если установлен ldap-utils):
ldapsearch -H ldaps://dc1.company.local -D "ivanov@company.local" -w '<пароль>' \
  -b "DC=company,DC=local" "(sAMAccountName=ivanov)" dn

# 3) Смотреть, что говорит бэкенд при попытке входа:
docker compose logs -f backend
```

Типичные проблемы:
- **«Сервер Active Directory недоступен»** → сеть/файрвол до DC, неверный
  `LDAP_SERVERS` или порт. Проверьте `nc -vz`.
- **Вход не проходит при верном пароле** → проверьте `LDAP_DOMAIN` / `LDAP_NETBIOS`
  и формат логина; включите `ldap://...:389` временно для проверки.
- **Сертификат LDAPS не доверенный** → добавьте корневой сертификат вашего УЦ
  (для строгой проверки) или используйте StartTLS; в крайнем случае на время
  отладки `LDAP_USE_SSL=false` во внутренней сети.
