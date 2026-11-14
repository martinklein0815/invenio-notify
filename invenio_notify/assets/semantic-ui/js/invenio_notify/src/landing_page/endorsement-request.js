/*
 * Copyright (C) 2025-2026 Cottage Labs.
 *
 * Invenio-Notify is free software; you can redistribute it and/or modify
 * it under the terms of the MIT License; see LICENSE file for more details.
 */

import React from "react";
import ReactDOM from "react-dom";
import { EndorsementRequestDropdown } from "./EndorsementRequestDropdown";

// One widget per request type: endorsement requests and review requests
["recordEndorsementRequest", "recordReviewRequest"].forEach((elementId) => {
    const div = document.getElementById(elementId);

    if (div
        && div.dataset.endorsementRequestEndpoint
        && div.dataset.actorOptionEndpoint) {
        ReactDOM.render(
            <EndorsementRequestDropdown
                requestType={div.dataset.requestType || "endorsement"}
                endorsementRequestEndpoint={div.dataset.endorsementRequestEndpoint}
                actorOptionEndpoint={div.dataset.actorOptionEndpoint}
                availableActors={JSON.parse(div.dataset.availableActors)}
                statusLabels={JSON.parse(div.dataset.statusLabels)}
            />,
            div
        );
    }
});
