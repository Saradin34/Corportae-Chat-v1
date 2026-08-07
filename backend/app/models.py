"""Database models."""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(128), default="")  # AD: displayName
    avatar_color: Mapped[str] = mapped_column(String(16), default="#3390ec")
    avatar_url: Mapped[str] = mapped_column(String(255), default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    # Directory contact fields (populated from Active Directory on login,
    # editable for local accounts).
    title: Mapped[str] = mapped_column(String(128), default="")    # AD: title
    phone: Mapped[str] = mapped_column(String(64), default="")     # AD: telephoneNumber
    office: Mapped[str] = mapped_column(String(128), default="")   # AD: physicalDeliveryOfficeName
    role: Mapped[str] = mapped_column(String(16), default="user")  # user | admin
    auth_source: Mapped[str] = mapped_column(String(16), default="local")  # local | ldap
    # Org group / department this user belongs to (controls permissions).
    # NULL == "Пользователи без группы" (uses the default group's permissions).
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Permission flags stored on each group. Keep this list in ONE place so the
# model, schemas, migrations and the resolver stay in sync.
GROUP_PERMISSIONS = [
    "can_send_messages",     # писать сообщения
    "can_create_private",    # создавать личные чаты
    "can_create_groups",     # создавать групповые чаты
    "can_send_files",        # отправлять файлы (документы)
    "can_send_images",       # отправлять изображения
    "can_forward",           # пересылать сообщения
    "can_pin",               # закреплять сообщения
    "can_edit_own",          # редактировать свои сообщения
    "can_delete_own",        # удалять свои сообщения
    "can_react",             # ставить реакции
]


class Group(Base):
    """An organisational group / department. Carries a set of permission flags
    that apply to all its members. One group is the implicit 'default' group
    used for users that aren't assigned to any group."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    # The "Пользователи без группы" pseudo-group (cannot be deleted/renamed).
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # If this group mirrors an AD group/OU, its distinguished name is stored here.
    ad_group_dn: Mapped[str] = mapped_column(String(512), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # ----- permission flags (see GROUP_PERMISSIONS) -----
    can_send_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    can_create_private: Mapped[bool] = mapped_column(Boolean, default=True)
    can_create_groups: Mapped[bool] = mapped_column(Boolean, default=True)
    can_send_files: Mapped[bool] = mapped_column(Boolean, default=True)
    can_send_images: Mapped[bool] = mapped_column(Boolean, default=True)
    can_forward: Mapped[bool] = mapped_column(Boolean, default=True)
    can_pin: Mapped[bool] = mapped_column(Boolean, default=True)
    can_edit_own: Mapped[bool] = mapped_column(Boolean, default=True)
    can_delete_own: Mapped[bool] = mapped_column(Boolean, default=True)
    can_react: Mapped[bool] = mapped_column(Boolean, default=True)


class AppSetting(Base):
    """Runtime-editable server settings (key/value). Used for things an admin
    can change from the web UI without editing env vars (file limits, password
    policy, branding, AD toggles, etc.)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(16), default="private")  # private | group | channel
    name: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    avatar_color: Mapped[str] = mapped_column(String(16), default="#5eb5f7")
    avatar_url: Mapped[str] = mapped_column(String(255), default="")
    # If this group mirrors an AD group/OU, its distinguished name is stored here.
    ad_group_dn: Mapped[str] = mapped_column(String(512), default="", index=True)
    # Link to the organisational group (e.g. department). When a group is
    # created or imported, a matching group chat is created automatically.
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["ChatMember"]] = relationship(back_populates="chat", cascade="all, delete-orphan")
    messages: Mapped[list["Message"]] = relationship(back_populates="chat", cascade="all, delete-orphan")


