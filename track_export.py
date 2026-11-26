#!/usr/bin/env python3
"""Экспорт траекторий самолётов FR24 в CSV, JSON и GeoJSON.

Модуль только читает данные декодера (model.py, feed_decoder.py не менялись).
Порядок самолётов - по первому появлению в дампе, порядок точек - по времени,
как его отдаёт feed_decoder.merge_tracks().

Запуск:
    python track_export.py [fr24_analysis.json] [каталог_экспорта]
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from analysis_context import AnalysisContext
from dump_loader import load_dump
from feed_decoder import decode_feed_response, merge_tracks
from model import Feed, TrackPoint


MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_DUMP = MODULE_DIR / "fr24_analysis.json"
DEFAULT_EXPORT_DIR = MODULE_DIR / "exports"

CSV_NAME = "tracks.csv"
JSON_NAME = "tracks.json"
GEOJSON_NAME = "tracks.geojson"

CSV_COLUMNS = (
    "aircraft_id",
    "callsign",
    "latitude",
    "longitude",
    "timestamp_ms",
    "timestamp_utc",
    "source_frame",
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Время
# --------------------------------------------------------------------------

def format_utc_ms(timestamp_ms: int | None) -> str | None:
    """Unix-время в мс -> 'YYYY-MM-DDTHH:MM:SS.mmmZ' (UTC, без потери мс).

    Используется целочисленная арифметика (timedelta), float не участвует.
    """
    if timestamp_ms is None:
        return None

    moment = _EPOCH + timedelta(milliseconds=int(timestamp_ms))
    return (
        f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"
    )


def parse_utc_ms(text: str) -> int:
    """Обратное преобразование для format_utc_ms (используется в тестах)."""
    moment = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )
    delta = moment - _EPOCH
    return (
        (delta.days * 86400 + delta.seconds) * 1000
        + delta.microseconds // 1000
    )


# --------------------------------------------------------------------------
# Сбор данных
# --------------------------------------------------------------------------

@dataclass
class AircraftTrack:
    aircraft_id: int
    callsign: str
    points: list[TrackPoint]
    feed_count: int = 0
    raw_point_count: int = 0
    current: TrackPoint | None = None
    first_seen_frame: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def point_count(self) -> int:
        return len(self.points)

    @property
    def start_ms(self) -> int | None:
        return self.points[0].timestamp if self.points else None

    @property
    def end_ms(self) -> int | None:
        return self.points[-1].timestamp if self.points else None


def decode_dump_feeds(dump_path: str | Path) -> list[Feed]:
    """Декодирует все protobuf-кадры дампа в список Feed (в порядке кадров)."""
    feeds: list[Feed] = []

    for frame in load_dump(dump_path):
        response = decode_feed_response(
            frame.raw,
            frame.source,
            AnalysisContext(),
        )
        feeds.extend(response.feeds)

    return feeds


def build_aircraft_tracks(feeds: list[Feed]) -> list[AircraftTrack]:
    """Группирует Feed по самолётам.

    Точки берутся из feed_decoder.merge_tracks() без изменений.
    Позывной - первый непустой по порядку появления.
    Текущая позиция - Feed с наибольшим field_15.
    """
    merged = merge_tracks(feeds)

    seen: set[tuple[int | None, int | None]] = set()
    meta: dict[int, dict[str, Any]] = {}

    for feed in feeds:
        if feed.field_1 is None:
            continue

        key = (feed.field_1, feed.field_15)
        if key in seen:
            continue
        seen.add(key)

        info = meta.setdefault(
            feed.field_1,
            {
                "callsign": "",
                "feed_count": 0,
                "raw_point_count": 0,
                "latest_feed": None,
            },
        )

        info["feed_count"] += 1
        info["raw_point_count"] += len(feed.track)

        if not info["callsign"] and feed.field_11:
            info["callsign"] = feed.field_11

        latest = info["latest_feed"]
        if latest is None or (feed.field_15 or 0) >= (latest.field_15 or 0):
            info["latest_feed"] = feed

    result: list[AircraftTrack] = []

    for aircraft_id, points in merged.items():
        info = meta[aircraft_id]
        latest: Feed = info["latest_feed"]

        current = None
        if latest.field_2 is not None and latest.field_3 is not None:
            frame = (
                latest.source.http_record_index if latest.source else -1
            )
            current = TrackPoint(
                latitude=latest.field_2,
                longitude=latest.field_3,
                timestamp=latest.field_15,
                source_frame=frame,
                raw_fields={"kind": "current"},
            )

        result.append(
            AircraftTrack(
                aircraft_id=aircraft_id,
                callsign=info["callsign"],
                points=points,
                feed_count=info["feed_count"],
                raw_point_count=info["raw_point_count"],
                current=current,
            )
        )

    return result


def load_aircraft_tracks(
    dump_path: str | Path = DEFAULT_DUMP,
) -> list[AircraftTrack]:
    return build_aircraft_tracks(decode_dump_feeds(dump_path))


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------

def _callsign_or_none(callsign: str) -> str | None:
    return callsign or None


def _point_dict(point: TrackPoint) -> dict[str, Any]:
    return {
        "latitude": point.latitude,
        "longitude": point.longitude,
        "timestamp_ms": point.timestamp,
        "timestamp_utc": format_utc_ms(point.timestamp),
        "source_frame": point.source_frame,
        "raw_fields": dict(point.raw_fields),
    }


def _prepare_dir(directory: str | Path) -> Path:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

def export_csv(
    aircraft: list[AircraftTrack],
    directory: str | Path = DEFAULT_EXPORT_DIR,
    filename: str = CSV_NAME,
) -> Path:
    """CSV в UTF-8 с BOM (для Excel). Координаты пишутся через repr(float)
    без округления. Одна строка на точку, порядок сохранён."""
    path = _prepare_dir(directory) / filename

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(CSV_COLUMNS)

        for track in aircraft:
            for point in track.points:
                writer.writerow(
                    (
                        track.aircraft_id,
                        track.callsign,
                        repr(point.latitude),
                        repr(point.longitude),
                        point.timestamp,
                        format_utc_ms(point.timestamp) or "",
                        point.source_frame,
                    )
                )

    return path


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------

def build_json_document(
    aircraft: list[AircraftTrack],
    source_file: str | None = None,
) -> dict[str, Any]:
    items = []

    for track in aircraft:
        items.append(
            {
                "aircraft_id": track.aircraft_id,
                "callsign": _callsign_or_none(track.callsign),
                "current_position": (
                    _point_dict(track.current) if track.current else None
                ),
                "feed_count": track.feed_count,
                "raw_point_count": track.raw_point_count,
                "point_count": track.point_count,
                "start_time_ms": track.start_ms,
                "start_time_utc": format_utc_ms(track.start_ms),
                "end_time_ms": track.end_ms,
                "end_time_utc": format_utc_ms(track.end_ms),
                "track": [_point_dict(p) for p in track.points],
            }
        )

    return {
        "format": "fr24-decoder-tracks",
        "version": 1,
        "source_file": source_file,
        "time_zone": "UTC",
        "coordinate_system": "WGS84",
        "aircraft_count": len(items),
        "aircraft": items,
    }


def export_json(
    aircraft: list[AircraftTrack],
    directory: str | Path = DEFAULT_EXPORT_DIR,
    filename: str = JSON_NAME,
    source_file: str | None = None,
) -> Path:
    path = _prepare_dir(directory) / filename
    document = build_json_document(aircraft, source_file)

    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(document, file, ensure_ascii=False, indent=2)
        file.write("\n")

    return path


# --------------------------------------------------------------------------
# GeoJSON
# --------------------------------------------------------------------------

def build_geojson_document(
    aircraft: list[AircraftTrack],
) -> dict[str, Any]:
    """FeatureCollection, по Feature на самолёт.

    Геометрия - LineString, координаты [longitude, latitude] (RFC 7946).
    LineString требует минимум 2 позиции: для самолёта с единственной
    точкой выдаётся Point, чтобы не создавать невалидный GeoJSON.
    """
    features = []

    for track in aircraft:
        coordinates = [[p.longitude, p.latitude] for p in track.points]

        if len(coordinates) >= 2:
            geometry: dict[str, Any] = {
                "type": "LineString",
                "coordinates": coordinates,
            }
        elif len(coordinates) == 1:
            geometry = {"type": "Point", "coordinates": coordinates[0]}
        else:
            geometry = None

        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "aircraft_id": track.aircraft_id,
                    "callsign": _callsign_or_none(track.callsign),
                    "point_count": track.point_count,
                    "start_time_ms": track.start_ms,
                    "start_time_utc": format_utc_ms(track.start_ms),
                    "end_time_ms": track.end_ms,
                    "end_time_utc": format_utc_ms(track.end_ms),
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


def export_geojson(
    aircraft: list[AircraftTrack],
    directory: str | Path = DEFAULT_EXPORT_DIR,
    filename: str = GEOJSON_NAME,
) -> Path:
    path = _prepare_dir(directory) / filename
    document = build_geojson_document(aircraft)

    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(document, file, ensure_ascii=False, indent=2)
        file.write("\n")

    return path


# --------------------------------------------------------------------------
# Всё сразу
# --------------------------------------------------------------------------

def export_all(
    dump_path: str | Path = DEFAULT_DUMP,
    directory: str | Path = DEFAULT_EXPORT_DIR,
) -> dict[str, Path]:
    aircraft = load_aircraft_tracks(dump_path)

    return {
        "csv": export_csv(aircraft, directory),
        "json": export_json(
            aircraft, directory, source_file=Path(dump_path).name
        ),
        "geojson": export_geojson(aircraft, directory),
    }


def main(argv: list[str]) -> int:
    dump_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_DUMP
    directory = Path(argv[2]) if len(argv) > 2 else DEFAULT_EXPORT_DIR

    paths = export_all(dump_path, directory)
    aircraft = load_aircraft_tracks(dump_path)

    print("Самолётов:", len(aircraft))
    print("Точек:", sum(a.point_count for a in aircraft))

    for kind, path in paths.items():
        print(f"{kind}: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
