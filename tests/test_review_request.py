#  Copyright (C) 2025-2026 Cottage Labs.
#
#  Invenio-Notify is free software; you can redistribute it and/or modify
#  it under the terms of the MIT License; see LICENSE file for more details.

"""Tests for review requests (COAR Notify Request Review) and the ietf:item changes."""

import uuid
from copy import deepcopy
from unittest.mock import patch, Mock

from coarnotify.factory import COARNotifyFactory
from invenio_rdm_records.proxies import current_rdm_records_service

from invenio_notify import constants
from invenio_notify.records.models import (
    ActorModel, EndorsementModel, EndorsementReplyModel, EndorsementRequestModel,
)
from invenio_notify.resources.resource import endorsement_request_resource
from invenio_notify.tasks import inbox_processing
from invenio_notify.utils import actor_utils
from invenio_notify.utils.endorsement_request_utils import (
    create_item, create_request_data, get_item_type, get_main_file,
)
from tests.fixtures.inbox_payload import payload_review, generate_notification_id

LANDING = "https://repo.example.org/records/abcde-12345"


def record_data(files=None, resource_type="publication-preprint", access_files="public"):
    return {
        "links": {"self_html": LANDING},
        "metadata": {"resource_type": {"id": resource_type}},
        "access": {"files": access_files},
        "files": files if files is not None else {"enabled": False},
    }


class TestItem:

    def test_resource_type_map(self, app):
        assert get_item_type(record_data()) == ["Article", "sorg:ScholarlyArticle"]
        assert get_item_type(record_data(resource_type="dataset")) == ["Document", "sorg:Dataset"]
        # prefix match
        assert get_item_type(record_data(resource_type="publication-annotation")) == \
            ["Document", "sorg:CreativeWork"]
        assert get_item_type(record_data(resource_type="image-photo")) == ["Image", "sorg:ImageObject"]
        # unknown
        assert get_item_type(record_data(resource_type="other")) == ["Document", "sorg:CreativeWork"]

    def test_resource_type_map_from_config(self, app):
        app.config["NOTIFY_RESOURCE_TYPE_MAP"] = {"publication": ["Document", "sorg:Thing"]}
        try:
            assert get_item_type(record_data()) == ["Document", "sorg:Thing"]
        finally:
            app.config["NOTIFY_RESOURCE_TYPE_MAP"] = None

    def test_item_is_main_file(self, app):
        files = {
            "enabled": True,
            "order": [],
            "default_preview": "1604.05363v1.pdf",
            "entries": {
                "data.csv": {"key": "data.csv", "mimetype": "text/csv"},
                "1604.05363v1.pdf": {"key": "1604.05363v1.pdf", "mimetype": "application/pdf"},
            },
        }
        item = create_item(record_data(files))
        assert item.id == f"{LANDING}/files/1604.05363v1.pdf"
        assert item.media_type == "application/pdf"
        assert item.type == ["Article", "sorg:ScholarlyArticle"]

    def test_main_file_without_default_preview(self, app):
        files = {
            "enabled": True,
            "entries": {
                "b paper.pdf": {"key": "b paper.pdf"},
                "a.txt": {"key": "a.txt", "mimetype": "text/plain"},
            },
        }
        assert get_main_file(record_data(files))[0] == "a.txt"
        files["order"] = ["b paper.pdf", "a.txt"]
        item = create_item(record_data(files))
        # filename is url-quoted, media type guessed from extension when missing
        assert item.id == f"{LANDING}/files/b%20paper.pdf"
        assert item.media_type == "application/pdf"

    def test_item_falls_back_to_landing_page(self, app):
        # metadata only record
        item = create_item(record_data())
        assert item.id == LANDING
        assert item.media_type == "text/html"
        assert item.type == ["Page", "sorg:AboutPage"]

        # restricted files
        files = {"enabled": True, "entries": {"a.pdf": {"key": "a.pdf", "mimetype": "application/pdf"}}}
        item = create_item(record_data(files, access_files="restricted"))
        assert item.id == LANDING


def make_actor(create_actor, capabilities=None, actor_id=None):
    actor = create_actor(actor_id=actor_id or f"https://service.example/{uuid.uuid4()}",
                         inbox_url="https://service.example/inbox")
    if capabilities is not None:
        ActorModel.update(id=actor.id, data={"capabilities": capabilities})
    return ActorModel.get(actor.id)


