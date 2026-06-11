#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Corporate Chat v2.0 PRO — Установщик / Installer.

Запуск:  python setup.py

Поддерживает два режима:
  1) Docker (рекомендуется) — поднимает postgres, redis, backend, nginx.
  2) Локальный — запускает backend через uvicorn + раздаёт фронтенд.

Скрипт намеренно использует ТОЛЬКО стандартную библиотеку Python,
чтобы работать на любой версии (включая предварительные сборки 3.14),
и не конфликтует с зависимостями backend (они живут в Docker / venv).
"""
from __future__ import annotations

import os
import platform
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_CYAN = "\033[96m"


def supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if platform.system() == "Windows":
        # Modern Windows Terminal / PowerShell support ANSI; enable VT.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            return True
        except Exception:
            return False
    return sys.stdout.isatty()


COLOR = supports_color()


def c(text: str, color: str) -> str:
    return f"{color}{text}{C_RESET}" if COLOR else text


def banner():
    print()
    print(c("╔══════════════════════════════════════════════════════════════╗", C_CYAN))
    print(c("║           Corporate Chat v2.0 PRO — Установщик                ║", C_CYAN))
    print(c("╚══════════════════════════════════════════════════════════════╝", C_CYAN))
    print()


def check_python():
    v = sys.version_info
    print(c(f"✅ Python {v.major}.{v.minor}.{v.micro}", C_GREEN))
    if (v.major, v.minor) >= (3, 14):
        print(c("⚠️  Python 3.14 — предварительная версия. "
                "Некоторые пакеты могут не иметь wheels.", C_YELLOW))
        print("   (Это не повлияет на режим Docker — там используется Python 3.11.)")
        if not ask_yes_no("Продолжить?", default=False):
            print("Отменено.")
            sys.exit(0)
    elif (v.major, v.minor) < (3, 10):
        print(c("✗ Требуется Python 3.10+ для локального режима. "
                "Для Docker это неважно.", C_RED))


def ask_yes_no(question: str, default: bool = False) -> bool:
    suffix = "(y/n) [y]" if default else "(y/n) [n]"
    try:
        ans = input(f"{question} {suffix}: ").strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans in ("y", "yes", "д", "да")


def ask_choice(prompt: str, options: list[str], default: int = 1) -> int:
    print()
    print(prompt)
    for i, opt in enumerate(options, start=1):
        print(f"  {i}. {opt}")
    try:
        ans = input(f"Ваш выбор [{default}]: ").strip()
    except EOFError:
        return default
    if not ans:
        return default
    try:
        n = int(ans)
        if 1 <= n <= len(options):
            return n
    except ValueError:
        pass
    return default


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def docker_compose_cmd() -> list[str] | None:
    """Detect 'docker compose' (v2) or 'docker-compose' (v1)."""
    if which("docker"):
        try:
            r = subprocess.run(["docker", "compose", "version"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                return ["docker", "compose"]
        except Exception:
            pass
    if which("docker-compose"):
        return ["docker-compose"]
    return None


def ensure_env() -> None:
    """Create .env from example if missing, and inject a strong SECRET_KEY."""
    if ENV_FILE.exists():
        print(c("ℹ️  Файл .env уже существует — оставляю как есть.", C_CYAN))
        return
    content = ENV_EXAMPLE.read_text(encoding="utf-8") if ENV_EXAMPLE.exists() else ""
    secret = secrets.token_urlsafe(48)
    if "SECRET_KEY=" in content:
        lines = []
        for line in content.splitlines():
            if line.startswith("SECRET_KEY="):
                lines.append(f"SECRET_KEY={secret}")
            else:
                lines.append(line)
        content = "\n".join(lines) + "\n"
    else:
        content += f"\nSECRET_KEY={secret}\n"
    ENV_FILE.write_text(content, encoding="utf-8")
    print(c("✅ Создан файл .env (со случайным SECRET_KEY).", C_GREEN))


# ---------------------------- Docker mode ----------------------------
def _https_files_ready() -> bool:
    return (ROOT / "nginx" / "certs" / "fullchain.pem").exists() and \
           (ROOT / "nginx" / "certs" / "privkey.pem").exists()


def ensure_https_cert() -> bool:
    """Make sure a TLS cert exists; offer to generate a self-signed one."""
    if _https_files_ready():
        print(c("✅ TLS-сертификат найден (nginx/certs/).", C_GREEN))
        return True
    print(c("\nДля HTTPS нужен сертификат (nginx/certs/fullchain.pem + privkey.pem).", C_YELLOW))
    if not ask_yes_no("Сгенерировать самоподписанный сертификат сейчас?", default=True):
        print("   Положите свои файлы в nginx/certs/ и запустите снова.")
        return False
    name = input("   Имя/адрес сервера (Enter — авто): ").strip()
    args = [sys.executable, str(ROOT / "gen_cert.py")]
    if name:
        args.append(name)
    rc = subprocess.run(args, cwd=ROOT).returncode
    return rc == 0 and _https_files_ready()


def run_docker(https: bool = False):
    compose = docker_compose_cmd()
    if not compose:
        print(c("✗ Docker не найден. Установите Docker Desktop:", C_RED))
        print("   https://www.docker.com/products/docker-desktop/")
        print("   Затем запустите снова: python setup.py")
        sys.exit(1)

    # Verify the daemon is running
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, text=True)
        if r.returncode != 0:
            print(c("✗ Docker установлен, но демон не запущен.", C_RED))
            print("   Запустите Docker Desktop и попробуйте снова.")
            sys.exit(1)
    except Exception:
        print(c("✗ Не удалось обратиться к Docker.", C_RED))
        sys.exit(1)

    # HTTPS overlay: add the second compose file once the cert is in place.
    if https:
        if not ensure_https_cert():
            print(c("✗ Без сертификата HTTPS-режим не запустить.", C_RED))
            sys.exit(1)
        compose = compose + ["-f", "docker-compose.yml", "-f", "docker-compose.https.yml"]

    ensure_env()

    print()
    print(c("🐳 Сборка и запуск контейнеров (postgres, redis, backend, nginx)...", C_CYAN))
    print("   Первая сборка может занять несколько минут.\n")

    # Always rebuild so source fixes are never masked by a stale cached image.
    build = subprocess.run(compose + ["build", "--pull"], cwd=ROOT)
    if build.returncode != 0:
        print(c("✗ Ошибка сборки образов.", C_RED))
        sys.exit(1)

    # --build here too as a belt-and-suspenders against stale images.
    up = subprocess.run(compose + ["up", "-d", "--build"], cwd=ROOT)
    if up.returncode != 0:
        print(c("✗ Ошибка запуска контейнеров. Логи backend:", C_RED))
        dump_backend_logs(compose)
        print(c("\n💡 Подсказка: исправьте причину выше и снова: python setup.py", C_YELLOW))
        sys.exit(1)

    port = read_env_value("HTTP_PORT", "80")
    print()
    print(c("⏳ Ожидание готовности сервисов...", C_CYAN))
    ok = wait_for_health(port, https=https)
    if not ok:
        logs = capture_backend_logs(compose)
        print(logs)
        # Detect the classic stale-volume password mismatch and offer a reset.
        if _is_password_error(logs):
            print(c("\n⚠️  Обнаружена ошибка аутентификации PostgreSQL "
                    "(InvalidPasswordError).", C_YELLOW))
            print("   Причина: том БД был создан ранее с ДРУГИМ паролем.")
            print("   PostgreSQL задаёт пароль только при первой инициализации тома.")
            if ask_yes_no("   Сбросить том БД и пересоздать чисто? "
                          "(данные будут удалены)", default=True):
                print(c("\n🧹 Удаляю том БД и перезапускаю...", C_CYAN))
                subprocess.run(compose + ["down", "-v"], cwd=ROOT)
                subprocess.run(compose + ["up", "-d", "--build"], cwd=ROOT)
                print(c("⏳ Повторное ожидание готовности...", C_CYAN))
                if wait_for_health(port, https=https):
                    _print_success(compose, port, https=https)
                    return
                print(c("\n✗ Всё ещё не готов. Логи:", C_RED))
                dump_backend_logs(compose)
                sys.exit(1)
        print(c("\n✗ Backend не стал готов вовремя. Логи выше.", C_RED))
        print(c("💡 Если видите 'Database not ready ... retry' — БД ещё "
                "инициализируется, подождите и обновите страницу.", C_YELLOW))
        sys.exit(1)

    _print_success(compose, port, https=https)


def _is_password_error(logs: str) -> bool:
    markers = ("InvalidPasswordError", "password authentication failed",
               "InvalidCatalogNameError", "role \"", "does not exist")
    return any(m in logs for m in markers)


def _print_success(compose: list[str], port: str, https: bool = False) -> None:
    print()
    print(c("══════════════════════════════════════════════════════════════", C_GREEN))
    print(c("✅ Corporate Chat запущен!", C_GREEN))
    if https:
        url = "https://localhost"
        print(f"   🔒 Откройте: {c(url, C_BOLD)}  (HTTPS включён)")
        print(c("   ⚠️  Самоподписанный сертификат — браузер предупредит один раз.", C_YELLOW))
        print("   ✅ Теперь уведомления работают и на других ПК (через https://<адрес>).")
    else:
        url = f"http://localhost{'' if port == '80' else ':' + port}"
        print(f"   🌐 Откройте: {c(url, C_BOLD)}")
    print(f"   👤 Админ:    {read_env_value('ADMIN_USERNAME','admin')} / "
          f"{read_env_value('ADMIN_PASSWORD','Admin12345!')}")
    print()
    print("   Полезные команды:")
    print(f"     {' '.join(compose)} logs -f         # логи")
    print(f"     {' '.join(compose)} down            # остановить")
    print(f"     {' '.join(compose)} down -v         # остановить + удалить БД")
    print(f"     {' '.join(compose)} up -d --build   # пересобрать")
    print(c("══════════════════════════════════════════════════════════════", C_GREEN))


def capture_backend_logs(compose: list[str]) -> str:
    """Return backend container logs as text (for inspection + display)."""
    try:
        r = subprocess.run(compose + ["logs", "--no-color", "--tail", "60", "backend"],
                           cwd=ROOT, capture_output=True, text=True)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return f"(не удалось получить логи: {e})"


def dump_backend_logs(compose: list[str]) -> None:
    """Print the backend container logs so the real error is visible."""
    print(capture_backend_logs(compose))


def read_env_value(key: str, default: str) -> str:
    if not ENV_FILE.exists():
        return default
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return default


def wait_for_health(port: str, timeout: int = 150, https: bool = False) -> bool:
    import ssl
    import urllib.request

    if https:
        url = "https://localhost/api/health"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # self-signed cert is expected
    else:
        url = f"http://localhost:{port}/api/health"
        ctx = None

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3, context=ctx) as resp:
                if resp.status == 200:
                    print(c("   ✅ Backend готов (через nginx).", C_GREEN))
                    return True
        except Exception:
            pass
        print("   …ещё не готово, ждём…")
        time.sleep(3)
    return False


# ---------------------------- Local mode ----------------------------
def run_local():
    v = sys.version_info
    if (v.major, v.minor) < (3, 10):
        print(c("✗ Локальный режим требует Python 3.10+.", C_RED))
        sys.exit(1)

    print(c("\n🖥️  Локальный режим", C_CYAN))
    print("Требуются запущенные PostgreSQL и Redis локально, ИЛИ задайте")
    print("переменные DATABASE_URL / REDIS_URL вручную.\n")

    backend = ROOT / "backend"
    venv = backend / ".venv"
    py = sys.executable

    if not venv.exists():
        print("Создаю виртуальное окружение...")
        subprocess.run([py, "-m", "venv", str(venv)], check=True)

    if platform.system() == "Windows":
        vpy = venv / "Scripts" / "python.exe"
    else:
        vpy = venv / "bin" / "python"

    print("Устанавливаю зависимости backend...")
    subprocess.run([str(vpy), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    rc = subprocess.run([str(vpy), "-m", "pip", "install", "-r",
                         str(backend / "requirements.txt")])
    if rc.returncode != 0:
        print(c("✗ Не удалось установить зависимости. Возможно, нет wheels "
                "для вашей версии Python — используйте Docker-режим.", C_RED))
        sys.exit(1)

    ensure_env()

    print()
    print(c("✅ Готово. Запуск backend на http://localhost:8000", C_GREEN))
    print("   Фронтенд раздаётся backend'ом по адресу http://localhost:8000/")
    print("   (Ctrl+C чтобы остановить)\n")

    # In local mode, serve the static frontend through the backend too.
    env = os.environ.copy()
    env.setdefault("SERVE_STATIC", "1")
    subprocess.run(
        [str(vpy), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0",
         "--port", "8000"],
        cwd=str(backend), env=env,
    )


def run_desktop_build():
    """Build the Electron desktop installer for the current OS."""
    desktop = ROOT / "desktop"
    if not desktop.exists():
        print(c("✗ Папка desktop/ не найдена.", C_RED))
        sys.exit(1)
    npm = which("npm")
    if not npm:
        print(c("✗ Не найден Node.js / npm. Установите с https://nodejs.org/", C_RED))
        sys.exit(1)

    print(c("\n🖥️  Сборка десктоп-приложения (Electron)…", C_CYAN))
    print("Устанавливаю зависимости (это может занять пару минут)…\n")
    rc = subprocess.run([npm, "install"], cwd=desktop)
    if rc.returncode != 0:
        print(c("✗ Ошибка npm install.", C_RED))
        sys.exit(1)

    system = platform.system()

    # electron-builder must not try to download/extract the code-signing
    # toolkit (winCodeSign) — it contains macOS symlinks that Windows refuses
    # to create without admin / Developer Mode. Disabling auto-signing avoids it.
    env = os.environ.copy()
    env["CSC_IDENTITY_AUTO_DISCOVERY"] = "false"

    if system == "Windows":
        choice = ask_choice(
            "Тип сборки для Windows:",
            [
                "📦 Установщик (.exe, NSIS) — нужен Developer Mode или админ",
                "🎒 Портативный (.exe, без установки) — БЕЗ прав админа (рекомендуется)",
                "📁 Папка (dir, распакованное приложение) — самый надёжный, без прав",
            ],
            default=2,
        )
        target = {1: "dist:win", 2: "dist:win:portable", 3: "dist:win:dir"}[choice]
        fallbacks = ["dist:win:portable", "dist:win:dir"]
    else:
        target = {"Darwin": "dist:mac", "Linux": "dist:linux"}.get(system, "dist")
        fallbacks = []

    def build(t):
        print(c(f"\nСобираю ({t})…\n", C_CYAN))
        return subprocess.run([npm, "run", t], cwd=desktop, env=env).returncode == 0

    ok = build(target)
    if not ok:
        for fb in fallbacks:
            if fb == target:
                continue
            print(c(f"\n⚠️  Сборка не удалась. Пробую вариант без прав администратора: {fb}…", C_YELLOW))
            if build(fb):
                ok = True
                target = fb
                break

    if not ok:
        print(c("\n✗ Сборку завершить не удалось.", C_RED))
        print(c("Скорее всего нужна одна из мер ниже:", C_YELLOW))
        print("  1) Включите Режим разработчика Windows (Параметры → Конфиденциальность")
        print("     и безопасность → Для разработчиков → Режим разработчика),")
        print("     либо запустите PowerShell от имени администратора, затем повторите.")
        print("  2) Либо выберите вариант «Портативный» или «Папка» — им права не нужны.")
        print("  Подробности: desktop/README.md → раздел «Сборка без прав администратора».")
        sys.exit(1)

    out = desktop / "dist"
    print()
    print(c("══════════════════════════════════════════════════════════════", C_GREEN))
    print(c("✅ Готово!", C_GREEN))
    print(f"   📦 Файлы: {out}")
    if target == "dist:win:dir":
        print("   Это распакованная папка win-unpacked — запускать 'Corporate Chat.exe'")
        print("   внутри неё. Можно скопировать папку на любой ПК.")
    elif target == "dist:win:portable":
        print("   Это один портативный .exe — запускается без установки и прав админа.")
    else:
        print("   Запустите установщик и при первом старте укажите адрес сервера.")
    print(c("══════════════════════════════════════════════════════════════", C_GREEN))


def main():
    banner()
    check_python()

    mode = ask_choice(
        "Выберите режим установки:",
        [
            "🐳 Docker (HTTP) — сервер, быстрый старт",
            "🔒 Docker (HTTPS) — сервер с сертификатом (уведомления на всех ПК)",
            "🖥️  Локальный — сервер без Docker",
            "💻 Десктоп-приложение (установщик для ПК)",
        ],
        default=1,
    )
    if mode == 1:
        run_docker(https=False)
    elif mode == 2:
        run_docker(https=True)
    elif mode == 3:
        run_local()
    else:
        run_desktop_build()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nПрервано пользователем.")
        sys.exit(130)
