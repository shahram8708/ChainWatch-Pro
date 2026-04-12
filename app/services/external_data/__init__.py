"""External data integrations for weather, ports, and route events."""

from app.services.external_data.news_monitor_service import (
    generate_alert_description_with_gemini,
    get_route_event_risk,
    scan_all_active_routes,
)
from app.services.external_data.port_data_service import (
    get_customs_risk_score,
    get_port_congestion_score,
    get_port_congestion_zones,
)
from app.services.external_data.weather_service import (
    get_route_weather_risk,
    get_weather_alert_locations,
)

__all__ = [
    "get_route_weather_risk",
    "get_weather_alert_locations",
    "get_port_congestion_score",
    "get_customs_risk_score",
    "get_port_congestion_zones",
    "get_route_event_risk",
    "scan_all_active_routes",
    "generate_alert_description_with_gemini",
]