class ChatMember(Base):
    __tablename__ = "chat_members"
    __table_args__ = (UniqueConstraint("chat_id", "user_id", name="uq_chat_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False)
    last_read_message_id: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chat: Mapped["Chat"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship()


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text, default="")
    reply_to: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    forwarded_from_name: Mapped[str] = mapped_column(String(128), default="")
    # attachment (image | file). Empty kind == plain text message.
    attachment_kind: Mapped[str] = mapped_column(String(16), default="")   # "" | image | file
    attachment_url: Mapped[str] = mapped_column(String(255), default="")
    attachment_thumb: Mapped[str] = mapped_column(String(255), default="")
    attachment_name: Mapped[str] = mapped_column(String(255), default="")
    attachment_size: Mapped[int] = mapped_column(Integer, default=0)
    attachment_w: Mapped[int] = mapped_column(Integer, default=0)
    attachment_h: Mapped[int] = mapped_column(Integer, default=0)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    importance: Mapped[str] = mapped_column(String(16), default="normal")  # normal | important | critical
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chat: Mapped["Chat"] = relationship(back_populates="messages")
    sender: Mapped["User"] = relationship()


class Reaction(Base):
    __tablename__ = "reactions"
    __table_args__ = (UniqueConstraint("message_id", "user_id", "emoji", name="uq_reaction"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    emoji: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Calendar(Base):
    """Personal/shared calendar."""
    __tablename__ = "calendars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    color: Mapped[str] = mapped_column(String(16), default="#3390ec")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner: Mapped["User"] = relationship()


class CalendarMember(Base):
    """Users that can see/edit a shared calendar."""
    __tablename__ = "calendar_members"
    __table_args__ = (UniqueConstraint("calendar_id", "user_id", name="uq_calendar_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    can_edit: Mapped[bool] = mapped_column(Boolean, default=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    calendar: Mapped["Calendar"] = relationship()
    user: Mapped["User"] = relationship()


class CalendarNote(Base):
    """Personal calendar note/reminder."""
    __tablename__ = "calendar_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    calendar_id: Mapped[int | None] = mapped_column(ForeignKey("calendars.id", ondelete="CASCADE"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(160), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    color: Mapped[str] = mapped_column(String(16), default="#3390ec")
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship()
    calendar: Mapped["Calendar"] = relationship()


class CallEvent(Base):
    """Telephony call event imported from IP PBX / Asterisk AMI."""
    __tablename__ = "call_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    extension: Mapped[str] = mapped_column(String(32), default="", index=True)
    caller_number: Mapped[str] = mapped_column(String(64), default="")
    caller_name: Mapped[str] = mapped_column(String(128), default="")
    direction: Mapped[str] = mapped_column(String(16), default="incoming")  # incoming | outgoing
    status: Mapped[str] = mapped_column(String(16), default="ringing")      # ringing | answered | missed | ended
    unique_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    linked_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[str] = mapped_column(Text, default="")

    user: Mapped["User"] = relationship()


class SupportTicket(Base):
    """Support request visible to user and admins."""
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(180), default="")
    category: Mapped[str] = mapped_column(String(32), default="general", index=True)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)  # open | in_progress | waiting_user | resolved | closed
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # low | normal | high | critical
    assigned_admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    assigned_admin: Mapped["User"] = relationship(foreign_keys=[assigned_admin_id])


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    sender_role: Mapped[str] = mapped_column(String(16), default="user")  # user | admin
    text: Mapped[str] = mapped_column(Text, default="")
    is_read_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    is_read_by_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["SupportTicket"] = relationship()
    sender: Mapped["User"] = relationship()


class SupportTemplate(Base):
    __tablename__ = "support_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(160), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(32), default="general", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    creator: Mapped["User"] = relationship()


class DownloadEvent(Base):
    """File preview/download audit history."""
    __tablename__ = "download_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(128), default="")
    file_url: Mapped[str] = mapped_column(String(255), default="", index=True)
    file_name: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(32), default="preview")  # preview | download | open
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Admin action audit trail."""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(128), default="")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
