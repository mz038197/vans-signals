# This service connects only to vans_signals

vans-signals keeps using the database `vans_signals` in the Neon project VCRouter-db. Today the branch has one role, `neondb_owner`, and this service uses it, so that credential can also open the router's `neondb`. This service switches to a role that can connect to `vans_signals` only. A new role name is not the boundary. CONNECT is revoked from PUBLIC, the role is not a member of `neon_superuser`, and it is granted CONNECT only on `vans_signals`. Acceptance is a real attempt to connect to `neondb` with that role, and the attempt is refused. The role's name, and where this switch sits beside the MCP cutover, are not decided here.

## Considered Options

- **Keep `neondb_owner`**: rejected. That role owns every database on the branch.
- **A second role that is still a superuser, or that relies on PUBLIC CONNECT**: rejected. Either one can still open `neondb`.
