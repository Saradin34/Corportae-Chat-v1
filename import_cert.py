#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Импорт ВАШЕГО TLS-сертификата и приватного ключа в Corporate Chat (HTTPS).

Берёт два ваших PEM-файла и кладёт их под именами, которые ждёт nginx:
    nginx/certs/fullchain.pem   <- сертификат   (BEGIN CERTIFICATE …)
    nginx/certs/privkey.pem     <- приватный ключ (BEGIN PRIVATE KEY …)

Перед записью проверяет:
  • что это действительно PEM-сертификат и PEM-ключ;
  • что ключ СООТВЕТСТВУЕТ сертификату (частая причина «битого» HTTPS);
  • срок действия сертификата;
  • наличие цепочки (intermediate) — предупреждает, если её нет.

Запуск (примеры):
    python import_cert.py cret.txt private.txt
    python import_cert.py C:\\path\\cret.txt C:\\path\\private.txt
    python import_cert.py            # спросит пути интерактивно

Можно также указать файл цепочки (intermediate CA) третьим аргументом:
    python import_cert.py cret.txt private.txt chain.txt
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CERT_DIR = ROOT / "nginx" / "certs"


def ensure_cryptography() -> bool:
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        import subprocess
        print("Устанавливаю пакет 'cryptography' для проверки сертификата…")
        return subprocess.run([sys.executable, "-m", "pip", "install", "cryptography"]).returncode == 0


def read_text(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        print(f"✗ Файл не найден: {p}")
        sys.exit(1)
    # tolerate UTF-8 BOM / Windows line endings
    return p.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip() + "\n"


def main():
    args = sys.argv[1:]
    if len(args) >= 2:
        cert_path, key_path = args[0], args[1]
        chain_path = args[2] if len(args) >= 3 else None
    else:
        print("Укажите пути к вашим файлам (Enter — значения по умолчанию).")
        cert_path = input("  Файл сертификата [cret.txt]: ").strip() or "cret.txt"
        key_path = input("  Файл приватного ключа [private.txt]: ").strip() or "private.txt"
        chain_path = input("  Файл цепочки (intermediate), если есть [пусто]: ").strip() or None

    cert_pem = read_text(cert_path)
    key_pem = read_text(key_path)
    chain_pem = read_text(chain_path) if chain_path else ""

    # ---- basic PEM sanity ----
    if "BEGIN CERTIFICATE" not in cert_pem:
        print("✗ В файле сертификата нет 'BEGIN CERTIFICATE'. Это точно сертификат?")
        sys.exit(1)
    if "BEGIN" not in key_pem or "PRIVATE KEY" not in key_pem:
        print("✗ В файле ключа нет 'BEGIN … PRIVATE KEY'. Это точно приватный ключ?")
        sys.exit(1)
    if "BEGIN CERTIFICATE" in key_pem:
        print("✗ Похоже, вы перепутали файлы местами (в ключе лежит сертификат).")
        print("  Порядок аргументов: сначала сертификат, потом ключ.")
        sys.exit(1)

    # ---- cryptographic validation ----
    if ensure_cryptography():
        from datetime import datetime, timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding, ec

        try:
            cert = x509.load_pem_x509_certificate(cert_pem.encode())
        except Exception as e:
            print(f"✗ Не удалось прочитать сертификат: {e}")
            sys.exit(1)
        try:
            key = serialization.load_pem_private_key(key_pem.encode(), password=None)
        except TypeError:
            print("✗ Приватный ключ зашифрован паролем. Снимите пароль:")
            print("    openssl rsa -in private.txt -out private_nopass.pem")
            sys.exit(1)
        except Exception as e:
            print(f"✗ Не удалось прочитать приватный ключ: {e}")
            sys.exit(1)

        # key matches certificate?
        cert_pub = cert.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        key_pub = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        if cert_pub != key_pub:
            print("✗ КЛЮЧ НЕ СООТВЕТСТВУЕТ СЕРТИФИКАТУ.")
            print("  Это разные пары. Возьмите ключ, которым выпускался ИМЕННО этот сертификат.")
            sys.exit(1)
        print("✅ Ключ соответствует сертификату.")

        # validity dates
        try:
            nb = cert.not_valid_before_utc
            na = cert.not_valid_after_utc
        except AttributeError:  # older cryptography
            nb = cert.not_valid_before.replace(tzinfo=timezone.utc)
            na = cert.not_valid_after.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now < nb:
            print(f"⚠️  Сертификат ещё не действует (с {nb}).")
        elif now > na:
            print(f"✗ Срок действия сертификата ИСТЁК ({na}). Нужен актуальный.")
            sys.exit(1)
        else:
            print(f"✅ Срок действия: до {na:%Y-%m-%d}.")

        # show subject / SAN so the user can confirm it's for the right host
        try:
            cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
            if cn:
                print(f"   Субъект (CN): {cn[0].value}")
        except Exception:
            pass
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            names = [str(g.value) for g in san]
            print(f"   Действителен для (SAN): {', '.join(names)}")
        except Exception:
            print("   ⚠️  В сертификате нет SAN. Современные браузеры требуют SAN —")
            print("       по одному только CN могут показывать предупреждение.")
    else:
        print("⚠️  Пакет cryptography не установлен — пропускаю проверку соответствия.")

    # ---- chain handling ----
    # fullchain.pem = leaf cert + (optional) intermediate chain
    full = cert_pem
    if chain_pem and "BEGIN CERTIFICATE" in chain_pem:
        if not full.endswith("\n"):
            full += "\n"
        full += chain_pem
        print("✅ Добавлена цепочка (intermediate).")
    else:
        count = cert_pem.count("BEGIN CERTIFICATE")
        if count < 2:
            print("ℹ️  В сертификате только один блок (без промежуточного УЦ).")
            print("    Для внутренней сети обычно ОК. Если браузер ругается на цепочку —")
            print("    добавьте intermediate третьим аргументом или допишите его в cret.txt.")

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    (CERT_DIR / "fullchain.pem").write_text(full, encoding="utf-8")
    (CERT_DIR / "privkey.pem").write_text(key_pem, encoding="utf-8")

    print("\n✅ Готово! Файлы установлены:")
    print(f"   {CERT_DIR / 'fullchain.pem'}")
    print(f"   {CERT_DIR / 'privkey.pem'}")
    print("\nЗапустите HTTPS-режим:")
    print("   docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build")
    print("\nЗатем открывайте https://<адрес-сервера> со всех ПК.")


if __name__ == "__main__":
    main()
