"""
citymind-backend

The Event Aggregator layer of CityMind. Sits between the citymind-yolo-service
(pure computer-vision microservice) and the downstream traffic-simulation /
infrastructure-planning / AI-copilot layers.

This package owns exactly one responsibility: turn frame-level detection
events coming out of the YOLO service into incident-level events (e.g. many
consecutive "accident" frames collapsed into a single Incident with a start
time, an end time, and summary statistics).

It does not run any computer vision itself, and it does not implement
traffic simulation, infrastructure planning, or AI-copilot logic — those are
separate, later layers in the CityMind architecture.
"""

__version__ = "1.0.0"
