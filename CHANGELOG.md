# Changelog

## Unreleased (fork: review-requests)

### Added
- Review requests: Offer with `coar-notify:ReviewAction`, a "Review request"
  section in the record sidebar, and the `NOTIFY_REVIEW_REQUEST` flag.
- Actor capabilities (`endorsement`, `review`), editable in the admin
  actor form. Actors without capabilities accept endorsement requests only.
- `ietf:item` with the main file URI, `mediaType` and a type derived from
  the record's resource type, in both endorsement and review requests
  (`NOTIFY_RESOURCE_TYPE_MAP`).
- Support for `Accept` replies, and storage of review announcements whose
  `inReplyTo` refers to an unknown request.
- `send_failed` request status, shown as "Sending failed", from which a
  request can be sent again.
- Alembic migration `1794700000`: `notify_endorsement_request.request_type`
  and `notify_actor.capabilities`.

### Changed
- Requests are stored before they are sent, so replies that arrive while
  the Offer is still being handled find their request.
- Replies without a record URL are matched to the record through the request
  referenced by `inReplyTo`.
- Record ids are resolved from file URLs (`/records/<id>/files/<key>`).
- Status emails and labels distinguish review and endorsement requests.
- The record sidebar heading is now "Reviews and endorsements".
- Test fixtures adapted to invenio-app-rdm 14.0.
