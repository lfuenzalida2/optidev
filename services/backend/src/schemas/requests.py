from typing import Literal

from pydantic import BaseModel, Field, SecretStr


class LoginBody(BaseModel):
    username: str
    password: str


class GithubTokenBody(BaseModel):
    token: str = Field(min_length=1, max_length=255)


class MfaTokenBody(BaseModel):
    mfaToken: str


class MfaVerifyBody(MfaTokenBody):
    code: str = Field(max_length=10)


class ChangePasswordBody(BaseModel):
    currentPassword: str
    newPassword: str = Field(min_length=8)


class ProfileBody(BaseModel):
    """Lo que cada usuario puede cambiar de sí mismo. `avatarUrl`/`avatarColor`
    en null vuelven al valor por defecto."""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    avatarUrl: str | None = None
    avatarColor: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    darkMode: bool | None = None


class UserCreateBody(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    email: str
    name: str
    password: str = Field(min_length=8)
    role: str = "member"


class UserUpdateBody(BaseModel):
    email: str | None = None
    name: str | None = None
    role: str | None = None
    isActive: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class ApiTokenCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class RepositoryBody(BaseModel):
    key: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=100)
    githubUrl: str | None = None
    defaultBranch: str = "staging"
    localPath: str | None = None
    description: str | None = None
    color: str = "#1890ff"


class ListBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    collapsible: bool = False


class ListUpdateBody(BaseModel):
    """Actualización parcial: solo se cambian los campos enviados."""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    collapsible: bool | None = None


class ListColorBody(BaseModel):
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class ListReorderBody(BaseModel):
    ids: list[int]


class CardCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    listId: int
    description: str = ""
    repositories: list[str] = []
    assigneeId: int | None = None
    position: int | None = None


class CardFromPullBody(BaseModel):
    repoKey: str = Field(min_length=1)
    number: int = Field(gt=0)


class CardUpdateBody(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    repositories: list[str] | None = None
    assigneeId: int | None = None
    prUrls: list[str] | None = None


class CardMoveBody(BaseModel):
    listId: int
    position: int | None = None


class CommentBody(BaseModel):
    body: str = Field(min_length=1)


class Md5Body(BaseModel):
    text: str


class WorkflowMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)


class WorkflowChatBody(BaseModel):
    """Historial completo (solo texto): el chat no guarda estado en el servidor."""
    messages: list[WorkflowMessage] = Field(min_length=1, max_length=100)


class WorkflowFileChange(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    # None = borrar el archivo.
    content: str | None = Field(default=None, max_length=1_000_000)


class WorkflowPullRequestBody(BaseModel):
    """La propuesta que armó Claude, tal como la devolvió /workflow/chat."""
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20_000)
    baseSha: str = Field(pattern=r"^[0-9a-f]{40}$")
    files: list[WorkflowFileChange] = Field(min_length=1, max_length=50)


class QaReviewBody(BaseModel):
    approved: bool
    listId: int | None = None  # solo al rechazar: lista a la que vuelve la tarjeta
    notes: str | None = None


class NotificationChannelsBody(BaseModel):
    """Parcial: solo se cambian los medios enviados."""
    inApp: bool | None = None


class TestVmBody(BaseModel):
    """Alta (todos los campos salvo los del secreto que no apliquen) o edición
    parcial. Los secretos vacíos en una edición conservan el guardado."""

    __test__ = False

    name: str | None = Field(default=None, max_length=100)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=100)
    authType: Literal["password", "key"] | None = None
    password: str | None = None
    privateKey: str | None = None
    passphrase: str | None = None
    resetHostKey: bool = False
    userId: int | None = None  # solo en el alta y solo admins: crea la VM para otro usuario


class RepositoryPreviewBody(BaseModel):
    script: str | None = Field(default=None, max_length=50_000)
    url: str | None = Field(default=None, max_length=255)


class PreviewLoadBody(BaseModel):
    repository: str
    branch: str = Field(min_length=1, max_length=255)
    vmId: int
    cardId: int | None = None
    prUrl: str | None = Field(default=None, max_length=255)


class PreviewVaultBody(BaseModel):
    addr: str = Field(min_length=1, max_length=255)
    token: str = Field(min_length=1, max_length=500)


class PreviewMysqlBody(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=3306, ge=1, le=65535)
    adminUser: str = Field(min_length=1, max_length=100)
    adminPassword: str | None = None


class SkillRunBody(BaseModel):
    skill: str = Field(max_length=50)
    cardId: int
    prUrl: str = Field(max_length=255)


class SkillReplyBody(BaseModel):
    message: str = Field(max_length=8000)  # el largo útil lo valida skill_service.reply


class RepoFixBody(BaseModel):
    # Token de GitHub temporal con permisos de admin: se usa en esa petición y no se guarda.
    # SecretStr para que no se imprima ni vuelva en un error de validación.
    token: SecretStr
    # Chequeos a arreglar; sin esto, todos los que no estén OK.
    checks: list[str] | None = None


class PreviewDbResetBody(BaseModel):
    # Nombre de la base escrito por el usuario, como confirmación.
    confirm: str = Field(max_length=64)
