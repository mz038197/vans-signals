# vans-signals

Keeps a Signal from a calling service, then shows it in Slack.

## Language

**Signal**:
One immutable row: log time, logger name, level, message, and source. It has no field for whether Slack accepted it. The source is the service named by the bearer token, not a service the body claims on its own. A missing or unknown token stores nothing. A body source that does not match the token stores nothing and is not sent to Slack.
_Avoid_: Incident, delivery status, deduplicated alert, recovery

**Log time**:
When the calling service emitted the log. It is stored as that instant and shown in Slack with the logger name, level, and message.
_Avoid_: The time Slack was contacted, a vans-signals receipt time

**Source**:
The service named by the caller's bearer token. For this version the production router token names `vans-coding-router`.
_Avoid_: A body field the caller can set to a different service, a teacher, a student
