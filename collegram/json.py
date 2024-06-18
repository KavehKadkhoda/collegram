from __future__ import annotations

import datetime
import inspect
from typing import TYPE_CHECKING, Any, Union

import msgspec
import polars as pl

from collegram.utils import LOCAL_FS, py_to_pl_types

if TYPE_CHECKING:
    from pathlib import Path

    from fsspec import AbstractFileSystem


RELEVANT_MEDIA_TYPES = {
    "MessageMediaWebPage": "webpage",
    "MessageMediaPhoto": "photo",
    "MessageMediaDocument": "document",
}
PEER_TYPES_ID = {
    "PeerChannel": "channel_id",
    "PeerUser": "user_id",
    "PeerChat": "chat_id",
}


class MessageBase(msgspec.Struct, tag_field="_"):
    id: int
    date: datetime.datetime | None


class MessageService(MessageBase):
    action: Any


class Action(msgspec.Struct):
    pass  # TODO?


class MaybeForwardedMessage(msgspec.Struct):
    id: int
    fwd_from: FwdFrom | None = None
    reply_to: ReplyHeader | None = None


class Message(MessageBase):
    message: str
    mentioned: bool | None = None
    legacy: bool | None = None
    out: bool | None = None
    media_unread: bool | None = None
    silent: bool | None = None
    noforwards: bool | None = None
    post: bool | None = None
    from_scheduled: bool | None = None
    edit_hide: bool | None = None
    pinned: bool | None = None
    invert_media: bool | None = None
    via_bot_id: int | None = None
    views: int | None = None
    forwards: int | None = None
    edit_date: datetime.datetime | None = None
    reactions: Reactions | None = None
    from_id: Peer | None = None
    comments_msg_id: int | None = None  # DEPRECATED
    media: MessageMediaTypes | None = None
    fwd_from: FwdFrom | None = None
    replies: Replies | None = None
    reply_to: ReplyHeader | None = None
    text_urls: list[str] | None = None
    text_mentions: list[str] | None = None


class Peer(msgspec.Struct):
    _: str
    channel_id: str | None = None
    user_id: str | None = None
    chat_id: str | None = None


class MessageMediaBase(msgspec.Struct, tag_field="_"):
    pass


class MessageMediaPhoto(MessageMediaBase):
    photo: MediaType


class MessageMediaDocument(MessageMediaBase):
    document: MediaType
    video: bool | None = None
    voice: bool | None = None


class MessageMediaWebPage(MessageMediaBase):
    webpage: MediaType


ignored_media_structs = [
    msgspec.defstruct(f"MessageMedia{name}", [], bases=(MessageMediaBase,))
    for name in (
        "Geo",
        "Contact",
        "Unsupported",
        "Venue",
        "Game",
        "Invoice",
        "GeoLive",
        "Poll",
        "Dice",
        "Story",
        "Giveaway",
        "GiveawayResults",
    )
]

MessageMediaTypes = Union[
    tuple(
        [MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage]
        + ignored_media_structs
    )
]


class MediaType(msgspec.Struct):
    id: int


class FwdFrom(msgspec.Struct):
    date: datetime.datetime | None
    from_id: Peer | None = None
    channel_post: int | None = None


class Replies(msgspec.Struct):
    replies: int
    comments: bool | None = None


class ReplyHeader(msgspec.Struct):
    reply_to_msg_id: int | None = None
    reply_to_peer_id: Peer | None = None
    forum_topic: bool | None = None


class Reactions(msgspec.Struct):
    results: list[ReactionCount] | None = None


class ReactionCount(msgspec.Struct):
    count: int
    reaction: Reaction


class Reaction(msgspec.Struct):
    emoticon: str | None = None
    document_id: int | None = None


MessageJSONDecodeType = Union[Message, MessageService]
MESSAGE_JSON_DECODER = msgspec.json.Decoder(type=MessageJSONDecodeType)
FAST_FORWARD_DECODER = msgspec.json.Decoder(type=MaybeForwardedMessage)


def read_messages_json(
    path: str | Path,
    fs: AbstractFileSystem = LOCAL_FS,
    decoder: msgspec.json.Decoder = MESSAGE_JSON_DECODER,
):
    with fs.open(str(path), "r") as f:
        return decoder.decode_lines(f.read())


def read_message(
    message: bytes | str, decoder: msgspec.json.Decoder = MESSAGE_JSON_DECODER
):
    return decoder.decode(message)


def yield_message(
    fpath: str | Path,
    fs: AbstractFileSystem = LOCAL_FS,
    decoder: msgspec.json.Decoder = MESSAGE_JSON_DECODER,
):
    with fs.open(str(fpath), "r") as f:
        for line in f:
            if line.strip("\n"):
                yield read_message(line, decoder)