class TestRequestData:

    @patch('invenio_notify.utils.endorsement_request_utils.invenio_url_for',
           return_value='https://example.com/inbox')
    def test_review_request(self, mock_url_for, rdm_record, superuser, create_actor):
        actor = make_actor(create_actor, ["review"])
        offer = create_request_data(superuser, rdm_record, actor, constants.REQUEST_TYPE_REVIEW,
                                    origin_id="https://example.com/origin")

        assert offer.type == ["Offer", "coar-notify:ReviewAction"]
        assert offer.id.startswith("urn:uuid:")
        assert offer.object.id == rdm_record.data["links"]["self_html"]
        assert offer.object.type == ["Page", "sorg:AboutPage"]
        assert offer.target.id == actor.actor_id
        assert offer.in_reply_to is None
        # the test record has no files, so the landing page is used as item
        assert offer.object.item.id == rdm_record.data["links"]["self_html"]

        COARNotifyFactory.get_by_object(deepcopy(offer.to_jsonld())).to_jsonld()

    @patch('invenio_notify.utils.endorsement_request_utils.invenio_url_for',
           return_value='https://example.com/inbox')
    def test_endorsement_request_uses_file_item(self, mock_url_for, rdm_record, superuser, create_actor):
        actor = make_actor(create_actor)
        rdm_record.data["files"] = {
            "enabled": True,
            "default_preview": "paper.pdf",
            "entries": {"paper.pdf": {"key": "paper.pdf", "mimetype": "application/pdf"}},
        }
        rdm_record.data["access"]["files"] = "public"
        offer = create_request_data(superuser, rdm_record, actor, constants.REQUEST_TYPE_ENDORSEMENT,
                                    origin_id="https://example.com/origin")
        assert offer.type == ["Offer", "coar-notify:EndorsementAction"]
        assert offer.object.item.id == rdm_record.data["links"]["self_html"] + "/files/paper.pdf"
        assert offer.object.item.media_type == "application/pdf"
        COARNotifyFactory.get_by_object(deepcopy(offer.to_jsonld())).to_jsonld()


class TestAvailableActors:

    def test_capabilities(self, db, rdm_record, create_actor):
        endorser = make_actor(create_actor)  # None -> endorsement only
        reviewer = make_actor(create_actor, ["review"])
        both = make_actor(create_actor, ["endorsement", "review"])
        record_id = rdm_record._record.id

        endorsement_ids = {a["actor_id"] for a in ActorModel.get_available_actors(record_id)}
        review_ids = {a["actor_id"] for a in ActorModel.get_available_actors(record_id, "review")}

        assert endorsement_ids == {endorser.id, both.id}
        assert review_ids == {reviewer.id, both.id}
        assert ActorModel.has_available_actors(record_id, "review")

    def test_status_per_request_type(self, db, rdm_record, create_actor, superuser_identity):
        both = make_actor(create_actor, ["endorsement", "review"])
        record_id = rdm_record._record.id
        EndorsementRequestModel.create({
            "notification_id": generate_notification_id(),
            "record_id": record_id,
            "user_id": superuser_identity.id,
            "actor_id": both.id,
            "raw": {},
            "request_type": "review",
            "latest_status": constants.WORKFLOW_STATUS_REQUEST_REVIEW,
        })

        review = ActorModel.get_available_actors(record_id, "review")
        endorsement = ActorModel.get_available_actors(record_id, "endorsement")
        assert review[0]["status"] == constants.WORKFLOW_STATUS_REQUEST_REVIEW
        assert endorsement[0]["status"] == constants.WORKFLOW_STATUS_AVAILABLE


