"""Configuration and Channel Registry — single source of truth for all 20 fault channels."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ── Environment Configuration ──────────────────────────────────────────────
OTLP_ENDPOINT = os.getenv("OTLP_ENDPOINT", "http://otel-collector:4318")
OTLP_API_KEY = os.getenv("OTLP_API_KEY", "")
OTLP_AUTH_TYPE = os.getenv("OTLP_AUTH_TYPE", "ApiKey")  # "ApiKey" or "Bearer"

ELASTIC_URL = os.getenv("ELASTIC_URL", "")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY", "")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
TWILIO_TO_NUMBER = os.getenv("TWILIO_TO_NUMBER", "")

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "nova7-ops@mission-control.local")

APP_PORT = int(os.getenv("APP_PORT", "80"))
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")

# ── Mission Configuration ──────────────────────────────────────────────────
MISSION_ID = "NOVA-7"
MISSION_NAME = "NOVA-7 Orbital Insertion"
COUNTDOWN_START_SECONDS = 600  # T-10:00
COUNTDOWN_SPEED = 1.0  # 1.0 = real-time

# ── Service Definitions ────────────────────────────────────────────────────
SERVICES: dict[str, dict[str, Any]] = {
    "mission-control": {
        "cloud_provider": "aws",
        "cloud_region": "us-east-1",
        "cloud_platform": "aws_ec2",
        "cloud_availability_zone": "us-east-1a",
        "subsystem": "command",
        "language": "python",
    },
    "fuel-system": {
        "cloud_provider": "aws",
        "cloud_region": "us-east-1",
        "cloud_platform": "aws_ec2",
        "cloud_availability_zone": "us-east-1b",
        "subsystem": "propulsion",
        "language": "go",
    },
    "ground-systems": {
        "cloud_provider": "aws",
        "cloud_region": "us-east-1",
        "cloud_platform": "aws_ec2",
        "cloud_availability_zone": "us-east-1c",
        "subsystem": "ground",
        "language": "java",
    },
    "navigation": {
        "cloud_provider": "gcp",
        "cloud_region": "us-central1",
        "cloud_platform": "gcp_compute_engine",
        "cloud_availability_zone": "us-central1-a",
        "subsystem": "guidance",
        "language": "rust",
    },
    "comms-array": {
        "cloud_provider": "gcp",
        "cloud_region": "us-central1",
        "cloud_platform": "gcp_compute_engine",
        "cloud_availability_zone": "us-central1-b",
        "subsystem": "communications",
        "language": "cpp",
    },
    "payload-monitor": {
        "cloud_provider": "gcp",
        "cloud_region": "us-central1",
        "cloud_platform": "gcp_compute_engine",
        "cloud_availability_zone": "us-central1-a",
        "subsystem": "payload",
        "language": "python",
    },
    "sensor-validator": {
        "cloud_provider": "azure",
        "cloud_region": "eastus",
        "cloud_platform": "azure_vm",
        "cloud_availability_zone": "eastus-1",
        "subsystem": "validation",
        "language": "dotnet",
    },
    "telemetry-relay": {
        "cloud_provider": "azure",
        "cloud_region": "eastus",
        "cloud_platform": "azure_vm",
        "cloud_availability_zone": "eastus-2",
        "subsystem": "relay",
        "language": "go",
    },
    "range-safety": {
        "cloud_provider": "azure",
        "cloud_region": "eastus",
        "cloud_platform": "azure_vm",
        "cloud_availability_zone": "eastus-1",
        "subsystem": "safety",
        "language": "java",
    },
}

# ── Severity Number Mapping ────────────────────────────────────────────────
SEVERITY_MAP = {
    "TRACE": 1,
    "DEBUG": 5,
    "INFO": 9,
    "WARN": 13,
    "ERROR": 17,
    "FATAL": 21,
}

# ── Channel Registry ───────────────────────────────────────────────────────
# The master definition of all 20 fault channels.
# Each channel defines the exact error signature that both fault injection
# and ES|QL significant event queries derive from.

CHANNEL_REGISTRY: dict[int, dict[str, Any]] = {
    1: {
        "name": "Thermal Calibration Drift",
        "subsystem": "propulsion",
        "vehicle_section": "engine_bay",
        "error_type": "ThermalCalibrationException",
        "sensor_type": "thermal",
        "affected_services": ["fuel-system", "sensor-validator"],
        "cascade_services": ["mission-control", "range-safety"],
        "description": "Thermal sensor calibration drifts outside acceptable bounds in the engine bay",
        "error_message": "Thermal sensor calibration drift detected: deviation {deviation}K exceeds threshold of 2.5K at epoch {epoch}",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "propulsion/thermal_monitor.py", line 342, in validate_calibration\n'
            "    baseline = self._load_calibration_baseline(sensor_id, epoch)\n"
            '  File "propulsion/thermal_monitor.py", line 298, in _load_calibration_baseline\n'
            "    return self.calibration_store.get(sensor_id)\n"
            '  File "propulsion/calibration_store.py", line 156, in get\n'
            "    raise ThermalCalibrationException(f\"Calibration drift: {deviation}K > 2.5K threshold\")\n"
            "ThermalCalibrationException: Calibration drift: {deviation}K > 2.5K threshold"
        ),
    },
    2: {
        "name": "Fuel Pressure Anomaly",
        "subsystem": "propulsion",
        "vehicle_section": "fuel_tanks",
        "error_type": "FuelPressureException",
        "sensor_type": "pressure",
        "affected_services": ["fuel-system", "sensor-validator"],
        "cascade_services": ["mission-control", "range-safety"],
        "description": "Fuel tank pressure readings outside nominal range",
        "error_message": "Fuel pressure anomaly: tank {tank_id} reading {pressure} PSI, expected {expected_min}-{expected_max} PSI",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "propulsion/fuel_controller.py", line 218, in monitor_pressure\n'
            "    self._validate_pressure_bounds(tank_id, reading)\n"
            '  File "propulsion/fuel_controller.py", line 195, in _validate_pressure_bounds\n'
            "    raise FuelPressureException(f\"Pressure {reading} PSI out of bounds\")\n"
            "FuelPressureException: Pressure {pressure} PSI out of bounds for tank {tank_id}"
        ),
    },
    3: {
        "name": "Oxidizer Flow Rate Deviation",
        "subsystem": "propulsion",
        "vehicle_section": "engine_bay",
        "error_type": "OxidizerFlowException",
        "sensor_type": "flow_rate",
        "affected_services": ["fuel-system", "sensor-validator"],
        "cascade_services": ["mission-control"],
        "description": "Oxidizer flow rate deviates from commanded value",
        "error_message": "Oxidizer flow rate deviation: measured {measured} kg/s vs commanded {commanded} kg/s (delta {delta}%)",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "propulsion/oxidizer_controller.py", line 167, in check_flow_rate\n'
            "    delta = abs(measured - commanded) / commanded * 100\n"
            '  File "propulsion/oxidizer_controller.py", line 173, in check_flow_rate\n'
            "    raise OxidizerFlowException(f\"Flow deviation {delta:.1f}% exceeds 3% tolerance\")\n"
            "OxidizerFlowException: Flow deviation {delta}% exceeds 3% tolerance"
        ),
    },
    4: {
        "name": "GPS Multipath Interference",
        "subsystem": "guidance",
        "vehicle_section": "avionics",
        "error_type": "GPSMultipathException",
        "sensor_type": "gps",
        "affected_services": ["navigation", "sensor-validator"],
        "cascade_services": ["mission-control", "range-safety"],
        "description": "GPS receiver detecting multipath signal interference",
        "error_message": "GPS multipath interference detected: {num_satellites} satellites affected, position uncertainty {uncertainty}m",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "guidance/gps_receiver.py", line 445, in process_fix\n'
            "    solution = self._compute_position(observations)\n"
            '  File "guidance/gps_receiver.py", line 412, in _compute_position\n'
            "    raise GPSMultipathException(f\"Multipath on {num_affected} SVs\")\n"
            "GPSMultipathException: Multipath on {num_satellites} SVs, uncertainty {uncertainty}m"
        ),
    },
    5: {
        "name": "IMU Synchronization Loss",
        "subsystem": "guidance",
        "vehicle_section": "avionics",
        "error_type": "IMUSyncException",
        "sensor_type": "imu",
        "affected_services": ["navigation", "sensor-validator"],
        "cascade_services": ["mission-control", "range-safety"],
        "description": "Inertial measurement unit loses time synchronization",
        "error_message": "IMU sync loss: drift {drift_ms}ms exceeds {threshold_ms}ms threshold on axis {axis}",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "guidance/imu_controller.py", line 289, in sync_check\n'
            "    drift = self._measure_clock_drift(imu_id)\n"
            '  File "guidance/imu_controller.py", line 267, in _measure_clock_drift\n'
            "    raise IMUSyncException(f\"Clock drift {drift}ms on {axis}-axis\")\n"
            "IMUSyncException: Clock drift {drift_ms}ms on {axis}-axis exceeds threshold"
        ),
    },
    6: {
        "name": "Star Tracker Alignment Fault",
        "subsystem": "guidance",
        "vehicle_section": "avionics",
        "error_type": "StarTrackerAlignmentException",
        "sensor_type": "star_tracker",
        "affected_services": ["navigation", "sensor-validator"],
        "cascade_services": ["mission-control"],
        "description": "Star tracker optical alignment exceeds tolerance",
        "error_message": "Star tracker alignment fault: boresight error {error_arcsec} arcsec, limit {limit_arcsec} arcsec",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "guidance/star_tracker.py", line 178, in validate_alignment\n'
            "    error = self._compute_boresight_error(catalog_stars, observed_stars)\n"
            '  File "guidance/star_tracker.py", line 156, in _compute_boresight_error\n'
            "    raise StarTrackerAlignmentException(f\"Boresight error {error} arcsec\")\n"
            "StarTrackerAlignmentException: Boresight error {error_arcsec} arcsec exceeds {limit_arcsec} limit"
        ),
    },
    7: {
        "name": "S-Band Signal Degradation",
        "subsystem": "communications",
        "vehicle_section": "antenna_array",
        "error_type": "SignalDegradationException",
        "sensor_type": "rf_signal",
        "affected_services": ["comms-array", "sensor-validator"],
        "cascade_services": ["mission-control", "telemetry-relay"],
        "description": "S-band communication signal strength below minimum threshold",
        "error_message": "S-band signal degradation: SNR {snr_db}dB below minimum {min_snr_db}dB on channel {rf_channel}",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "comms/sband_controller.py", line 234, in monitor_signal\n'
            "    snr = self._measure_snr(channel)\n"
            '  File "comms/sband_controller.py", line 211, in _measure_snr\n'
            "    raise SignalDegradationException(f\"SNR {snr}dB < {min_snr}dB minimum\")\n"
            "SignalDegradationException: SNR {snr_db}dB below minimum on channel {rf_channel}"
        ),
    },
    8: {
        "name": "X-Band Packet Loss",
        "subsystem": "communications",
        "vehicle_section": "antenna_array",
        "error_type": "PacketLossException",
        "sensor_type": "packet_integrity",
        "affected_services": ["comms-array", "sensor-validator"],
        "cascade_services": ["telemetry-relay", "mission-control"],
        "description": "X-band data link experiencing excessive packet loss",
        "error_message": "X-band packet loss: {loss_pct}% loss rate exceeds {threshold_pct}% threshold on link {link_id}",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "comms/xband_link.py", line 312, in check_integrity\n'
            "    loss_rate = self._compute_loss_rate(window_samples)\n"
            '  File "comms/xband_link.py", line 289, in _compute_loss_rate\n'
            "    raise PacketLossException(f\"Loss rate {loss_rate}% on link {link_id}\")\n"
            "PacketLossException: Packet loss {loss_pct}% exceeds threshold on link {link_id}"
        ),
    },
    9: {
        "name": "UHF Antenna Pointing Error",
        "subsystem": "communications",
        "vehicle_section": "antenna_array",
        "error_type": "AntennaPointingException",
        "sensor_type": "antenna_position",
        "affected_services": ["comms-array", "sensor-validator"],
        "cascade_services": ["mission-control"],
        "description": "UHF antenna gimbal pointing error exceeds tolerance",
        "error_message": "UHF antenna pointing error: azimuth deviation {az_error}deg, elevation deviation {el_error}deg",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "comms/uhf_antenna.py", line 198, in track_target\n'
            "    error = self._compute_pointing_error(commanded, actual)\n"
            '  File "comms/uhf_antenna.py", line 175, in _compute_pointing_error\n'
            "    raise AntennaPointingException(f\"Pointing error az={az}deg el={el}deg\")\n"
            "AntennaPointingException: Pointing error azimuth {az_error}deg elevation {el_error}deg"
        ),
    },
    10: {
        "name": "Payload Thermal Excursion",
        "subsystem": "payload",
        "vehicle_section": "payload_bay",
        "error_type": "PayloadThermalException",
        "sensor_type": "thermal",
        "affected_services": ["payload-monitor", "sensor-validator"],
        "cascade_services": ["mission-control"],
        "description": "Payload bay temperature outside safe operating range",
        "error_message": "Payload thermal excursion: zone {zone} temperature {temp}C, safe range {safe_min}C-{safe_max}C",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "payload/thermal_controller.py", line 267, in monitor_zones\n'
            "    self._validate_zone_temp(zone, reading)\n"
            '  File "payload/thermal_controller.py", line 245, in _validate_zone_temp\n'
            "    raise PayloadThermalException(f\"Zone {zone} temp {temp}C out of range\")\n"
            "PayloadThermalException: Zone {zone} temperature {temp}C outside safe range"
        ),
    },
    11: {
        "name": "Payload Vibration Anomaly",
        "subsystem": "payload",
        "vehicle_section": "payload_bay",
        "error_type": "PayloadVibrationException",
        "sensor_type": "vibration",
        "affected_services": ["payload-monitor", "sensor-validator"],
        "cascade_services": ["mission-control", "range-safety"],
        "description": "Payload vibration levels exceed structural safety margins",
        "error_message": "Payload vibration anomaly: {axis}-axis {amplitude}g at {frequency}Hz exceeds {limit}g limit",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "payload/vibration_monitor.py", line 189, in analyze_spectrum\n'
            "    peak = self._find_peak_amplitude(fft_data, axis)\n"
            '  File "payload/vibration_monitor.py", line 167, in _find_peak_amplitude\n'
            "    raise PayloadVibrationException(f\"{axis}-axis {amplitude}g @ {freq}Hz\")\n"
            "PayloadVibrationException: {axis}-axis vibration {amplitude}g at {frequency}Hz exceeds limit"
        ),
    },
    12: {
        "name": "Cross-Cloud Relay Latency",
        "subsystem": "relay",
        "vehicle_section": "ground_network",
        "error_type": "RelayLatencyException",
        "sensor_type": "network_latency",
        "affected_services": ["telemetry-relay", "sensor-validator"],
        "cascade_services": ["mission-control", "comms-array"],
        "description": "Cross-cloud telemetry relay latency exceeds acceptable bounds",
        "error_message": "Relay latency spike: {source_cloud}->{dest_cloud} latency {latency_ms}ms exceeds {threshold_ms}ms threshold",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "relay/cross_cloud_router.py", line 334, in route_telemetry\n'
            "    latency = self._measure_route_latency(source, dest)\n"
            '  File "relay/cross_cloud_router.py", line 312, in _measure_route_latency\n'
            "    raise RelayLatencyException(f\"Latency {latency}ms on {source}->{dest}\")\n"
            "RelayLatencyException: Relay latency {latency_ms}ms exceeds threshold on {source_cloud}->{dest_cloud}"
        ),
    },
    13: {
        "name": "Relay Packet Corruption",
        "subsystem": "relay",
        "vehicle_section": "ground_network",
        "error_type": "PacketCorruptionException",
        "sensor_type": "data_integrity",
        "affected_services": ["telemetry-relay", "sensor-validator"],
        "cascade_services": ["mission-control"],
        "description": "Telemetry packets failing integrity checks during relay",
        "error_message": "Packet corruption detected: {corrupted_count}/{total_count} packets failed CRC on route {route_id}",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "relay/integrity_checker.py", line 223, in validate_batch\n'
            "    crc_result = self._check_crc32(packet)\n"
            '  File "relay/integrity_checker.py", line 201, in _check_crc32\n'
            "    raise PacketCorruptionException(f\"CRC mismatch on route {route_id}\")\n"
            "PacketCorruptionException: {corrupted_count} of {total_count} packets corrupted on route {route_id}"
        ),
    },
    14: {
        "name": "Ground Power Bus Fault",
        "subsystem": "ground",
        "vehicle_section": "launch_pad",
        "error_type": "PowerBusFaultException",
        "sensor_type": "electrical",
        "affected_services": ["ground-systems", "sensor-validator"],
        "cascade_services": ["mission-control", "fuel-system"],
        "description": "Launch pad power bus voltage irregularity detected",
        "error_message": "Power bus fault: bus {bus_id} voltage {voltage}V, nominal {nominal_v}V (deviation {deviation_pct}%)",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "ground/power_monitor.py", line 278, in check_bus_voltage\n'
            "    deviation = abs(voltage - nominal) / nominal * 100\n"
            '  File "ground/power_monitor.py", line 284, in check_bus_voltage\n'
            "    raise PowerBusFaultException(f\"Bus {bus_id} deviation {deviation:.1f}%\")\n"
            "PowerBusFaultException: Bus {bus_id} voltage {voltage}V deviates {deviation_pct}% from nominal"
        ),
    },
    15: {
        "name": "Weather Station Data Gap",
        "subsystem": "ground",
        "vehicle_section": "launch_pad",
        "error_type": "WeatherDataGapException",
        "sensor_type": "weather",
        "affected_services": ["ground-systems", "sensor-validator"],
        "cascade_services": ["mission-control", "range-safety"],
        "description": "Weather monitoring station reporting data gaps",
        "error_message": "Weather data gap: station {station_id} no data for {gap_seconds}s, max allowed {max_gap}s",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "ground/weather_monitor.py", line 198, in poll_station\n'
            "    data = self._fetch_station_data(station_id)\n"
            '  File "ground/weather_monitor.py", line 176, in _fetch_station_data\n'
            "    raise WeatherDataGapException(f\"No data from {station_id} for {gap}s\")\n"
            "WeatherDataGapException: Station {station_id} data gap {gap_seconds}s exceeds {max_gap}s limit"
        ),
    },
    16: {
        "name": "Pad Hydraulic Pressure Loss",
        "subsystem": "ground",
        "vehicle_section": "launch_pad",
        "error_type": "HydraulicPressureException",
        "sensor_type": "hydraulic",
        "affected_services": ["ground-systems", "sensor-validator"],
        "cascade_services": ["mission-control"],
        "description": "Launch pad hydraulic system pressure dropping below minimum",
        "error_message": "Hydraulic pressure loss: system {system_id} pressure {pressure} PSI, minimum {min_pressure} PSI",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "ground/hydraulic_controller.py", line 234, in monitor_pressure\n'
            "    self._check_minimum_pressure(system_id, reading)\n"
            '  File "ground/hydraulic_controller.py", line 212, in _check_minimum_pressure\n'
            "    raise HydraulicPressureException(f\"System {system_id} at {pressure} PSI\")\n"
            "HydraulicPressureException: System {system_id} pressure {pressure} PSI below minimum {min_pressure} PSI"
        ),
    },
    17: {
        "name": "Sensor Validation Pipeline Stall",
        "subsystem": "validation",
        "vehicle_section": "ground_network",
        "error_type": "ValidationPipelineException",
        "sensor_type": "pipeline_health",
        "affected_services": ["sensor-validator"],
        "cascade_services": ["mission-control", "telemetry-relay"],
        "description": "Sensor validation pipeline stalled, readings not being validated",
        "error_message": "Validation pipeline stall: queue depth {queue_depth}, processing rate {rate}/s dropped below {min_rate}/s",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "validation/pipeline_manager.py", line 312, in check_health\n'
            "    rate = self._compute_processing_rate(window)\n"
            '  File "validation/pipeline_manager.py", line 289, in _compute_processing_rate\n'
            "    raise ValidationPipelineException(f\"Rate {rate}/s below minimum {min_rate}/s\")\n"
            "ValidationPipelineException: Pipeline stall, rate {rate}/s below {min_rate}/s, queue depth {queue_depth}"
        ),
    },
    18: {
        "name": "Calibration Epoch Mismatch",
        "subsystem": "validation",
        "vehicle_section": "ground_network",
        "error_type": "CalibrationEpochException",
        "sensor_type": "calibration",
        "affected_services": ["sensor-validator"],
        "cascade_services": ["mission-control", "fuel-system", "navigation"],
        "description": "Sensor calibration epoch does not match expected reference",
        "error_message": "Calibration epoch mismatch: sensor {sensor_id} epoch {actual_epoch} vs expected {expected_epoch}",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "validation/calibration_checker.py", line 178, in verify_epoch\n'
            "    expected = self._get_reference_epoch(sensor_type)\n"
            '  File "validation/calibration_checker.py", line 156, in _get_reference_epoch\n'
            "    raise CalibrationEpochException(f\"Epoch mismatch for {sensor_id}\")\n"
            "CalibrationEpochException: Sensor {sensor_id} epoch {actual_epoch} != expected {expected_epoch}"
        ),
    },
    19: {
        "name": "Flight Termination System Check Failure",
        "subsystem": "safety",
        "vehicle_section": "vehicle_wide",
        "error_type": "FTSCheckException",
        "sensor_type": "safety_system",
        "affected_services": ["range-safety", "sensor-validator"],
        "cascade_services": ["mission-control"],
        "description": "Flight termination system self-check returning anomalous results",
        "error_message": "FTS check failure: unit {unit_id} self-test returned code {error_code}, expected 0x00",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "safety/fts_controller.py", line 267, in self_test\n'
            "    result = self._execute_test_sequence(unit_id)\n"
            '  File "safety/fts_controller.py", line 245, in _execute_test_sequence\n'
            "    raise FTSCheckException(f\"Unit {unit_id} returned {error_code}\")\n"
            "FTSCheckException: FTS unit {unit_id} self-test failed with code {error_code}"
        ),
    },
    20: {
        "name": "Range Safety Tracking Loss",
        "subsystem": "safety",
        "vehicle_section": "vehicle_wide",
        "error_type": "TrackingLossException",
        "sensor_type": "radar_tracking",
        "affected_services": ["range-safety", "sensor-validator"],
        "cascade_services": ["mission-control", "navigation"],
        "description": "Range safety radar losing vehicle track",
        "error_message": "Tracking loss: radar {radar_id} lost track for {gap_ms}ms, max allowed {max_gap_ms}ms",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            '  File "safety/tracking_system.py", line 334, in update_track\n'
            "    state = self._correlate_returns(radar_id)\n"
            '  File "safety/tracking_system.py", line 312, in _correlate_returns\n'
            "    raise TrackingLossException(f\"Radar {radar_id} lost track {gap}ms\")\n"
            "TrackingLossException: Radar {radar_id} track gap {gap_ms}ms exceeds {max_gap_ms}ms limit"
        ),
    },
}
