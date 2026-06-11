"""Application configuration loaded from environment variables."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://chat:chatpass@db:5432/corporate_chat"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # Security
    SECRET_KEY: str = "change-me-in-production-please-use-a-long-random-string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # App
    APP_NAME: str = "Corporate Chat"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False

    # Extra allowed CORS origins (comma-separated). Empty = same-origin only.
    CORS_ORIGINS: str = ""

    # First admin (auto-created on startup if not exists)
    ADMIN_USERNAME: str = "admin"
    ADMIN_EMAIL: str = "admin@corporate.chat"
    ADMIN_PASSWORD: str = "Admin12345!"

    # ---------- Authentication mode ----------
    # When AD/LDAP is enabled and LOCAL_AUTH is disabled, local
    # registration/login is turned off and users sign in via AD only.
    AUTH_MODE: str = "local"           # local | ldap
    ALLOW_LOCAL_AUTH: bool = True      # set False for "AD only"

    # ---------- Active Directory / LDAP ----------
    LDAP_ENABLED: bool = False
    # Comma-separated list of domain controllers, e.g.:
    #   ldap://dc1.company.local:389,ldaps://dc2.company.local:636
    LDAP_SERVERS: str = "ldap://dc.company.local:389"
    LDAP_USE_SSL: bool = False         # True -> LDAPS (port 636)
    LDAP_START_TLS: bool = False       # StartTLS over 389
    # Active Directory base DN where users live
    LDAP_BASE_DN: str = "DC=company,DC=local"
    # UPN suffix appended to the username for the bind, e.g. "@company.local".
    # AD accepts user@domain (userPrincipalName) for the bind.
    LDAP_DOMAIN: str = "company.local"
    # Optional NetBIOS/down-level domain, used as DOMAIN\\user fallback bind.
    LDAP_NETBIOS: str = ""
    # Attribute used as the login name (sAMAccountName for AD).
    LDAP_LOGIN_ATTR: str = "sAMAccountName"
    # Bind mechanism for the *user* password check:
    #   ""        -> auto: SIMPLE for user@domain (UPN), NTLM for DOMAIN\\user
    #   "SIMPLE"  -> force SIMPLE bind (UPN); recommended for AD over LDAPS
    #   "NTLM"    -> force NTLM bind (DOMAIN\\user)
    #   "KERBEROS"-> SASL/GSSAPI (needs a kerberos ticket cache, NOT a password)
    LDAP_AUTH_MECHANISM: str = ""
    # Service account for searching the directory (group membership, profile).
    # Leave empty to use the just-authenticated user's own credentials.
    LDAP_BIND_DN: str = ""
    LDAP_BIND_PASSWORD: str = ""
    # AD group(s) whose members become application admins.
    # Multiple groups are separated by ';' (semicolon, because DNs contain
    # commas). Each entry may be a full DN OR a bare group name (CN), e.g.:
    #   LDAP_ADMIN_GROUP=CN=ASU-Admin,OU=Roles,...,DC=by;APP_Truska_Admin
    LDAP_ADMIN_GROUP: str = ""
    # Optional AD group (full DN) required to log in at all (allow-list).
    LDAP_REQUIRED_GROUP: str = ""
    LDAP_TIMEOUT: int = 8
    # When True, each AD user's group memberships are mirrored into the app as
    # group chats on login (the user is auto-added to the matching app group).
    LDAP_SYNC_GROUPS: bool = True
    # Only mirror groups whose DN contains one of these comma-separated
    # substrings (e.g. "OU=Departments"). Empty = mirror all memberOf groups.
    LDAP_GROUP_FILTER: str = ""
    # Ignore well-known builtin AD groups that aren't useful as chats.
    LDAP_GROUP_EXCLUDE: str = "Domain Users,Domain Computers,Domain Guests"

    # ---------- SSO (NTLM / Kerberos / Reverse Proxy) ----------
    SSO_ENABLED: bool = False
    SSO_ALLOW_PROXY: bool = True       # Trust REMOTE_USER / X-Remote-User from reverse proxy
    SSO_ALLOW_NEGOTIATE: bool = True   # Direct SPNEGO (requires keytab on Linux / SSPI on Windows)
    SSO_SERVICE_NAME: str = "HTTP/chat.kupava.by@KUPAVA.BY"          # HTTP/chat.company.local@COMPANY.LOCAL
    SSO_KEYTAB_PATH: str = "/etc/krb5.keytab"            # /etc/krb5.keytab (Linux only)

    # ---------- File uploads ----------
    UPLOAD_DIR: str = "/app/uploads"
    MAX_UPLOAD_MB: int = 25            # per-file limit (also enforced by nginx)
    MAX_AVATAR_MB: int = 5
    # Image extensions that get an inline preview + thumbnail
    IMAGE_EXTENSIONS: str = "jpg,jpeg,png,gif,webp,bmp"


settings = Settings()