class TestSendReviewRequest:

    @staticmethod
    def send(client, identity, record, actor_id, request_type="review"):
        url = f'/api/endorsement-request/send/{record.id}'
        with patch.object(endorsement_request_resource, 'g') as mock_g:
            mock_g.identity = identity
            with patch.object(endorsement_request_resource.record_utils, 'read_record_item') as mock_read:
                mock_read.return_value = record
                return client.post(url, json={"actor_id": actor_id, "request_type": request_type})

    @patch('invenio_notify.utils.endorsement_request_utils.invenio_url_for',
           return_value='https://example.com/self-inbox')
    @patch.object(endorsement_request_resource.requests, 'post')
    def test_success(self, mock_post, mock_url_for, client, rdm_record, superuser_identity,
                     create_actor, db):
        actor = make_actor(create_actor, ["review"])
        rdm_record._record.parent.access.owner.owner_id = superuser_identity.user.id
        mock_post.return_value = Mock(status_code=201)

        response = self.send(client, superuser_identity, rdm_record, actor.id)

        assert response.status_code == 200, response.get_json()
        sent = mock_post.call_args.kwargs["json"]
        assert sent["type"] == ["Offer", "coar-notify:ReviewAction"]

        request = EndorsementRequestModel.query.one()
        assert request.request_type == "review"
        assert request.latest_status == constants.WORKFLOW_STATUS_REQUEST_REVIEW
        assert request.notification_id == sent["id"]

        # while pending, no new request can be sent
        statuses = ActorModel.get_available_actors(rdm_record._record.id, "review")
        assert statuses[0]["status"] == constants.WORKFLOW_STATUS_REQUEST_REVIEW
        assert constants.WORKFLOW_STATUS_REQUEST_REVIEW not in \
            endorsement_request_resource.get_available_statuses("review")

    @patch.object(endorsement_request_resource.requests, 'post')
    def test_actor_without_review_capability(self, mock_post, client, rdm_record, superuser_identity,
                                             create_actor, db):
        actor = make_actor(create_actor)  # endorsement only
        rdm_record._record.parent.access.owner.owner_id = superuser_identity.user.id

        response = self.send(client, superuser_identity, rdm_record, actor.id)

        assert response.status_code == 400
        mock_post.assert_not_called()

    def test_unknown_request_type(self, client, rdm_record, superuser_identity, create_actor, db):
        actor = make_actor(create_actor, ["review"])
        rdm_record._record.parent.access.owner.owner_id = superuser_identity.user.id
        response = self.send(client, superuser_identity, rdm_record, actor.id, request_type="bogus")
        assert response.status_code == 400


def review_service_announce(record_id, in_reply_to):
    """Review announcement as sent by a review service (e.g. Kotahi)."""
    return {
        "@context": ["https://www.w3.org/ns/activitystreams", "https://coar-notify.net"],
        "id": generate_notification_id(),
        "type": ["Announce", "coar-notify:ReviewAction"],
        "actor": {"id": "https://review.example.org", "name": "Review Service", "type": "Service"},
        "object": {"id": "https://review.example.org/notify/articles/4/index.html",
                   "type": ["Document", "sorg:Review"]},
        "origin": {"id": "https://review.example.org", "type": "Service",
                   "inbox": "https://review.example.org/api/coar/inbox/notify"},
        "target": {"id": "https://repo.example.org", "type": "Service",
                   "inbox": "https://repo.example.org/api/notify/inbox"},
        "context": {"id": f"https://127.0.0.1:5000/records/{record_id}",
                    "type": ["Document", "sorg:AboutPage"],
                    "ietf:item": {"id": f"https://127.0.0.1:5000/records/{record_id}/files/paper.pdf",
                                  "type": ["Article", "sorg:ScholarlyArticle"],
                                  "mediaType": "application/pdf"},
                    "ietf:cite-as": "https://doi.org/10.48550/arXiv.1604.05363"},
        "inReplyTo": in_reply_to,
    }


class TestReviewReplies:

    def test_announce_review_with_unknown_in_reply_to_is_stored(self, db, rdm_record,
                                                                inbox_test_data_builder):
        """An announcement may refer to a request that was never stored."""
        data = review_service_announce(rdm_record.id, in_reply_to=generate_notification_id())
        test_data = (inbox_test_data_builder(rdm_record.id, data)
                     .create_actor().add_member_to_actor().create_inbox())

        inbox_processing()

        db.session.refresh(test_data.inbox)
        assert test_data.inbox.process_note is None
        endorsement = EndorsementModel.query.one()
        assert endorsement.review_type == constants.TYPE_REVIEW
        assert endorsement.result_url == data["object"]["id"]
        assert EndorsementReplyModel.query.count() == 0

    def test_review_request_accept_then_announce(self, db, rdm_record, inbox_test_data_builder,
                                                 superuser_identity):
        request_id = generate_notification_id()
        data = review_service_announce(rdm_record.id, in_reply_to=request_id)
        builder = (inbox_test_data_builder(rdm_record.id, data)
                   .create_actor().add_member_to_actor())
        record = current_rdm_records_service.record_cls.pid.resolve(rdm_record.id)
        request = EndorsementRequestModel.create({
            "notification_id": request_id,
            "record_id": record.id,
            "user_id": superuser_identity.id,
            "actor_id": builder.actor.id,
            "raw": {},
            "request_type": "review",
            "latest_status": constants.WORKFLOW_STATUS_REQUEST_REVIEW,
        })

        # Accept
        accept = {
            "@context": ["https://www.w3.org/ns/activitystreams", "https://coar-notify.net"],
            "id": generate_notification_id(),
            "type": "Accept",
            "actor": data["actor"],
            "origin": data["origin"],
            "target": data["target"],
            "inReplyTo": request_id,
            "object": {
                "id": request_id,
                "type": ["Offer", "coar-notify:ReviewAction"],
                "object": {"id": f"https://127.0.0.1:5000/records/{rdm_record.id}",
                           "ietf:cite-as": f"https://127.0.0.1:5000/records/{rdm_record.id}"},
                "origin": data["target"],
                "target": data["origin"],
            },
        }
        builder._create_inbox_fixture(record_id=rdm_record.id, raw=accept)
        inbox_processing()
        db.session.refresh(request)
        assert request.latest_status == constants.WORKFLOW_STATUS_ACCEPT
        assert EndorsementModel.query.count() == 0

        # Announce Review
        builder.create_inbox()
        inbox_processing()
        db.session.refresh(request)
        db.session.refresh(builder.inbox)
        assert builder.inbox.process_note is None
        assert request.latest_status == constants.WORKFLOW_STATUS_ANNOUNCE_REVIEW
        endorsement = EndorsementModel.query.one()
        assert endorsement.endorsement_reply_id is not None

        statuses = ActorModel.get_available_actors(record.id, "review")
        # actor has no review capability in this test -> not listed
        assert statuses == []
        ActorModel.update(id=builder.actor.id, data={"capabilities": ["review"]})
        statuses = ActorModel.get_available_actors(record.id, "review")
        assert statuses[0]["status"] == constants.WORKFLOW_STATUS_ANNOUNCE_REVIEW


