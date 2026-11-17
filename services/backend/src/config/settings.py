"""Configuración del backend, leída de variables de entorno (o de `.env`).

JWT_SECRET no tiene default a propósito: sin él (o si es corto) el backend no
arranca, para que nunca quede desplegado firmando tokens con un secreto débil.
"""

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    BACKEND_PORT: int = 8000
    # Base de datos: con DB_HOST definido la URL se arma con las DB_*. Si no,
    # se usa DATABASE_URL (override completo: tests y CI) o SQLite.
    DB_DIALECT: str = "mysql+aiomysql"
    DB_HOST: str | None = None
    DB_PORT: int = 3306
    DB_NAME: str = "optidev"
    DB_USER: str = "optidev"
    DB_PASSWORD: str = ""
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/optidev.db"

    # HS256 con menos de 32 bytes de clave es atacable por fuerza bruta (RFC 7518 §3.2).
    JWT_SECRET: str = Field(min_length=32)
    JWT_ACCESS_TTL_MINUTES: int = 12 * 60

    # Usuario admin inicial: se crea al arrancar solo si la tabla de usuarios
    # está vacía. Después se ignora (cambiar la contraseña desde la app).
    ADMIN_USERNAME: str = "admin"
    ADMIN_EMAIL: str = "admin@optidev.com"
    ADMIN_PASSWORD: str | None = None

    # Actualización desde la app (deploy/update.sh). El backend solo crea el
    # archivo de pedido en UPDATE_REQUEST_DIR; el estado y el log los escribe
    # el script (root) en UPDATE_STATE_DIR. Rutas relativas: desde el cwd del backend.
    UPDATE_REQUEST_DIR: str = "./data/update"
    UPDATE_STATE_DIR: str = "/var/lib/optidev-update"

    # Listas con las que se crea el tablero la primera vez.
    DEFAULT_LISTS: str = "Pendiente,En proceso,PR,Testing,Hecho"

    # Imágenes pegadas en tarjetas y fotos de perfil. nginx limita el body a
    # 10 MB (client_max_body_size): no subir este valor por encima de eso.
    UPLOAD_DIR: str = "./data/uploads"
    UPLOAD_MAX_MB: int = 5

    # Dashboard de GitHub: token con lectura de los repos (scope `repo` si son
    # privados). Sin él la página muestra cómo configurarlo.
    GITHUB_TOKEN: str | None = None
    GITHUB_CACHE_SECONDS: int = 60
    # Cada cuánto se renueva solo el caché de PRs abiertos (dashboard de PRs).
    GITHUB_PULLS_REFRESH_SECONDS: int = 300

    # Salud de los repos (Configuración → Repositorios): cada cuánto se renueva la revisión
    # (queda en caché), qué ramas no deben aceptar push directo y de qué repo (key del tablero)
    # se toman los labels y workflows estándar (vacío = solo "Aprobado"/"Revisado" y algún workflow).
    REPO_HEALTH_INTERVAL_SECONDS: int = 86400
    REPO_HEALTH_BRANCHES: str = "master"
    REPO_HEALTH_REFERENCE_REPO: str | None = "mms-backend"

    # Pestaña Workflow: el repo de agentes es el del tablero con esta key. Se
    # lee con GITHUB_TOKEN; el PR se abre con el token personal de quien
    # aprueba la propuesta (escritura de Contents y Pull requests). El chat usa
    # la API de Anthropic.
    WORKFLOW_REPO_KEY: str = "agents"
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str = "claude-sonnet-5-5"

    # Clave para cifrar las credenciales de las VMs de pruebas (cualquier
    # texto largo). Sin ella se deriva de JWT_SECRET: cambiar JWT_SECRET deja
    # ilegibles las credenciales guardadas y cada usuario tiene que volver a
    # cargarlas. Lo mismo pasa si se cambia esta.
    VM_CREDENTIALS_KEY: str | None = None
    VM_SSH_TIMEOUT_SECONDS: int = 10

    # Cargar ramas en las VMs de pruebas: optidev descarga la rama en
    # PREVIEW_MIRROR_DIR (con GITHUB_TOKEN) y la manda a la VM por SSH, a
    # PREVIEW_REMOTE_DIR/<repo>. Ninguna credencial de GitHub llega a la VM.
    PREVIEW_MIRROR_DIR: str = "./data/preview-mirrors"
    PREVIEW_REMOTE_DIR: str = "/opt/optidev-preview"
    PREVIEW_TIMEOUT_SECONDS: int = 30 * 60
    # Cargar ramas y aplicar arreglos en el propio servidor de optidev (una
    # "VM" que apunta acá) está bloqueado: instalaría cosas como root en el
    # servidor. Solo los tests lo habilitan.
    PREVIEW_ALLOW_SELF: bool = False
    # URL de optidev tal como la ven las VMs (para registrar el MCP en la
    # rutina diaria). Vacía = la del navegador que pidió la instalación.
    PUBLIC_URL: str = ""

    # MFA con app de autenticación (TOTP). Con MFA_REQUIRED, quien todavía no
    # lo tiene lo registra al iniciar sesión; sin él, el registro no se exige
    # (pero quien ya lo tiene igual debe ingresar el código).
    MFA_REQUIRED: bool = True
    MFA_ISSUER: str = "Optidev"
    # Clave para cifrar los secretos TOTP. Sin ella se deriva de JWT_SECRET:
    # cambiar cualquiera de las dos invalida todos los MFA (un admin los
    # rehace, o a mano en la base: ver README).
    MFA_SECRET_KEY: str | None = None

    @model_validator(mode="after")
    def _build_database_url(self):
        if self.DB_HOST:
            query = {"charset": "utf8mb4"} if self.DB_DIALECT.startswith("mysql") else {}
            self.DATABASE_URL = URL.create(
                self.DB_DIALECT, self.DB_USER, self.DB_PASSWORD or None,
                self.DB_HOST, self.DB_PORT, self.DB_NAME, query,
            ).render_as_string(hide_password=False)
        return self


settings = Settings()
