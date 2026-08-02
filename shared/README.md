# shared/

`backend/` and `ml-service/` are two independent, separately-deployable
Python projects. Neither imports code from the other, and neither will ever
import from this folder — that's intentional (see `backend/app/models/detection.py`'s
own docstring for why).

What actually keeps them compatible is the **wire contract**: the JSON shape
`ml-service` returns from `/detect/image`, `/detect/video`, `/detect/stream`,
and `/detect/webcam`, which `backend/app/models/detection.py` independently
mirrors as Pydantic models.

This folder exists to hold that contract as **documentation**, not code, so
if you change one side you know exactly what to update on the other:

- `detection_event.schema.json` — JSON Schema for a single `DetectionEvent`
  and the `VideoDetectionResponse` wrapper. Treat this as the source of
  truth for the contract. If you add/rename/remove a field in
  `ml-service/app/schemas.py`, update this file and then update
  `backend/app/models/detection.py` to match.

## Why not just import a shared package?

Two reasons, both deliberate for an MVP built by a solo developer:

1. **Independent deployability.** `ml-service` is a GPU-hungry CV workload;
   `backend` is a lightweight I/O-bound API. They will very likely end up
   on different machines/containers/scaling policies. A shared importable
   package would couple their deploy lifecycles together for no real
   benefit at this stage.
2. **A stable seam for future layers.** Everything above `backend` in the
   CityMind stack (Traffic Simulation, Infrastructure Planning, AI Copilot)
   will consume `backend`'s `Incident` model, not `ml-service`'s
   `DetectionEvent` model directly. Keeping the two schemas as separate,
   explicit files — rather than one shared import — makes it obvious which
   layer owns which shape, and makes it safe to evolve `Incident` later
   without touching `ml-service` at all.

If this project grows a real API gateway or you outgrow copy-paste
discipline, revisit this — a small `shared` Python package with just the
Pydantic models would be a reasonable next step then, not now.
