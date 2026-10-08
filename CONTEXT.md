# vans-signals

Keeps a Signal from a calling service, then shows it in Slack.

## Language

**Signal**:
One immutable row: log time, logger name, level, message, and source. It has no field for whether Slack accepted it. The source is the service named by the bearer token, not a service the body claims on its own. A missing or unknown token stores nothing. A body source that does not match the token stores nothing and is not sent to Slack.
_Avoid_: Incident, delivery status, deduplicated alert, recovery

**Log time**:
When the calling service emitted the log. It is stored as that instant and shown in Slack with the logger name, level, and message.
_Avoid_: The time Slack was contacted, a vans-signals receipt time

**Signal store**:
The database `vans_signals` in the Neon project VCRouter-db. The role this service uses can connect to that database and cannot connect to `neondb`. CONNECT is revoked from the public, and the role is not a superuser. Acceptance is a connection to `neondb` with that role being refused.
_Avoid_: the router's `neondb`, the shared owner role `neondb_owner`, a second role name that can still connect

**Source**:
The service named by the caller's bearer token. Each calling service has its own token. The production tokens name `vans-coding-router`, `vans-mcp-server`, and `pokemon-world-mcp`.
_Avoid_: A body field the caller can set to a different service, a teacher, a student, one shared token for every service
