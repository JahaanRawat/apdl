"""Event batch and single-event validation matching C++ schema.cpp exactly.

All validation rules, error messages, and field names are ported 1:1 from the
C++ implementation to ensure identical behaviour.
"""

MAX_BATCH_SIZE = 500
MAX_EVENT_NAME_LENGTH = 256
MAX_PROPERTY_KEY_LENGTH = 256
MAX_STRING_PROPERTY_LENGTH = 8192

VALID_EVENT_TYPES = {"track", "identify", "group", "page", "screen", "alias"}


def validate_event_batch(body: object) -> dict:
    """Validate a full event batch payload.

    Returns {"valid": bool, "errors": [{"field": str, "message": str}, ...]}.
    """
    result: dict = {"valid": True, "errors": []}

    if not isinstance(body, dict):
        result["valid"] = False
        result["errors"].append({"field": "body", "message": "Request body must be a JSON object"})
        return result

    if "events" not in body:
        result["valid"] = False
        result["errors"].append({"field": "events", "message": "Missing required field 'events'"})
        return result

    events = body["events"]

    if not isinstance(events, list):
        result["valid"] = False
        result["errors"].append({"field": "events", "message": "Field 'events' must be an array"})
        return result

    if len(events) == 0:
        result["valid"] = False
        result["errors"].append({"field": "events", "message": "Events array must not be empty"})
        return result

    if len(events) > MAX_BATCH_SIZE:
        result["valid"] = False
        result["errors"].append({
            "field": "events",
            "message": f"Batch size {len(events)} exceeds maximum of {MAX_BATCH_SIZE}",
        })
        return result

    for i, event in enumerate(events):
        event_result = validate_single_event(event)
        if not event_result["valid"]:
            result["valid"] = False
            prefix = f"events[{i}]."
            for err in event_result["errors"]:
                result["errors"].append({
                    "field": prefix + err["field"],
                    "message": err["message"],
                })

    return result


def validate_single_event(event: object) -> dict:
    """Validate a single event object.

    Returns {"valid": bool, "errors": [{"field": str, "message": str}, ...]}.
    """
    result: dict = {"valid": True, "errors": []}

    if not isinstance(event, dict):
        result["valid"] = False
        result["errors"].append({"field": "", "message": "Event must be a JSON object"})
        return result

    # Check for event name or type
    has_event_name = isinstance(event.get("event"), str)
    has_type = isinstance(event.get("type"), str)

    if not has_event_name and not has_type:
        result["valid"] = False
        result["errors"].append({
            "field": "event",
            "message": "Event must have either 'event' (name) or 'type' field",
        })

    # Validate event name length
    if has_event_name:
        name = event["event"]
        if name == "":
            result["valid"] = False
            result["errors"].append({"field": "event", "message": "Event name must not be empty"})
        elif len(name) > MAX_EVENT_NAME_LENGTH:
            result["valid"] = False
            result["errors"].append({
                "field": "event",
                "message": f"Event name exceeds maximum length of {MAX_EVENT_NAME_LENGTH}",
            })

    # Validate type if present
    if has_type:
        event_type = event["type"]
        if event_type not in VALID_EVENT_TYPES:
            result["valid"] = False
            result["errors"].append({
                "field": "type",
                "message": f"Invalid event type '{event_type}'. Must be one of: track, identify, group, page, screen, alias",
            })

    # Must have user_id or anonymous_id (snake_case or camelCase)
    has_user_id = isinstance(event.get("user_id"), str) and len(event["user_id"]) > 0
    has_anon_id = isinstance(event.get("anonymous_id"), str) and len(event["anonymous_id"]) > 0
    has_userId = isinstance(event.get("userId"), str) and len(event["userId"]) > 0
    has_anonymousId = isinstance(event.get("anonymousId"), str) and len(event["anonymousId"]) > 0

    if not has_user_id and not has_anon_id and not has_userId and not has_anonymousId:
        result["valid"] = False
        result["errors"].append({
            "field": "user_id",
            "message": "Event must have either 'user_id'/'userId' or 'anonymous_id'/'anonymousId'",
        })

    # Validate timestamp if present
    if "timestamp" in event:
        if not isinstance(event["timestamp"], str):
            result["valid"] = False
            result["errors"].append({
                "field": "timestamp",
                "message": "Timestamp must be a string in ISO 8601 format",
            })

    # Validate properties if present
    if "properties" in event:
        if not isinstance(event["properties"], dict):
            result["valid"] = False
            result["errors"].append({
                "field": "properties",
                "message": "Properties must be a JSON object",
            })
        else:
            for key, value in event["properties"].items():
                if len(key) > MAX_PROPERTY_KEY_LENGTH:
                    result["valid"] = False
                    result["errors"].append({
                        "field": f"properties.{key}",
                        "message": f"Property key exceeds maximum length of {MAX_PROPERTY_KEY_LENGTH}",
                    })
                if isinstance(value, str) and len(value) > MAX_STRING_PROPERTY_LENGTH:
                    result["valid"] = False
                    result["errors"].append({
                        "field": f"properties.{key}",
                        "message": f"String property value exceeds maximum length of {MAX_STRING_PROPERTY_LENGTH}",
                    })

    # Validate traits if present
    if "traits" in event:
        if not isinstance(event["traits"], dict):
            result["valid"] = False
            result["errors"].append({"field": "traits", "message": "Traits must be a JSON object"})

    # Validate context if present
    if "context" in event:
        if not isinstance(event["context"], dict):
            result["valid"] = False
            result["errors"].append({"field": "context", "message": "Context must be a JSON object"})

    return result
