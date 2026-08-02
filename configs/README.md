# configs/

Each service manages its own `.env` (copy each service's `.env.example` into
that service's own directory — that's where it actually gets loaded from).
This folder is just a **quick-reference summary** of the two so you can see
at a glance how they're meant to line up. It is not read by either service.

- `ports.reference.env` — the two ports/URLs that must agree with each other
  for `backend` to reach `ml-service`.

If you change either service's port, update both `<service>/.env` and this
file so they don't silently drift apart again.
