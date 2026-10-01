"""Cross-cutting primitives that are not a service and not a model.

`money` is the first and, for now, the only member. It lives here rather than
under `services/` because it has no dependencies of its own — nothing in it
reaches for a session, a request or a setting — and because CLAUDE.md rule 1
names this exact path as the one place rounding is allowed to happen.
"""
