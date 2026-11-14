#  Copyright (C) 2025-2026 Cottage Labs.
#
#  Invenio-Notify is free software; you can redistribute it and/or modify
#  it under the terms of the MIT License; see LICENSE file for more details.

from coarnotify.core.notify import NotifyObject, NotifyTypes, NotifyItem, NotifyService, NotifyProperties
from coarnotify.core.activitystreams2 import ActivityStreamsTypes, Properties
import mimetypes
from urllib.parse import quote

from coarnotify.patterns import RequestEndorsement, RequestReview
from coarnotify.core.notify import NotifyActor

from invenio_base import invenio_url_for
from invenio_records_resources.services.records.results import RecordItem

from invenio_notify import constants
from invenio_notify.constants import WORKFLOW_STATUS_TENTATIVE_REJECT
from invenio_notify.records.models import ActorModel, EndorsementRequestModel


def _get_user_name(user):
    if 'full_name' in user.user_profile and user.user_profile['full_name']:
        return user.user_profile['full_name']
    elif user.username:
        return user.username

    return user.email

DEFAULT_RESOURCE_TYPE_MAP = {
    # InvenioRDM resource type id (prefix) -> ActivityStreams / schema.org types of ietf:item
    "publication-preprint": [ActivityStreamsTypes.ARTICLE, "sorg:ScholarlyArticle"],
    "publication-article": [ActivityStreamsTypes.ARTICLE, "sorg:ScholarlyArticle"],
    "publication-conferencepaper": [ActivityStreamsTypes.ARTICLE, "sorg:ScholarlyArticle"],
    "publication-conferenceproceeding": [ActivityStreamsTypes.DOCUMENT, "sorg:Book"],
    "publication-workingpaper": [ActivityStreamsTypes.ARTICLE, "sorg:ScholarlyArticle"],
    "publication-datapaper": [ActivityStreamsTypes.ARTICLE, "sorg:ScholarlyArticle"],
    "publication-softwaredocumentation": [ActivityStreamsTypes.ARTICLE, "sorg:TechArticle"],
    "publication-book": [ActivityStreamsTypes.DOCUMENT, "sorg:Book"],
    "publication-section": [ActivityStreamsTypes.DOCUMENT, "sorg:Chapter"],
    "publication-thesis": [ActivityStreamsTypes.DOCUMENT, "sorg:Thesis"],
    "publication-report": [ActivityStreamsTypes.DOCUMENT, "sorg:Report"],
    "publication": [ActivityStreamsTypes.DOCUMENT, "sorg:CreativeWork"],
    "dataset": [ActivityStreamsTypes.DOCUMENT, "sorg:Dataset"],
    "software": [ActivityStreamsTypes.DOCUMENT, "sorg:SoftwareSourceCode"],
    "image": [ActivityStreamsTypes.IMAGE, "sorg:ImageObject"],
    "video": [ActivityStreamsTypes.VIDEO, "sorg:VideoObject"],
    "audio": [ActivityStreamsTypes.AUDIO, "sorg:AudioObject"],
    "presentation": [ActivityStreamsTypes.DOCUMENT, "sorg:PresentationDigitalDocument"],
    "poster": [ActivityStreamsTypes.DOCUMENT, "sorg:Poster"],
    "lesson": [ActivityStreamsTypes.DOCUMENT, "sorg:LearningResource"],
}
DEFAULT_ITEM_TYPE = [ActivityStreamsTypes.DOCUMENT, "sorg:CreativeWork"]
LANDING_PAGE_TYPE = [ActivityStreamsTypes.PAGE, NotifyTypes.ABOUT_PAGE]

PATTERN_CLASSES = {
    constants.REQUEST_TYPE_ENDORSEMENT: RequestEndorsement,
    constants.REQUEST_TYPE_REVIEW: RequestReview,
}


def _config(key, default=None):
    from flask import current_app
    try:
        return current_app.config.get(key, default)
    except RuntimeError:  # outside application context
        return default


def get_item_type(record_data: dict) -> list:
    """ActivityStreams and schema.org types for the record's resource type.

    Uses NOTIFY_RESOURCE_TYPE_MAP (falls back to DEFAULT_RESOURCE_TYPE_MAP). The longest
    key that equals the resource type id or is a prefix of it (``publication`` matches
    ``publication-annotation``) wins.
    """
    type_map = _config("NOTIFY_RESOURCE_TYPE_MAP") or DEFAULT_RESOURCE_TYPE_MAP
    resource_type = ((record_data.get("metadata") or {}).get("resource_type") or {}).get("id") or ""
    best = None
    for key in type_map:
        if resource_type == key or resource_type.startswith(key + "-"):
            if best is None or len(key) > len(best):
                best = key
    return list(type_map[best]) if best else list(DEFAULT_ITEM_TYPE)


