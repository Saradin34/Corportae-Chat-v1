#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор самоподписанного TLS-сертификата для Corporate Chat (HTTPS).

Кладёт файлы в nginx/certs/fullchain.pem и nginx/certs/privkey.pem.
Сертификат включает SAN (имена/IP), чтобы он подходил и для других ПК,
которые подключаются к серверу по IP или DNS-имени.

Запуск:
    python gen_cert.py                      # авто-определит имя хоста и IP
    python gen_cert.py chat.company.local   # явное имя
    python gen_cert.py chat.company.local 192.168.1.50   # имя + IP

Использует пакет `cryptography` (ставится автоматически при необходимости),
поэтому openssl в PATH не требуется.
"""
import ipaddress
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CERT_DIR = ROOT / "nginx" / "certs"


def ensure_cryptography():
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        print("Устанавливаю пакет 'cryptography'…")
        rc = subprocess.run([sys.executable, "-m", "pip", "install", "cryptography"]).returncode
        return rc == 0


def local_ips():
    ips = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ":" not in ip:  # skip IPv6 for simplicity
                ips.add(ip)
    except Exception:
        pass
    # also the primary outbound IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    ips.discard("127.0.0.1")
    return sorted(ips)


def main():
    args = sys.argv[1:]
    primary = args[0] if args else socket.gethostname()
    extra_ips = args[1:]

    if not ensure_cryptography():
        print("✗ Не удалось установить 'cryptography'. Установите вручную: pip install cryptography")
        sys.exit(1)

    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    # Build SAN list: hostname, localhost, all detected IPs, plus any extra.
    dns_names = {primary, "localhost"}
    ip_addrs = set(["127.0.0.1"]) | set(local_ips()) | set(extra_ips)

    san = []
    for name in dns_names:
        if name:
            san.append(x509.DNSName(name))
    for ip in ip_addrs:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            # not an IP -> treat as DNS name
            san.append(x509.DNSName(ip))

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, primary),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Corporate Chat"),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=825))  # ~2.25 года
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    (CERT_DIR / "privkey.pem").write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    (CERT_DIR / "fullchain.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print("\n✅ Сертификат создан:")
    print(f"   {CERT_DIR / 'fullchain.pem'}")
    print(f"   {CERT_DIR / 'privkey.pem'}")
    print("\n   Включён для имён/адресов (SAN):")
    for name in sorted(dns_names):
        print(f"     DNS: {name}")
    for ip in sorted(ip_addrs):
        print(f"     IP:  {ip}")
    print("\nДалее запустите HTTPS-режим:")
    print("   docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build")
    print("\n⚠️  Это самоподписанный сертификат — браузер покажет предупреждение,")
    print("    пока вы не добавите его в доверенные (можно раскатать через GPO).")


if __name__ == "__main__":
    main()
