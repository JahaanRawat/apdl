"""
Flink event enrichment job (Phase 3+).

Reads raw events from the events.raw Kafka topic, enriches them with:
  - Geo data: IP address -> country / region via MaxMind GeoIP2
  - Device classification: User-Agent -> device type / browser via ua-parser

Writes enriched events to the events.enriched Kafka topic.

Requires:
  - PyFlink 1.18+
  - GeoLite2 database file at GEOIP_DB_PATH
  - Kafka brokers at KAFKA_BROKERS
"""
import json
import logging
import os
from typing import Any

from pyflink.common import Row, Types, WatermarkStrategy
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.datastream.connectors.kafka import (
    KafkaOffsetsInitializer,
    KafkaRecordSerializationSchema,
    KafkaSource,
    KafkaSink,
)
from pyflink.datastream.functions import MapFunction, RuntimeContext

logger = logging.getLogger(__name__)

KAFKA_BROKERS = os.environ.get("KAFKA_BROKERS", "localhost:9092")
GEOIP_DB_PATH = os.environ.get("GEOIP_DB_PATH", "/data/GeoLite2-City.mmdb")
INPUT_TOPIC = "events.raw"
OUTPUT_TOPIC = "events.enriched"
CONSUMER_GROUP = "flink-event-enrichment"


class EventEnrichmentFunction(MapFunction):
    """Enriches raw events with geo and device information.

    This function is instantiated per Flink task slot and maintains its own
    GeoIP reader and UA parser instances for thread safety.
    """

    def __init__(self, geoip_db_path: str):
        self._geoip_db_path = geoip_db_path
        self._geoip_reader = None
        self._ua_parser = None

    def open(self, runtime_context: RuntimeContext):
        """Initialize enrichment resources when the task starts.

        Called once per parallel subtask instance. We lazily import here
        so that the driver program doesn't need these libraries installed.
        """
        try:
            import geoip2.database
            self._geoip_reader = geoip2.database.Reader(self._geoip_db_path)
            logger.info("GeoIP2 database loaded from %s", self._geoip_db_path)
        except Exception as e:
            logger.warning(
                "Failed to load GeoIP2 database from %s: %s. "
                "Geo enrichment will be skipped.",
                self._geoip_db_path,
                e,
            )
            self._geoip_reader = None

        try:
            from ua_parser import user_agent_parser
            self._ua_parser = user_agent_parser
            logger.info("UA parser loaded")
        except ImportError:
            logger.warning(
                "ua-parser not available. Device enrichment will be skipped."
            )
            self._ua_parser = None

    def close(self):
        """Release resources when the task stops."""
        if self._geoip_reader is not None:
            self._geoip_reader.close()
            self._geoip_reader = None

    def map(self, value: str) -> str:
        """Enrich a single event JSON string.

        Args:
            value: JSON-encoded raw event string.

        Returns:
            JSON-encoded enriched event string with country, region,
            device_type, and browser fields populated.
        """
        try:
            event = json.loads(value)
        except json.JSONDecodeError:
            logger.warning("Skipping malformed JSON: %s", value[:200])
            return value

        context = event.get("context", {})

        # --- Geo enrichment ---
        ip_address = context.get("ip")
        if ip_address and self._geoip_reader is not None:
            geo = self._lookup_geo(ip_address)
            event["country"] = geo.get("country", event.get("country", ""))
            event["region"] = geo.get("region", event.get("region", ""))
        else:
            event.setdefault("country", "")
            event.setdefault("region", "")

        # --- Device enrichment ---
        user_agent = context.get("user_agent", "")
        if user_agent and self._ua_parser is not None:
            device_info = self._parse_user_agent(user_agent)
            context["device_type"] = device_info.get(
                "device_type", context.get("device_type", "")
            )
            context["browser"] = device_info.get(
                "browser", context.get("browser", "")
            )
        else:
            context.setdefault("device_type", "")
            context.setdefault("browser", "")

        event["context"] = context
        event["_enriched"] = True

        return json.dumps(event)

    def _lookup_geo(self, ip_address: str) -> dict[str, str]:
        """Look up geographic information for an IP address.

        Returns a dict with 'country' and 'region' keys. On any lookup
        failure (private IPs, missing data), returns empty strings.
        """
        try:
            response = self._geoip_reader.city(ip_address)
            return {
                "country": (
                    response.country.iso_code
                    if response.country and response.country.iso_code
                    else ""
                ),
                "region": (
                    response.subdivisions.most_specific.iso_code
                    if response.subdivisions and response.subdivisions.most_specific
                    else ""
                ),
            }
        except Exception:
            # Private IPs, unknown IPs, or database errors
            return {"country": "", "region": ""}

    def _parse_user_agent(self, user_agent: str) -> dict[str, str]:
        """Parse a User-Agent string into device type and browser.

        Uses the ua-parser library to extract structured info, then
        classifies the device into mobile/tablet/desktop/bot/other.
        """
        try:
            parsed = self._ua_parser.Parse(user_agent)

            # Extract browser family and major version
            ua_family = parsed.get("user_agent", {}).get("family", "")
            ua_major = parsed.get("user_agent", {}).get("major", "")
            browser = f"{ua_family} {ua_major}".strip() if ua_family else ""

            # Classify device type
            device_family = parsed.get("device", {}).get("family", "").lower()
            os_family = parsed.get("os", {}).get("family", "").lower()

            device_type = self._classify_device(device_family, os_family, user_agent)

            return {"device_type": device_type, "browser": browser}
        except Exception:
            return {"device_type": "", "browser": ""}

    @staticmethod
    def _classify_device(
        device_family: str, os_family: str, user_agent: str
    ) -> str:
        """Classify a device into a category based on parsed UA components.

        Categories: mobile, tablet, desktop, bot, other.
        """
        ua_lower = user_agent.lower()

        # Bot detection
        bot_indicators = ["bot", "crawler", "spider", "scraper", "headless"]
        if any(indicator in ua_lower for indicator in bot_indicators):
            return "bot"
        if device_family == "spider":
            return "bot"

        # Tablet detection (check before mobile since tablets may match mobile patterns)
        tablet_indicators = ["ipad", "tablet", "kindle", "silk"]
        if any(indicator in ua_lower for indicator in tablet_indicators):
            return "tablet"

        # Mobile detection
        mobile_indicators = [
            "iphone", "android", "mobile", "windows phone",
            "blackberry", "opera mini", "opera mobi",
        ]
        if any(indicator in ua_lower for indicator in mobile_indicators):
            # Android without "mobile" could be a tablet
            if "android" in ua_lower and "mobile" not in ua_lower:
                return "tablet"
            return "mobile"

        # Desktop detection
        desktop_os = ["windows", "mac os x", "linux", "chrome os"]
        if any(dos in os_family for dos in desktop_os):
            return "desktop"

        return "other"


