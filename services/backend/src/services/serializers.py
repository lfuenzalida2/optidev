from datetime import timezone

from src.models.api_token import ApiToken
from src.models.board_list import BoardList
from src.models.card import Card
from src.models.card_activity import CardActivity
from src.models.card_comment import CardComment
from src.models.repository import Repository
from src.models.user import User


def _iso(value):
    # SQLite no guarda la zona horaria: lo que vuelve de la BD es naive pero
    # siempre se escribe en UTC. Sin el offset el navegador lo leería como hora local.
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def user_brief(user: User | None) -> dict | None:
    if not user:
        return None
    return {
        "id": user.id, "username": user.username, "name": user.name,
        "avatarUrl": user.avatar_url, "avatarColor": user.avatar_color,
    }


def user_dict(user: User) -> dict:
    return {
        **user_brief(user),
        "email": user.email,
        "role": user.role,
        "isActive": user.is_active,
        "onboardingCompleted": user.onboarding_completed_at is not None,
        "darkMode": user.dark_mode,
        "mfaEnabled": user.mfa_enabled_at is not None,
        "githubLogin": user.github_login,
        "routineLastRunAt": _iso(user.routine_last_run_at),
        "createdAt": _iso(user.created_at),
    }


def notification_dict(notification) -> dict:
    return {
        "id": notification.id,
        "kind": notification.kind,
        "data": notification.data or {},
        "readAt": _iso(notification.read_at),
        "createdAt": _iso(notification.created_at),
    }


def vm_request_dict(vm_request) -> dict:
    return {"id": vm_request.id, "createdAt": _iso(vm_request.created_at)}


def repository_dict(repo: Repository) -> dict:
    return {
        "id": repo.id,
        "key": repo.key,
        "name": repo.name,
        "githubUrl": repo.github_url,
        "defaultBranch": repo.default_branch,
        "localPath": repo.local_path,
        "description": repo.description,
        "color": repo.color,
        "previewScript": repo.preview_script,
        "previewUrl": repo.preview_url,
    }


def list_dict(board_list: BoardList) -> dict:
    return {
        "id": board_list.id,
        "name": board_list.name,
        "position": board_list.position,
        "color": board_list.color,
        "collapsible": board_list.collapsible,
    }


def card_dict(card: Card, comment_count: int | None = None) -> dict:
    data = {
        "id": card.id,
        "listId": card.list_id,
        "listName": card.board_list.name,
        "title": card.title,
        "description": card.description,
        "position": card.position,
        "assignee": user_brief(card.assignee),
        "createdBy": user_brief(card.created_by),
        "repositories": [{"id": r.id, "key": r.key, "name": r.name, "color": r.color} for r in card.repositories],
        "prUrls": list(card.pr_urls or []),
        "archivedAt": _iso(card.archived_at),
        "createdAt": _iso(card.created_at),
        "updatedAt": _iso(card.updated_at),
    }
    if comment_count is not None:
        data["commentCount"] = comment_count
    return data


def comment_dict(comment: CardComment) -> dict:
    return {
        "id": comment.id,
        "cardId": comment.card_id,
        "author": user_brief(comment.author),
        "body": comment.body,
        "via": comment.via,
        "createdAt": _iso(comment.created_at),
        "updatedAt": _iso(comment.updated_at),
    }


def activity_dict(activity: CardActivity) -> dict:
    return {
        "id": activity.id,
        "actor": user_brief(activity.actor),
        "via": activity.via,
        "action": activity.action,
        "data": activity.data or {},
        "createdAt": _iso(activity.created_at),
    }


def api_token_dict(token: ApiToken) -> dict:
    return {
        "id": token.id,
        "name": token.name,
        "prefix": token.prefix,
        "lastUsedAt": _iso(token.last_used_at),
        "createdAt": _iso(token.created_at),
    }
