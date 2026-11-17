# Importa todos los modelos para que queden registrados en Base.metadata
# (lo necesitan alembic y create_all en los tests).
from src.models.api_token import ApiToken
from src.models.app_setting import AppSetting
from src.models.board_list import BoardList
from src.models.card import Card, card_repositories
from src.models.card_activity import CardActivity
from src.models.card_comment import CardComment
from src.models.notification import Notification
from src.models.preview_load import PreviewLoad
from src.models.repository import Repository
from src.models.test_vm import TestVm
from src.models.user import User
from src.models.vm_request import VmRequest
from src.models.vm_task import VmTask

__all__ = [
    "ApiToken", "AppSetting", "BoardList", "Card", "CardActivity", "CardComment", "Notification", "PreviewLoad", "Repository", "TestVm", "User",
    "VmRequest", "VmTask",
    "card_repositories",
]
