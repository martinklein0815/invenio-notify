# Invenio Notify

**IMPORTANT NOTE**:  This repository was used for development of this integration for InvenioRDM.  The code
has now been moved to the Invenio Software github space, and you should look at [https://github.com/inveniosoftware/invenio-notify](https://github.com/inveniosoftware/invenio-notify) for the latest code and documentation.



## Fork: review requests

This fork adds COAR Notify **review requests** to the extension, alongside the
existing endorsement requests. It is based on CottageLabs/invenio-notify
`f9b2e5c` and was tested with invenio-app-rdm 14.0.0 and invenio-rdm-records 32.2.0.

### What it adds

- **Send review requests.** Record owners see a "Review request" section in
  the record sidebar and can send an Offer with `coar-notify:ReviewAction`
  to a review service (for example Kotahi).
- **Resource item URI and type.** Endorsement and review requests describe
  the record as its landing page (`Page`, `sorg:AboutPage`) with
  `ietf:cite-as` (the DOI when there is one) and an `ietf:item` that points
  to the main file, with its `mediaType` and a type derived from the
  record's resource type. Records without public files use the landing page
  as the item.
- **Receive review announcements.** Accept, TentativeAccept, Reject,
  TentativeReject and Announce replies update the request. An announcement
  whose `inReplyTo` is unknown is stored as an unsolicited review. Reviews
  are shown under "Reviews and endorsements" on the record page.
- **Robust reply matching.** The request is stored before it is sent, so a
  service that replies while still handling the Offer finds it. Replies are
  matched through `inReplyTo` when they carry no record URL, and record ids
  are also resolved from file URLs (`/records/<id>/files/<key>`).

### Upgrading

1. Run the migration on the `invenio_notify` branch:

   ```
   invenio alembic upgrade invenio_notify@1794700000
   ```

   It adds `notify_endorsement_request.request_type` (default
   `endorsement`) and `notify_actor.capabilities`.
2. Rebuild the assets (`invenio webpack buildall`), since the landing page
   JavaScript changed.
3. In the administration panel, edit each review service actor and set
   "Accepts requests for" to include `review`. Actors without capabilities
   keep accepting endorsement requests only, so nothing changes until you
   do this.

### Configuration

| Setting | Default | Purpose |
|---|---|---|
| `NOTIFY_REVIEW_REQUEST` | `True` | Turns the review request section on or off. |
| `NOTIFY_REVIEW_STATUS_LABELS` | see `config.py` | Labels for review request statuses. |
| `NOTIFY_REVIEW_AVAILABLE_ACTORS` | tentative_reject, available, send_failed | Statuses in which a new review request may be sent. |
| `NOTIFY_RESOURCE_TYPE_MAP` | `None` | Maps resource type ids to the `ietf:item` type; `None` uses the built-in map. |
