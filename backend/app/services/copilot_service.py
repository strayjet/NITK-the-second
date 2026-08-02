"""
CopilotService — CityMind's AI Copilot.

Implements a tool/function-calling architecture: the incoming natural
language message (plus optional structured `context`) is classified into an
intent, mapped to one or more *tools* (plain async callables with a declared
name + JSON-serializable arguments/results, mirroring the shape of an LLM
function-calling loop), and the tool results are assembled into a single
structured JSON response.

Two intent-classification backends are supported:

- **LLM-backed** (used automatically when `GEMINI_API_KEY` is set in the
  environment): the message is sent to the Gemini API (`generateContent`)
  with the tool schemas below as `function_declarations`, and Gemini decides
  which tool(s) to call and with what arguments — the real function-calling
  loop.
- **Deterministic fallback** (used otherwise, e.g. offline/dev/CI, or if the
  Gemini call fails/times out for any reason): a lightweight rule-based
  classifier extracts coordinates, node ids, and keywords from the message.
  This keeps `/copilot/chat` fully functional with zero external
  dependencies or API keys.

Either way, the *execution* of a tool call always goes through the same
`_TOOLS` dispatch table, which calls straight into `CityService` and the
repository layer — there is no duplicate business logic between backends.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.facility_repository import FacilityRepository
from app.repositories.incident_repository import IncidentRepository
from app.schemas.copilot import CopilotChatResponse, ToolCall
from app.services.city_service import CityLogicComputationError, CityLogicNotReadyError, CityService

logger = logging.getLogger(__name__)

_COORD_PAIR_RE = re.compile(
    r"(-?\d{1,3}(?:\.\d+)?)\s*[,;]\s*(-?\d{1,3}(?:\.\d+)?)"
)
_EDGE_RE = re.compile(r"\bedge\s*[:=]?\s*\(?\s*(\d+)\s*[,\-]\s*(\d+)\s*\)?", re.IGNORECASE)
_NCAND_RE = re.compile(r"(\d+)\s*(?:candidates|sites|options)", re.IGNORECASE)
_INCIDENT_ID_RE = re.compile(r"incident[_\s]?(?:id)?\s*[:#]?\s*([a-zA-Z0-9\-]{6,})", re.IGNORECASE)
_CAMERA_RE = re.compile(r"(?:camera|cam)[_\s]?(?:id)?\s*[:#]?\s*([a-zA-Z0-9_\-]+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Tool schemas (shared by both the LLM backend and the docs / OpenAPI-style
# description of the copilot's capabilities).
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "plan_route",
        "description": "Compute the population-aware shortest route between two lat/lon coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                "destination": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            },
            "required": ["origin", "destination"],
        },
    },
    {
        "name": "recommend_facility",
        "description": "Recommend the best new facility site (e.g. hospital, fire station) to minimize population-weighted access cost.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n_candidates": {"type": "integer"},
                "facility_type": {"type": "string"},
            },
        },
    },
    {
        "name": "simulate_closure",
        "description": "Simulate closing a specific road edge and compare the route/detour impact before and after.",
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                "destination": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                "edge_to_close": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
            },
            "required": ["origin", "destination", "edge_to_close"],
        },
    },
    {
        "name": "lookup_incidents",
        "description": "Look up recent traffic incidents, optionally filtered by camera id or status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "camera_id": {"type": "string"},
                "status": {"type": "string", "enum": ["ACTIVE", "CLOSED"]},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "emergency_response_analysis",
        "description": (
            "Given an active incident (or a raw location), find the nearest recommended/placed facility, "
            "compute the fastest route + ETA from that facility to the incident, and summarize dispatch risk."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
                "location": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            },
        },
    },
]

# Assumed average urban emergency-response driving speed, for ETA estimates.
_AVG_SPEED_KMPH = 35.0


class CopilotService:
    """Stateless per-request orchestrator; holds no mutable state of its own."""

    def __init__(self, city_service: CityService, db: AsyncSession) -> None:
        self.city_service = city_service
        self.db = db
        self._tools: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {
            "plan_route": self._tool_plan_route,
            "recommend_facility": self._tool_recommend_facility,
            "simulate_closure": self._tool_simulate_closure,
            "lookup_incidents": self._tool_lookup_incidents,
            "emergency_response_analysis": self._tool_emergency_response_analysis,
        }

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    async def chat(self, message: str, context: dict[str, Any] | None = None) -> CopilotChatResponse:
        context = context or {}
        plan = await self._classify(message, context)
        intent: str = plan["intent"]
        calls: list[ToolCall] = []
        results: dict[str, Any] = {}
        warnings: list[str] = []

        for step in plan["tool_calls"]:
            tool_name = step["tool"]
            args = step.get("arguments", {})
            fn = self._tools.get(tool_name)
            if fn is None:
                warnings.append(f"unknown tool '{tool_name}' — skipped")
                continue
            try:
                result = await fn(args)
                calls.append(ToolCall(tool=tool_name, arguments=args, result=result))
                results[tool_name] = result
            except (CityLogicNotReadyError, CityLogicComputationError) as exc:
                calls.append(ToolCall(tool=tool_name, arguments=args, error=str(exc)))
                warnings.append(f"{tool_name}: {exc}")
            except Exception as exc:  # noqa: BLE001 - never let one bad tool call 500 the copilot
                logger.exception("copilot tool '%s' failed", tool_name)
                calls.append(ToolCall(tool=tool_name, arguments=args, error=str(exc)))
                warnings.append(f"{tool_name}: unexpected error ({exc})")

        reply = self._compose_reply(intent, calls, warnings)

        return CopilotChatResponse(
            intent=intent,  # type: ignore[arg-type]
            reply=reply,
            tool_calls=calls,
            data=results or None,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Intent classification
    # ------------------------------------------------------------------

    async def _classify(self, message: str, context: dict[str, Any]) -> dict[str, Any]:
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            try:
                return await self._classify_with_llm(message, context, api_key)
            except Exception:  # noqa: BLE001 - always fall back rather than fail the request
                logger.exception("LLM-backed copilot classification failed, falling back to rules")
        return self._classify_with_rules(message, context)

    async def _classify_with_llm(self, message: str, context: dict[str, Any], api_key: str) -> dict[str, Any]:
        """Real function-calling loop against the Gemini API."""
        import httpx

        system = (
            "You are CityMind's routing/dispatch tool router. Given the operator's message, decide which "
            "tool(s) to call to answer it. Always prefer calling a tool over answering in free text. "
            f"Known context (may fill in missing arguments): {json.dumps(context)}"
        )
        # Gemini's function-calling schema is FunctionDeclaration-shaped: {name, description,
        # parameters} rather than Anthropic's {name, description, input_schema}. Translate once here
        # so TOOL_SCHEMAS stays the single source of truth for both backends and for /copilot/tools.
        function_declarations = [
            {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}
            for t in TOOL_SCHEMAS
        ]
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.0-flash:generateContent",
                headers={"content-type": "application/json"},
                params={"key": api_key},
                json={
                    "system_instruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": message}]}],
                    "tools": [{"function_declarations": function_declarations}],
                },
            )
            resp.raise_for_status()
            data = resp.json()

        parts = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        tool_calls = [
            {"tool": part["functionCall"]["name"], "arguments": part["functionCall"].get("args", {})}
            for part in parts
            if "functionCall" in part
        ]
        if not tool_calls:
            return {"intent": "UNKNOWN", "tool_calls": []}

        intent_map = {
            "plan_route": "ROUTE_PLANNING",
            "recommend_facility": "FACILITY_RECOMMENDATION",
            "simulate_closure": "CLOSURE_SIMULATION",
            "lookup_incidents": "INCIDENT_LOOKUP",
            "emergency_response_analysis": "EMERGENCY_RESPONSE",
        }
        intent = intent_map.get(tool_calls[0]["tool"], "UNKNOWN")
        return {"intent": intent, "tool_calls": tool_calls}

    def _classify_with_rules(self, message: str, context: dict[str, Any]) -> dict[str, Any]:
        text = message.lower()
        coords = [(float(a), float(b)) for a, b in _COORD_PAIR_RE.findall(message)]
        origin = context.get("origin") or (list(coords[0]) if len(coords) >= 1 else None)
        destination = context.get("destination") or (list(coords[1]) if len(coords) >= 2 else None)

        edge_match = _EDGE_RE.search(message)
        edge_to_close = context.get("edge_to_close") or (
            [int(edge_match.group(1)), int(edge_match.group(2))] if edge_match else None
        )

        is_emergency = any(
            kw in text for kw in ("emergency", "dispatch", "ambulance", "fire truck", "urgent response")
        )
        is_closure = any(kw in text for kw in ("close", "closure", "closed", "block", "blocked")) or edge_to_close
        is_facility = any(
            kw in text
            for kw in ("facility", "hospital", "fire station", "police station", "build a", "new station", "site")
        )
        is_incident = any(kw in text for kw in ("incident", "accident", "crash", "collision")) and not is_emergency
        is_route = any(kw in text for kw in ("route", "path", "navigate", "directions", "how do i get", "fastest way"))

        if is_emergency:
            incident_id_match = _INCIDENT_ID_RE.search(message)
            location = destination or origin
            return {
                "intent": "EMERGENCY_RESPONSE",
                "tool_calls": [
                    {
                        "tool": "emergency_response_analysis",
                        "arguments": {
                            "incident_id": context.get("incident_id") or (incident_id_match.group(1) if incident_id_match else None),
                            "location": location,
                        },
                    }
                ],
            }

        if is_closure and origin and destination:
            return {
                "intent": "CLOSURE_SIMULATION",
                "tool_calls": [
                    {
                        "tool": "simulate_closure",
                        "arguments": {"origin": origin, "destination": destination, "edge_to_close": edge_to_close},
                    }
                ],
            }

        if is_facility:
            n_match = _NCAND_RE.search(message)
            return {
                "intent": "FACILITY_RECOMMENDATION",
                "tool_calls": [
                    {
                        "tool": "recommend_facility",
                        "arguments": {
                            "n_candidates": int(n_match.group(1)) if n_match else context.get("n_candidates"),
                            "facility_type": context.get("facility_type", "hospital"),
                        },
                    }
                ],
            }

        if is_incident:
            camera_match = _CAMERA_RE.search(message)
            status = "ACTIVE" if "active" in text else ("CLOSED" if "closed" in text or "resolved" in text else None)
            return {
                "intent": "INCIDENT_LOOKUP",
                "tool_calls": [
                    {
                        "tool": "lookup_incidents",
                        "arguments": {
                            "camera_id": camera_match.group(1) if camera_match else context.get("camera_id"),
                            "status": status,
                            "limit": context.get("limit", 10),
                        },
                    }
                ],
            }

        if is_route and origin and destination:
            return {
                "intent": "ROUTE_PLANNING",
                "tool_calls": [{"tool": "plan_route", "arguments": {"origin": origin, "destination": destination}}],
            }

        return {"intent": "UNKNOWN", "tool_calls": []}

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _tool_plan_route(self, args: dict[str, Any]) -> dict[str, Any]:
        origin, destination = self._require_coords(args)
        route, length_m, coords = await self.city_service.get_route(tuple(origin), tuple(destination))
        return {
            "distance_km": round(length_m / 1000.0, 3),
            "node_count": len(route),
            "route_coordinates": coords,
        }

    async def _tool_recommend_facility(self, args: dict[str, Any]) -> dict[str, Any]:
        n_candidates = args.get("n_candidates")
        facility_type = args.get("facility_type") or "hospital"
        result = await self.city_service.recommend_facility(
            n_candidates=n_candidates, facility_type=facility_type
        )
        if result.get("status") == "no_valid_site":
            return {
                "status": "no_valid_site",
                "detail": result.get("detail"),
                "facility_type": facility_type,
            }
        return {
            "node_id": str(result["node_id"]),
            "lat": result["lat"],
            "lon": result["lon"],
            "cost": result["cost"],
            "facility_type": result.get("facility_type", facility_type),
            "label": result.get("label"),
            "color": result.get("color"),
            "min_spacing_km": result.get("min_spacing_km"),
            "existing_facilities_considered": result.get("existing_facilities_considered"),
        }

    async def _tool_simulate_closure(self, args: dict[str, Any]) -> dict[str, Any]:
        origin, destination = self._require_coords(args)
        edge = args.get("edge_to_close")
        if not edge or len(edge) != 2:
            raise CityLogicComputationError(
                "an edge_to_close=[u, v] node-id pair is required to simulate a closure"
            )
        result = await self.city_service.simulate_closure(tuple(origin), tuple(destination), (int(edge[0]), int(edge[1])))
        if "route_before" not in result:
            return {"status": result.get("status", "no alternative route exists")}
        return {
            "status": "OK",
            "length_before_km": round(result["length_before_km"], 3),
            "length_after_km": round(result["length_after_km"], 3),
            "delay_km": round(result["delay_km"], 3),
            "eta_delay_minutes": round((result["delay_km"] / _AVG_SPEED_KMPH) * 60, 1),
        }

    async def _tool_lookup_incidents(self, args: dict[str, Any]) -> dict[str, Any]:
        repo = IncidentRepository(self.db)
        rows, total = await repo.list_filtered(
            camera_id=args.get("camera_id"),
            status=args.get("status"),
            limit=args.get("limit", 10) or 10,
            offset=0,
        )
        return {
            "total_matching": total,
            "incidents": [
                {
                    "incident_id": r.incident_id,
                    "status": r.status,
                    "camera_id": r.camera_id,
                    "road_id": r.road_id,
                    "lat": r.lat,
                    "lng": r.lng,
                    "start_time": r.start_time.isoformat(),
                    "emergency_vehicle_present": r.emergency_vehicle_present,
                    "max_confidence": r.max_confidence,
                }
                for r in rows
            ],
        }

    async def _tool_emergency_response_analysis(self, args: dict[str, Any]) -> dict[str, Any]:
        incident_repo = IncidentRepository(self.db)
        facility_repo = FacilityRepository(self.db)

        location = args.get("location")
        incident = None
        if args.get("incident_id"):
            incident = await incident_repo.get(args["incident_id"])
            if incident and incident.lat is not None and incident.lng is not None:
                location = [incident.lat, incident.lng]

        if location is None:
            active = await incident_repo.list_filtered(status="ACTIVE", offset=0, limit=1)
            rows, _ = active
            if rows and rows[0].lat is not None:
                incident = rows[0]
                location = [rows[0].lat, rows[0].lng]

        if location is None:
            raise CityLogicComputationError(
                "no incident location available — provide an incident_id or a [lat, lon] location"
            )

        facilities, _ = await facility_repo.list_filtered(offset=0, limit=200)
        if not facilities:
            return {
                "incident_location": location,
                "nearest_facility": None,
                "warning": "no facilities on record — cannot compute dispatch route",
            }

        # Nearest facility by straight-line distance first (cheap pre-filter),
        # then confirm with an actual network route.
        def _haversine_km(a: list[float], b: tuple[float, float]) -> float:
            import math

            lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
            dlat, dlon = lat2 - lat1, lon2 - lon1
            h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
            return 2 * 6371.0 * math.asin(min(1.0, h ** 0.5))

        nearest = min(facilities, key=lambda f: _haversine_km(location, (f.lat, f.lon)))

        route_info: dict[str, Any] = {}
        try:
            route, length_m, coords = await self.city_service.get_route((nearest.lat, nearest.lon), tuple(location))
            eta_minutes = round((length_m / 1000.0 / _AVG_SPEED_KMPH) * 60, 1)
            route_info = {
                "distance_km": round(length_m / 1000.0, 3),
                "eta_minutes": eta_minutes,
                "route_coordinates": coords,
                "risk_level": "HIGH" if eta_minutes > 15 else ("MEDIUM" if eta_minutes > 7 else "LOW"),
            }
        except (CityLogicNotReadyError, CityLogicComputationError) as exc:
            route_info = {"error": str(exc)}

        return {
            "incident_id": incident.incident_id if incident else None,
            "incident_location": location,
            "nearest_facility": {
                "id": nearest.id,
                "name": nearest.name,
                "facility_type": nearest.facility_type,
                "lat": nearest.lat,
                "lon": nearest.lon,
            },
            "dispatch": route_info,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_coords(args: dict[str, Any]) -> tuple[list[float], list[float]]:
        origin, destination = args.get("origin"), args.get("destination")
        if not origin or not destination or len(origin) != 2 or len(destination) != 2:
            raise CityLogicComputationError("both origin=[lat, lon] and destination=[lat, lon] are required")
        return origin, destination

    @staticmethod
    def _compose_reply(intent: str, calls: list[ToolCall], warnings: list[str]) -> str:
        if intent == "UNKNOWN" or not calls:
            return (
                "I can help with route planning, facility recommendations, road-closure simulation, "
                "incident lookup, and emergency response analysis. Try: \"plan a route from 19.07,72.87 to "
                "19.09,72.83\" or \"what's the fastest way to dispatch to the nearest incident?\""
            )

        successful = [c for c in calls if c.error is None]
        if not successful:
            return "I attempted to help but every tool call failed: " + "; ".join(warnings)

        summaries = []
        for call in successful:
            r = call.result or {}
            if call.tool == "plan_route":
                summaries.append(f"Route found: {r.get('distance_km')} km across {r.get('node_count')} nodes.")
            elif call.tool == "recommend_facility":
                if r.get("status") == "no_valid_site":
                    summaries.append(f"No valid {r.get('facility_type', 'facility')} site found: {r.get('detail')}")
                else:
                    summaries.append(
                        f"Recommended {r.get('label') or r.get('facility_type') or 'facility'} site at "
                        f"({r.get('lat'):.5f}, {r.get('lon'):.5f}) with access cost {r.get('cost'):.1f}."
                    )
            elif call.tool == "simulate_closure":
                if r.get("status") and r.get("status") != "OK":
                    summaries.append(f"Closure simulation: {r.get('status')}.")
                else:
                    summaries.append(
                        f"Closure adds {r.get('delay_km')} km ({r.get('eta_delay_minutes')} min) of detour."
                    )
            elif call.tool == "lookup_incidents":
                summaries.append(f"Found {r.get('total_matching')} matching incident(s).")
            elif call.tool == "emergency_response_analysis":
                dispatch = r.get("dispatch", {})
                if "eta_minutes" in dispatch:
                    summaries.append(
                        f"Nearest facility is {dispatch.get('distance_km')} km away — ETA {dispatch.get('eta_minutes')} min "
                        f"({dispatch.get('risk_level')} risk)."
                    )
                else:
                    summaries.append("Located nearest facility, but could not compute a live route.")
        if warnings:
            summaries.append("(Note: " + "; ".join(warnings) + ")")
        return " ".join(summaries)
