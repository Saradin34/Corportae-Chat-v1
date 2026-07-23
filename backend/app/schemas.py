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
    last_read_message_id: int = 0
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
    importance: str = "normal"  # normal | important | critical


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
    importance: str = "normal"
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


class DocumentPreviewRequest(BaseModel):
    url: str = Field(min_length=1, max_length=255)
    name: str = Field(default="", max_length=255)


class DocumentPreviewOut(BaseModel):
    kind: str = "html"  # html | pdf | image | unsupported
    html: str = ""
    url: str = ""
    name: str = ""
    warnings: list[str] = Field(default_factory=list)


class DownloadLogRequest(BaseModel):
    url: str = Field(min_length=1, max_length=255)
    name: str = Field(default="", max_length=255)
    action: str = Field(default="preview", max_length=32)




class SupportMessageOut(BaseModel):
    id: int
    ticket_id: int
    sender_id: Optional[int] = None
    sender_name: str = ""
    sender_role: str = "user"
    text: str
    is_read_by_user: bool = False
    is_read_by_admin: bool = False
    created_at: datetime


class SupportTicketOut(BaseModel):
    id: int
    user_id: int
    user_name: str = ""
    subject: str
    category: str = "general"
    status: str = "open"
    priority: str = "normal"
    assigned_admin_id: Optional[int] = None
    assigned_admin_name: str = ""
    unread: int = 0
    last_message: str = ""
    created_at: datetime
    updated_at: datetime


class SupportCreateRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=180)
    text: str = Field(min_length=1, max_length=4000)
    category: str = Field(default="general", max_length=32)
    priority: str = Field(default="normal", max_length=16)


class SupportReplyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class SupportStatusRequest(BaseModel):
    status: str = Field(pattern=r"^(open|in_progress|waiting_user|pending|resolved|closed)$")


class SupportAssignRequest(BaseModel):
    admin_id: Optional[int] = None


class SupportTemplateOut(BaseModel):
    id: int
    title: str
    text: str
    category: str = "general"
    is_active: bool = True
    created_by: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SupportTemplateCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=4000)
    category: str = Field(default="general", max_length=32)


class SupportTemplateUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=160)
    text: Optional[str] = Field(default=None, min_length=1, max_length=4000)
    category: Optional[str] = Field(default=None, max_length=32)
    is_active: Optional[bool] = None


class CalendarOut(BaseModel):
    id: int
    name: str
    color: str = "#3390ec"
    owner_id: int
    is_shared: bool = False
    member_ids: list[int] = Field(default_factory=list)
    can_edit: bool = True

    class Config:
        from_attributes = True


class CalendarCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    color: str = Field(default="#3390ec", max_length=16)
    member_ids: list[int] = Field(default_factory=list)


class CalendarUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    color: Optional[str] = Field(default=None, max_length=16)
    member_ids: Optional[list[int]] = None


class CalendarNoteCreate(BaseModel):
    calendar_id: Optional[int] = None
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(default="", max_length=2000)
    starts_at: datetime
    color: str = Field(default="#3390ec", max_length=16)


class CalendarNoteUpdate(BaseModel):
    calendar_id: Optional[int] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=160)
    text: Optional[str] = Field(default=None, max_length=2000)
    starts_at: Optional[datetime] = None
    color: Optional[str] = Field(default=None, max_length=16)
    is_done: Optional[bool] = None


class CalendarNoteOut(BaseModel):
    id: int
    user_id: int
    calendar_id: Optional[int] = None
    calendar_name: str = ""
    calendar_color: str = ""
    title: str
    text: str = ""
    starts_at: datetime
    color: str = "#3390ec"
    is_done: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class OriginateCallRequest(BaseModel):
    to_user_id: int


class CallEventOut(BaseModel):
    id: int
    user_id: Optional[int] = None
    extension: str = ""
    caller_number: str = ""
    caller_name: str = ""
    caller_display: str = ""
    callee_name: str = ""
    callee_number: str = ""
    callee_display: str = ""
    call_summary: str = ""
    direction: str = "incoming"
    status: str = "ringing"
    unique_id: str = ""
    linked_id: str = ""
    is_read: bool = False
    started_at: datetime
    answered_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class DownloadEventOut(BaseModel):
    id: int
    user_id: Optional[int] = None
    username: str = ""
    file_url: str = ""
    file_name: str = ""
    action: str = ""
    created_at: datetime

    class Config:
        from_attributes = True


ChatOut.model_rebuild()
TokenResponse.model_rebuild()
ImportAdGroupResult.model_rebuild()