def _file_entries(record_data: dict) -> dict:
    entries = (record_data.get("files") or {}).get("entries") or {}
    if isinstance(entries, list):
        entries = {e.get("key"): e for e in entries if e.get("key")}
    return entries


def get_main_file(record_data: dict):
    """Pick the file a reviewer or endorser should look at.

    Returns ``(key, entry)`` for the default preview file, else the first file in the
    record's file order, else the first file; ``(None, None)`` when the record has no
    public files.
    """
    files = record_data.get("files") or {}
    if not files.get("enabled", True):
        return None, None
    access_files = (record_data.get("access") or {}).get("files")
    if access_files and access_files != "public":
        return None, None

    entries = _file_entries(record_data)
    if not entries:
        return None, None

    default_preview = files.get("default_preview")
    if default_preview and default_preview in entries:
        return default_preview, entries[default_preview]
    for key in files.get("order") or []:
        if key in entries:
            return key, entries[key]
    key = sorted(entries)[0]
    return key, entries[key]


def create_item(record_data: dict) -> NotifyItem:
    """Build ``ietf:item``: the main file of the record with its media type and the
    resource type, or the landing page if the record has no public files."""
    landing_page = record_data["links"]["self_html"]
    item = NotifyItem()

    key, entry = get_main_file(record_data)
    if key is None:
        item.id = landing_page
        item.media_type = "text/html"
        item.type = list(LANDING_PAGE_TYPE)
        return item

    item.id = f"{landing_page.rstrip('/')}/files/{quote(key)}"
    media_type = (entry or {}).get("mimetype") or mimetypes.guess_type(key)[0] or "application/octet-stream"
    item.media_type = media_type
    item.type = get_item_type(record_data)
    return item


def create_request_data(user, record: RecordItem, actor: ActorModel,
                        request_type=constants.REQUEST_TYPE_ENDORSEMENT, origin_id=None):
    """Create an endorsement or review request (a COAR Notify Offer).

    Args:
        user: User object making the request
        record: RecordItem object representing the record
        actor: Actor object containing inbox URL and other details
        request_type: 'endorsement' (RequestEndorsement) or 'review' (RequestReview)
        origin_id: Origin ID from configuration (optional, will be retrieved from config if not provided)
    """
    if request_type not in PATTERN_CLASSES:
        raise ValueError(f"Unknown request type: {request_type}")

    if origin_id is None:
        from flask import current_app
        origin_id = current_app.config.get("NOTIFY_ORIGIN_ID", None)
        if not origin_id:
            raise ValueError("NOTIFY_ORIGIN_ID must be set in invenio.cfg")

    # Check for existing TentativeReject reply to include as inReplyTo
    status, noti_id = EndorsementRequestModel.get_latest_status(
        record._record.model.id, actor.id, True, request_type=request_type
    )

    offer = PATTERN_CLASSES[request_type]()

    notify_actor = NotifyActor()
    notify_actor.id = f"mailto:{user.email}"
    notify_actor.name = _get_user_name(user)
    notify_actor.type = ActivityStreamsTypes.PERSON

    obj = NotifyObject()
    obj.type = list(LANDING_PAGE_TYPE)
    obj.id = record.data["links"]["self_html"]
    if 'doi' in record.data['links']:
        obj.cite_as = record.data['links']['doi']
    else:
        obj.cite_as = record.data["links"]["self_html"]

    obj.item = create_item(record.data)

    origin = NotifyService()
    origin.id = origin_id
    origin.inbox = invenio_url_for('inbox_api.receive_notification')
    origin.type = ActivityStreamsTypes.SERVICE

    target = NotifyService()
    target.id = actor.actor_id
    target.inbox = str(actor.inbox_url) # actor.inbox_url is a furl
    target.type = ActivityStreamsTypes.SERVICE

    offer.actor = notify_actor
    offer.object = obj
    offer.origin = origin
    offer.target = target

    if status == WORKFLOW_STATUS_TENTATIVE_REJECT and noti_id:
        offer.in_reply_to = noti_id

    return offer


def create_endorsement_request_data(user, record: RecordItem, actor: ActorModel, origin_id=None):
    """Create endorsement request data following COAR notification structure.

    Kept for backwards compatibility, see `create_request_data`.
    """
    return create_request_data(user, record, actor, constants.REQUEST_TYPE_ENDORSEMENT, origin_id)
