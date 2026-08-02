"""
Reserved for the Traffic Simulation layer of CityMind.

citymind-backend's job in the CityMind pipeline is strictly the Event
Aggregator (turning YOLO frame-level detections into incident-level
events). Traffic simulation is the next layer up the stack and is
intentionally NOT implemented here — this package exists only so the
directory structure is ready for it, and to give the Event Aggregator a
stable module path (`app.simulation`) to publish incidents into once that
layer exists.
"""
