#  Copyright (C) 2025-2026 Cottage Labs.
#
#  Invenio-Notify is free software; you can redistribute it and/or modify
#  it under the terms of the MIT License; see LICENSE file for more details.

import requests
from coarnotify.patterns import RequestEndorsement
from flask import g, current_app, request
from flask_resources import (
    Resource,
    resource_requestctx,
    response_handler,
    route,
)
from invenio_access.permissions import system_identity
from invenio_accounts.models import User
from invenio_db.uow import unit_of_work
from invenio_records_resources.resources.records.resource import (
    request_data,
    request_view_args,
)
from invenio_records_resources.services.records.results import RecordItem

from invenio_notify import constants
from invenio_notify.errors import SendRequestFail, BadRequestError
from invenio_notify.records.models import ActorModel, EndorsementRequestModel, EndorsementModel
from invenio_notify.utils import record_utils
from invenio_notify.utils.endorsement_request_utils import (
    create_request_data,
)
from invenio_notify.utils.record_utils import resolve_record_from_pid
from invenio_rdm_records.records import RDMRecord
from ..errors import ApiErrorHandlersMixin


def validate_owner_id(record: RDMRecord, user_id):
    """Check if the user is the owner of the record.
    
    Args:
        record: RDMRecord instance
        user_id: ID of the user to check
        
    Raises:
        BadRequestError: If user is not the record owner
    """
    if record.parent.access.owner.owner_id != user_id:
        raise BadRequestError('User is not the owner of this record')


def get_request_type(value) -> str:
    """Validate the request type sent by the client (defaults to endorsement)."""
    request_type = value or constants.REQUEST_TYPE_ENDORSEMENT
    if request_type not in constants.REQUEST_TYPES:
        raise BadRequestError(f'Unknown request type: {request_type}')
    if (request_type == constants.REQUEST_TYPE_REVIEW
            and not current_app.config.get(constants.NOTIFY_REVIEW_REQUEST, False)):
        raise BadRequestError('Review requests are not enabled')
    return request_type


def get_available_statuses(request_type) -> list:
    """Statuses in which a new request of this type may be sent."""
    if request_type == constants.REQUEST_TYPE_REVIEW:
        return current_app.config.get(
            "NOTIFY_REVIEW_AVAILABLE_ACTORS",
            [constants.WORKFLOW_STATUS_TENTATIVE_REJECT, constants.WORKFLOW_STATUS_AVAILABLE])
    return current_app.config.get(
        "NOTIFY_ENDORSEMENT_AVAILABLE_ACTORS",
        [constants.WORKFLOW_STATUS_TENTATIVE_REJECT, constants.WORKFLOW_STATUS_AVAILABLE])


@unit_of_work()
def create_endorsement_request_record(endorsement_request_data, record_id, user_id, actor_id,
                                      request_type=constants.REQUEST_TYPE_ENDORSEMENT, uow=None):
    """Create endorsement or review request database record.
    
    Args:
        endorsement_request_data: The COAR Notify Offer that was sent
        record_id: ID of the record
        user_id: ID of the user making the request
        actor_id: ID of the actor
        request_type: 'endorsement' or 'review'
        
    Returns:
        EndorsementRequestModel: The created request record
    """
    return EndorsementRequestModel.create({
        "notification_id": endorsement_request_data.id,
        "record_id": record_id,
        "user_id": user_id,
        "actor_id": actor_id,
        "raw": endorsement_request_data.to_jsonld(),
        "request_type": request_type,
        "latest_status": constants.REQUEST_TYPE_INITIAL_STATUS[request_type],
    })


