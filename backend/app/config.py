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
    # Optional bind mechanism override. Leave empty for the normal AD flow:
    # SIMPLE for user@domain and NTLM for DOMAIN\\user. Set to NEGOTIATE only
    # when Kerberos/GSSAPI is deliberately configured in the container.
    LDAP_AUTH_MECHANISM: str = ""
    # Service account for searching the directory (group membership, profile).
    # Leave empty to use the just-authenticated user's own credentials.
    LDAP_BIND_DN: str = ""
    LDAP_BIND_PASSWORD: str = ""
    # AD group (full DN) whose members become application admins.
    LDAP_ADMIN_GROUP: str = ""
    # Optional AD group (full DN) required to log in at all (allow-list).
    LDAP_REQUIRED_GROUP: str = ""
    LDAP_TIMEOUT: int = 8
    # When True, each AD user's group memberships are mirrored into the app as
    # group chats on login (the user is auto-added to the matching app group).
    LDAP_SYNC_GROUPS: bool = True
    # Optional separate base DN for AD group search/import. Empty = search LDAP_BASE_DN and domain root fallback.
    LDAP_GROUP_BASE_DN: str = ""
    # Only mirror groups whose DN contains one of these comma-separated
    # substrings (e.g. "OU=Departments"). Empty = mirror all memberOf groups.
    LDAP_GROUP_FILTER: str = ""
    # Ignore well-known builtin AD groups that aren't useful as chats.
    LDAP_GROUP_EXCLUDE: str = "Domain Users,Domain Computers,Domain Guests"
    # ---------- Call control ----------
    # ami = Asterisk callback Originate;
    # ip_phone/phone/http = ask the user's physical IP phone to dial directly;
    # fanvil is kept as a legacy alias for ip_phone;
    # auto = try direct IP-phone HTTP control first, then AMI fallback.
    CALL_CONTROL_MODE: str = "ami"  # ami | ip_phone | phone | http | fanvil | auto

    # ---------- Yeastar P-Series OpenAPI call control ----------
    # This is the preferred PBX-side direct click-to-call for Yeastar P-Series.
    # It calls /openapi/v1.0/call/dial with auto_answer=yes so the caller's
    # phone should open the call without the old manual callback-answer step
    # when the endpoint supports auto-answer.
    YEASTAR_ENABLED: bool = False
    YEASTAR_BASE_URL: str = "https://192.168.30.1:8088"
    YEASTAR_API_PATH: str = "openapi/v1.0"
    YEASTAR_USERNAME: str = ""
    YEASTAR_PASSWORD: str = ""
    YEASTAR_VERIFY_SSL: bool = False
    YEASTAR_TIMEOUT: float = 8.0
    YEASTAR_AUTO_ANSWER: str = "yes"
    YEASTAR_DIAL_PERMISSION: str = ""

    # ---------- Generic IP-phone HTTP control ----------
    # Works with Fanvil and also with other IP phones if the phone supports an
    # HTTP dial/action URL. Configure the proper URL template for each model.
    PHONE_CONTROL_ENABLED: bool = False
    PHONE_CONTROL_USERNAME: str = ""
    PHONE_CONTROL_PASSWORD: str = ""
    PHONE_CONTROL_TIMEOUT: float = 4.0
    # JSON or CSV map: {"219":"192.168.30.49"} or "219=192.168.30.49,204=192.168.30.50"
    PHONE_CONTROL_MAP: str = ""
    # Template supports: {scheme}, {ip}, {from_ext}, {to}, {to_ext}. Multiple templates can be separated by ;
    PHONE_CONTROL_SCHEME: str = "http"
    PHONE_CONTROL_DIAL_URL_TEMPLATE: str = "{scheme}://{ip}/cgi-bin/ConfigManApp.com?key={to}"
    PHONE_CONTROL_FALLBACK_AMI: bool = True
    # Optional AD lookup for phone IPs. If PHONE_CONTROL_MAP has no entry for
    # the caller extension, backend can search AD and read the IP from a chosen
    # attribute. This works only if AD really contains the physical phone IP.
    PHONE_CONTROL_AD_ENABLED: bool = False
    PHONE_CONTROL_AD_EXTENSION_ATTRS: str = "telephoneNumber,ipPhone,otherTelephone"
    PHONE_CONTROL_AD_IP_ATTRS: str = "ipPhone,networkAddress,description,info,extensionAttribute1,extensionAttribute2,extensionAttribute3"
    # Optional Asterisk lookup for phone IPs. This uses the same AMI connection
    # that already powers call history and parses active SIP/PJSIP contacts.
    PHONE_CONTROL_ASTERISK_CONTACTS_ENABLED: bool = True
    PHONE_CONTROL_ASTERISK_CONTACT_COMMANDS: str = "pjsip show contacts;sip show peers"

    # Legacy Fanvil names are still supported as aliases for existing .env files.
    FANVIL_ENABLED: bool = False
    FANVIL_USERNAME: str = ""
    FANVIL_PASSWORD: str = ""
    FANVIL_TIMEOUT: float = 4.0
    FANVIL_PHONE_MAP: str = ""
    FANVIL_SCHEME: str = "http"
    FANVIL_DIAL_URL_TEMPLATE: str = "{scheme}://{ip}/cgi-bin/ConfigManApp.com?key={to}"
    FANVIL_FALLBACK_AMI: bool = True

    # ---------- IP PBX / Asterisk AMI ----------
    AMI_ENABLED: bool = False
    AMI_HOST: str = "192.168.30.1"
    AMI_PORT: int = 5038
    AMI_USERNAME: str = ""
    AMI_SECRET: str = ""
    AMI_RECONNECT_SECONDS: int = 5
    AMI_DEBUG_EVENTS: bool = False
    # Originate settings for 1:1 click-to-call. For Asterisk PJSIP use
    # PJSIP/{ext}; for chan_sip use SIP/{ext}; for dialplan callback you can
    # set Local/{ext}@from-internal and adjust context/exten below.
    AMI_ORIGINATE_CHANNEL_TEMPLATE: str = "PJSIP/{ext}"
    AMI_ORIGINATE_CONTEXT: str = "from-internal"
    AMI_ORIGINATE_CONTEXT_TEMPLATE: str = "DLPN_DialPlan{from_ext}"
    AMI_ORIGINATE_DIAL_FALLBACK: bool = False
    AMI_ORIGINATE_PRIORITY: int = 1
    AMI_ORIGINATE_TIMEOUT_MS: int = 30000

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