NEW_MSG_FIELDS = {
    "media_type": pl.Utf8,
    "media_id": pl.Int64,
    "from_type": pl.Utf8,
    "from_id": pl.Utf8,
    "replies_to_msg_id": pl.Int64,
    "replies_to_chan_id": pl.Utf8,
    "fwd_from_date": pl.Datetime(time_zone="UTC"),
    "fwd_from_type": pl.Utf8,
    "fwd_from_id": pl.Utf8,
    "fwd_from_msg_id": pl.Int64,
    "nr_replies": pl.Int64,
    "has_comments": pl.Boolean,
}
CHANGED_MSG_FIELDS = {
    "reactions": pl.Struct,
}
DISCARDED_MSG_FIELDS = (
    "media",
    "from_id",
    "reply_to",
    "fwd_from",
    "replies",
    "reactions",
)


def get_pl_schema():
    annots = inspect.getfullargspec(Message).annotations
    discarded_args = DISCARDED_MSG_FIELDS
    msg_schema = {
        arg: py_to_pl_types(annots[arg])
        for arg in set(annots.keys()).difference(discarded_args)
    }
    msg_schema = {**msg_schema, **CHANGED_MSG_FIELDS, **NEW_MSG_FIELDS}
    return msg_schema


def messages_to_dict(messages: list[Message]):
    # can also determine nested from Message.__annotations__, but not super robust
    non_nested_f = set(Message.__struct_fields__).difference(DISCARDED_MSG_FIELDS)
    final_fields = non_nested_f.union(NEW_MSG_FIELDS.keys()).union(
        CHANGED_MSG_FIELDS.keys()
    )
    m_dict = {field: [] for field in final_fields}
    for m in messages:
        for field in non_nested_f:
            m_dict[field].append(getattr(m, field))

        media = m.media
        if media is not None:
            # TODO: save media separately? like whole JSON / parquets of photos / videos
            # / web pages / documents
            if isinstance(media, MessageMediaPhoto):
                m_dict["media_type"].append("photo")
                m_dict["media_id"].append(media.photo.id)
            elif isinstance(media, MessageMediaWebPage):
                m_dict["media_type"].append("webpage")
                m_dict["media_id"].append(media.webpage.id)
            elif isinstance(media, MessageMediaDocument):
                if media.video:
                    m_dict["media_type"].append("video")
                elif media.voice:
                    m_dict["media_type"].append("voice")
                else:
                    m_dict["media_type"].append("document")
                m_dict["media_id"].append(media.document.id)
            else:
                m_dict["media_type"].append("other")
                m_dict["media_id"].append(None)
        else:
            m_dict["media_type"].append(None)
            m_dict["media_id"].append(None)

        from_id = m.from_id
        if from_id is not None:
            m_dict["from_type"].append(from_id._)
            m_dict["from_id"].append(getattr(from_id, PEER_TYPES_ID[from_id._]))
        else:
            m_dict["from_id"].append(None)
            m_dict["from_type"].append(None)

        reply_to = m.reply_to
        m_dict["replies_to_msg_id"].append(getattr(reply_to, "reply_to_msg_id", None))
        m_dict["replies_to_chan_id"].append(
            None
            if reply_to is None
            else getattr(reply_to.reply_to_peer_id, "channel_id", None)
        )

        fwd_from = m.fwd_from
        if fwd_from is not None:
            m_dict["fwd_from_date"].append(fwd_from.date)
            m_dict["fwd_from_msg_id"].append(fwd_from.channel_post)
            if fwd_from.from_id is not None:
                m_dict["fwd_from_type"].append(fwd_from.from_id._)
                m_dict["fwd_from_id"].append(
                    getattr(fwd_from.from_id, PEER_TYPES_ID[fwd_from.from_id._])
                )
            else:
                m_dict["fwd_from_type"].append(None)
                m_dict["fwd_from_id"].append(None)
        else:
            m_dict["fwd_from_date"].append(None)
            m_dict["fwd_from_msg_id"].append(None)
            m_dict["fwd_from_type"].append(None)
            m_dict["fwd_from_id"].append(None)

        replies = m.replies
        if replies is not None:
            m_dict["nr_replies"].append(replies.replies)
            m_dict["has_comments"].append(replies.comments)
        else:
            m_dict["nr_replies"].append(0)
            m_dict["has_comments"].append(False)

        if m.reactions is not None:
            # There can be big number of different reactions, so keep this as dict
            # (converted to struct by Polars).
            reaction_d = {}
            if m.reactions.results is not None:
                for r in m.reactions.results:
                    # Cast `document_id` to string to have consistent type.
                    key = r.reaction.emoticon or str(r.reaction.document_id)
                    reaction_d[key] = r.count
            m_dict["reactions"].append(reaction_d)
        else:
            m_dict["reactions"].append(None)
    return m_dict


def service_messages_to_dict(messages: list[MessageService]):
    nested_f = ["action"]
    non_nested_f = set(MessageService.__struct_fields__).difference(nested_f)
    new_f = ["action", "action_type"]
    final_fields = non_nested_f.union(new_f)
    m_dict = {field: [] for field in final_fields}
    for m in messages:
        for field in non_nested_f:
            m_dict[field].append(getattr(m, field))
        action_d = msgspec.to_builtins(m.action)
        m_dict["action_type"].append(action_d.pop("_"))
        m_dict["action"].append(action_d)
    return m_dict