def send_to_actor_inbox(actor, endorsement_request_data):
    """Send endorsement request to actor's inbox.
    
    Args:
        actor: ActorModel instance
        endorsement_request_data: Dictionary containing endorsement request data
        
    Returns:
        str: Error message if validation fails, None if successful
    """
    if not actor.inbox_url:
        current_app.logger.error(
            f'Actor inbox URL is not configured for actor {actor.id} url[{actor.inbox_url}]'
        )
        raise ValueError('Actor inbox URL is not configured')

    try:
        headers = {'Content-Type': 'application/ld+json'}
        if actor.inbox_api_token:
            headers['Authorization'] = f'Bearer {actor.inbox_api_token}'
        response = requests.post(
            actor.inbox_url,
            json=endorsement_request_data.to_jsonld(),
            headers=headers,
            timeout=30
        )

    except requests.exceptions.RequestException as e:
        current_app.logger.error(f'Failed to send request to actor inbox: {e}')
        raise SendRequestFail('Failed to send request')

    if response.status_code not in {200, 201, 202}:
        current_app.logger.warning(
            f'Actor inbox request failed with '
            f'status code [{response.status_code}] for actor [{actor.inbox_url}]'
            f' -- {response.text}'
        )
        raise SendRequestFail('Actor reply invalid request', status=response.status_code)

    return None


class EndorsementRequestResource(ApiErrorHandlersMixin, Resource):
    """
    Resource for handling endorsement requests.

    Api that used by frontend landing page "Endorsement Request" section.
    """

    def create_url_rules(self):
        """Create the URL rules for the endorsement request resource."""
        return [
            route("POST", self.config.routes["send"], self.send, ),
            route("GET", self.config.routes["actors"], self.list_actors, ),
        ]

    @request_view_args
    @request_data
    @response_handler()
    def send(self):
        """Send an endorsement or review request.

        Body: {"actor_id": <int>, "request_type": "endorsement" | "review"}
        (request_type defaults to "endorsement").
        """
        data = resource_requestctx.data
        pid_value = resource_requestctx.view_args["pid_value"]

        if not data or 'actor_id' not in data:
            raise ValueError('actor_id is required')
        if g.identity is None or g.identity.id is None:
            raise BadRequestError('User identity is required')

        actor_id = data['actor_id']
        request_type = get_request_type(data.get('request_type'))

        actor = ActorModel.query.filter_by(id=actor_id).one()  # NoResultFound -> 404

        if not actor or not actor.inbox_url or not actor.supports(request_type):
            raise BadRequestError(f'Actor not available for {request_type} request')

        record: RecordItem = record_utils.read_record_item(system_identity, pid_value)
        user = User.query.get(g.identity.id)

        validate_owner_id(record._record, user.id)

        record_id = record._record.model.id

        # Same availability rules as the actor list shown in the sidebar
        available = {a['actor_id']: a['status']
                     for a in ActorModel.get_available_actors(record_id, request_type)}
        if actor.id not in available or available[actor.id] not in get_available_statuses(request_type):
            raise BadRequestError(f'Actor not available for {request_type} request')

        offer = create_request_data(user, record, actor, request_type)
        send_to_actor_inbox(actor, offer)

        try:
            create_endorsement_request_record(offer, record._record.model.id, user.id, actor_id, request_type)
            current_app.logger.info(f'Created {request_type} request record for actor {actor_id}')
        except Exception as e:
            current_app.logger.error(f'Failed to create {request_type} request record: {e}')
            raise Exception(f'Failed to create {request_type} request record') from e

        return {'is_success': 1, 'message': 'Request Accepted'}, 200

    @request_view_args
    @response_handler()
    def list_actors(self):
        """List the actors a request can be sent to, with their status.

        Query arg: request_type=endorsement|review (defaults to endorsement).
        """
        pid_value = resource_requestctx.view_args["pid_value"]
        record = resolve_record_from_pid(pid_value)

        if g.identity is None or g.identity.id is None:
            raise BadRequestError('User identity is required')

        user_id = g.identity.id

        validate_owner_id(record, user_id)

        request_type = get_request_type(request.args.get('request_type'))
        actors = ActorModel.get_available_actors(record.id, request_type)
        return actors, 200