def build_pipeline():
    """Construct and execute the Flink enrichment pipeline.

    Pipeline topology:
        KafkaSource(events.raw)
        -> EventEnrichmentFunction (parallel map)
        -> KafkaSink(events.enriched)
    """
    env = StreamExecutionEnvironment.get_execution_environment()

    # Configure checkpointing for exactly-once semantics
    env.enable_checkpointing(60_000)  # Checkpoint every 60 seconds
    env.set_parallelism(int(os.environ.get("FLINK_PARALLELISM", "4")))

    # --- Kafka Source ---
    kafka_source = (
        KafkaSource.builder()
        .set_bootstrap_servers(KAFKA_BROKERS)
        .set_topics(INPUT_TOPIC)
        .set_group_id(CONSUMER_GROUP)
        .set_starting_offsets(KafkaOffsetsInitializer.committed_offsets())
        .set_value_only_deserializer(SimpleStringSchema())
        .build()
    )

    # --- Kafka Sink ---
    kafka_sink = (
        KafkaSink.builder()
        .set_bootstrap_servers(KAFKA_BROKERS)
        .set_record_serializer(
            KafkaRecordSerializationSchema.builder()
            .set_topic(OUTPUT_TOPIC)
            .set_value_serialization_schema(SimpleStringSchema())
            .build()
        )
        .build()
    )

    # --- Pipeline ---
    (
        env.from_source(
            kafka_source,
            WatermarkStrategy.no_watermarks(),
            "events.raw",
        )
        .map(EventEnrichmentFunction(GEOIP_DB_PATH))
        .name("enrich-events")
        .sink_to(kafka_sink)
        .name("events.enriched")
    )

    env.execute("event-enrichment")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    build_pipeline()
