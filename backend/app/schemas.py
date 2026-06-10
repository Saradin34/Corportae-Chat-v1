"""Pydantic schemas (request/response models)."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


# ---------- Auth ----------
class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_]+$")
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    full_name: str = Field(default="", max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class AuthConfigOut(BaseModel):
    local_auth: bool = True
    ldap_enabled: bool = False
    ldap_domain: str = ""
    sso_enabled: bool = False
    sso_negotiate: bool = False
    sso_allow_proxy: bool = False


# ---------- Users ----------
class UserOut(BaseModel):
    id: int
    username: str
    email: str
    full_name: str
    avatar_color: str
    avatar_url: str = ""
    bio: str
    title: str = ""
    phone: str = ""
    office: str = ""
    role: str
    auth_source: str = "local"
    group_id: Optional[int] = None
    is_active: bool
    is_online: bool
    last_seen: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=128)
    bio: Optional[str] = Field(default=None, max_length=500)
    avatar_color: Optional[str] = Field(default=None, max_length=16)
    title: Optional[str] = Field(default=None, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=64)
    office: Optional[str] = Field(default=None, max_length=128)


# ---------- Chats ----------
class CreateChatRequest(BaseModel):
    type: str = Field(default="private")  # private | group | channel
    name: str = Field(default="", max_length=128)
    description: str = Field(default="", max_length=500)
    member_ids: list[int] = Field(default_factory=list)


class UpdateChatRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)
    avatar_color: Optional[str] = Field(default=None, max_length=16)


class AddMembersRequest(BaseModel):
    member_ids: list[int] = Field(default_factory=list)


class ChatOut(BaseModel):
    id: int
    type: str
    name: str
    description: str = ""
    avatar_color: str
    avatar_url: str = ""
    created_by: Optional[int] = None
    last_message: Optional[str] = None
    last_message_at: Optional[datetime] = None
    unread: int = 0
    is_muted: bool = False
    members: list["ChatMemberOut"] = Field(default_factory=list)


class ChatMemberOut(BaseModel):
    id: int
    username: str
    email: str
    full_name: str
    avatar_color: str
    avatar_url: str = ""
    bio: str
    title: str = ""
    phone: str = ""
    office: str = ""
    role: str
    is_active: bool
    is_online: bool
    is_chat_admin: bool = False
    last_seen: Optional[datetime] = None
    created_at: Optional[datetime] = None


# ---------- Messages ----------
class CreateMessageRequest(BaseModel):
    text: str = Field(default="", max_length=4000)
    reply_to: Optional[int] = None
    # optional attachment (set by the upload endpoint result)
    attachment_kind: str = ""
    attachment_url: str = ""
    attachment_thumb: str = ""
    attachment_name: str = ""
    attachment_size: int = 0
    attachment_w: int = 0
    attachment_h: int = 0


class ForwardMessageRequest(BaseModel):
    message_id: int
    to_chat_id: int


class ReactionRequest(BaseModel):
    emoji: str = Field(min_length=1, max_length=16)


class ReactionOut(BaseModel):
    emoji: str
    count: int
    user_ids: list[int] = Field(default_factory=list)
    reacted: bool = False


class MessageOut(BaseModel):
    id: int
    chat_id: int
    sender_id: int
    sender_username: str
    sender_name: str
    sender_color: str
    sender_avatar: str = ""
    text: str
    reply_to: Optional[int] = None
    forwarded_from_name: str = ""
    attachment_kind: str = ""
    attachment_url: str = ""
    attachment_thumb: str = ""
    attachment_name: str = ""
    attachment_size: int = 0
    attachment_w: int = 0
    attachment_h: int = 0
    is_pinned: bool = False
    is_edited: bool
    is_deleted: bool
    is_system: bool = False
    reactions: list[ReactionOut] = Field(default_factory=list)
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Admin ----------
class AdminStats(BaseModel):
    total_users: int
    online_users: int
    total_chats: int
    private_chats: int
    group_chats: int
    total_messages: int
    messages_today: int
    new_users_week: int
    admins: int
    banned_users: int
    groups: int = 0
    ldap_users: int = 0


class AuditLogOut(BaseModel):
    id: int
    actor_id: Optional[int] = None
    actor_name: str
    action: str
    details: str
    created_at: datetime

    class Config:
        from_attributes = True


class BroadcastRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


# ---------- Groups (departments + permissions) ----------
class GroupPermissions(BaseModel):
    can_send_messages: bool = True
    can_create_private: bool = True
    can_create_groups: bool = True
    can_send_files: bool = True
    can_send_images: bool = True
    can_forward: bool = True
    can_pin: bool = True
    can_edit_own: bool = True
    can_delete_own: bool = True
    can_react: bool = True


class GroupOut(GroupPermissions):
    id: int
    name: str
    description: str = ""
    is_default: bool = False
    ad_group_dn: str = ""
    member_count: int = 0

    class Config:
        from_attributes = True


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=500)


class UpdateGroupRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)
    # any permission flag may be included; all optional
    can_send_messages: Optional[bool] = None
    can_create_private: Optional[bool] = None
    can_create_groups: Optional[bool] = None
    can_send_files: Optional[bool] = None
    can_send_images: Optional[bool] = None
    can_forward: Optional[bool] = None
    can_pin: Optional[bool] = None
    can_edit_own: Optional[bool] = None
    can_delete_own: Optional[bool] = None
    can_react: Optional[bool] = None


class AssignGroupRequest(BaseModel):
    user_ids: list[int] = Field(default_factory=list)
    group_id: Optional[int] = None  # None -> remove from group


# ---------- Active Directory group search / import ----------
class AdGroupOut(BaseModel):
    dn: str
    name: str
    description: str = ""
    member_count: int = 0
    # whether an app group is already linked to this AD group
    linked: bool = False


class ImportAdGroupRequest(BaseModel):
    dn: str = Field(min_length=1, max_length=512)
    name: str = Field(default="", max_length=128)  # optional override of group name


class ImportAdGroupResult(BaseModel):
    group: "GroupOut"
    created_users: int = 0
    added_members: int = 0
    total_ad_members: int = 0


class MyPermissionsOut(GroupPermissions):
    group_id: Optional[int] = None
    group_name: str = ""
    is_admin: bool = False


# ---------- Server settings ----------
class ServerSettingsOut(BaseModel):
    max_upload_mb: int = 50
    max_avatar_mb: int = 5
    password_min_length: int = 6
    allow_local_auth: bool = True
    ldap_enabled: bool = False
    app_title: str = "Corporate Chat"
    brand_color: str = "#3390ec"


class ServerSettingsUpdate(BaseModel):
    max_upload_mb: Optional[int] = Field(default=None, ge=1, le=2048)
    max_avatar_mb: Optional[int] = Field(default=None, ge=1, le=64)
    password_min_length: Optional[int] = Field(default=None, ge=4, le=64)
    allow_local_auth: Optional[bool] = None
    ldap_enabled: Optional[bool] = None
    app_title: Optional[str] = Field(default=None, max_length=64)
    brand_color: Optional[str] = Field(default=None, max_length=16)


class UploadResult(BaseModel):
    kind: str
    url: str
    thumb: str = ""
    name: str = ""
    size: int = 0
    width: int = 0
    height: int = 0


class AvatarResult(BaseModel):
    avatar_url: str


ChatOut.model_rebuild()
TokenResponse.model_rebuild()
ImportAdGroupResult.model_rebuild()