def test_record_id_from_file_url(app):
    from invenio_notify.utils.notify_utils import get_recid_by_record_url
    assert get_recid_by_record_url(f"{LANDING}/files/1604.05363v1.pdf", app) == "abcde-12345"
    assert get_recid_by_record_url(LANDING, app) == "abcde-12345"
    assert get_recid_by_record_url(LANDING + "/", app) == "abcde-12345"


class TestStoreBeforeSend:

    @patch('invenio_notify.utils.endorsement_request_utils.invenio_url_for',
           return_value='https://example.com/self-inbox')
    @patch.object(endorsement_request_resource.requests, 'post')
    def test_request_stored_before_send_and_marked_failed(self, mock_post, mock_url_for, client,
                                                          rdm_record, superuser_identity,
                                                          create_actor, db):
        actor = make_actor(create_actor, ["review"])
        rdm_record._record.parent.access.owner.owner_id = superuser_identity.user.id

        seen = {}

        def fake_post(url, json=None, **kwargs):
            # the request must already be stored when the service is contacted
            seen['stored'] = EndorsementRequestModel.query.filter_by(notification_id=json["id"]).count()
            return Mock(status_code=400, text='{"message":"Notification already exists"}')

        mock_post.side_effect = fake_post

        response = TestSendReviewRequest.send(client, superuser_identity, rdm_record, actor.id)
        assert response.status_code == 400
        assert seen['stored'] == 1

        request = EndorsementRequestModel.query.one()
        assert request.latest_status == constants.WORKFLOW_STATUS_SEND_FAILED

        # a failed send can be retried
        statuses = ActorModel.get_available_actors(rdm_record._record.id, "review")
        assert statuses[0]["status"] == constants.WORKFLOW_STATUS_SEND_FAILED
        assert constants.WORKFLOW_STATUS_SEND_FAILED in \
            endorsement_request_resource.get_available_statuses("review")


def test_record_id_from_reply_referring_to_offer(db, rdm_record, create_actor, superuser_identity):
    from invenio_notify.proxies import current_inbox_service
    actor = make_actor(create_actor, ["review"])
    offer_id = generate_notification_id()
    EndorsementRequestModel.create({
        "notification_id": offer_id,
        "record_id": rdm_record._record.id,
        "user_id": superuser_identity.id,
        "actor_id": actor.id,
        "raw": {},
        "request_type": "review",
        "latest_status": constants.WORKFLOW_STATUS_REQUEST_REVIEW,
    })
    reply = Mock(context=None, in_reply_to=offer_id,
                 object=Mock(id=offer_id, object=None))
    assert current_inbox_service.get_record_id_from_notification(reply) == rdm_record.id

    reply = Mock(context=Mock(id="https://review.example.org/submission/1"), in_reply_to=None,
                 object=Mock(id=offer_id, object=None))
    assert current_inbox_service.get_record_id_from_notification(reply) == rdm_record.id
