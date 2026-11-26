
from __future__ import annotations

import json
import math
import struct
import time
import requests

from pathlib import Path
from datetime import datetime, timezone

from grpc_web import decode_grpc_web_body
from feed_decoder import decode_feed_response
from analysis_context import AnalysisContext
from model import SourceRef
from track_export import (
    build_aircraft_tracks,
    export_csv,
    export_json,
    export_geojson,
)


BASE_DIR = Path(__file__).resolve().parent
HAR_FILE = BASE_DIR / "www.flightradar24.com.har"

CAPTURE_DIR = BASE_DIR / "captures"
EXPORT_DIR = BASE_DIR / "live_exports"

URL = (
    "https://data-feed.flightradar24.com/"
    "fr24.feed.api.v1.Feed/LiveFeed"
)

INTERVAL_SECONDS = 10
TIMEOUT_SECONDS = 20
SAVE_EVERY_REQUESTS = 1


def build_request_body(template: bytes) -> bytes:
    try:
        with (BASE_DIR / "radar_config.json").open("r", encoding="utf-8-sig") as file:
            config = json.load(file)
        lat = float(config["latitude"])
        lon = float(config["longitude"])
        radius_km = float(config["radius_km"])
        if not all(math.isfinite(v) for v in (lat, lon, radius_km)):
            raise ValueError("Radar settings must be finite numbers")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Coordinates are out of range")
        if not (0 < radius_km <= 20000):
            raise ValueError("Radius must be between 0 and 20000 km")
        angular = radius_km / 6371.0088
        lat_delta = math.degrees(angular)
        north = min(90.0, lat + lat_delta)
        south = max(-90.0, lat - lat_delta)
        if abs(lat) + lat_delta >= 90:
            lon_delta = 180.0
        else:
            lon_delta = math.degrees(math.asin(math.sin(angular) / math.cos(math.radians(lat))))
        west = lon - lon_delta
        east = lon + lon_delta
        if west < -180 or east > 180:
            raise ValueError("Radar area crosses the date line")
        result = bytearray(template)
        if (len(result) != 70 or result[:7] != bytes((0, 0, 0, 0, 65, 10, 20))
                or [result[i] for i in (7, 12, 17, 22)] != [13, 21, 29, 37]):
            raise ValueError("Unexpected HAR protobuf template")
        for offset, value in zip((8, 13, 18, 23), (north, south, west, east)):
            struct.pack_into("<f", result, offset, value)
        return bytes(result)
    except Exception as exc:
        print("Radar config error: " + str(exc) + ". Using original HAR template.")
        return template


def load_request_template():
    if not HAR_FILE.exists():
        raise FileNotFoundError(f"HAR не найден: {HAR_FILE}")

    with HAR_FILE.open("r", encoding="utf-8-sig") as file:
        har = json.load(file)

    entry = next(
        (
            item
            for item in har["log"]["entries"]
            if item.get("request", {}).get("url") == URL
            and item.get("request", {}).get("method") == "POST"
            and item.get("request", {}).get("postData", {}).get("text")
        ),
        None,
    )

    if entry is None:
        raise RuntimeError("POST LiveFeed не найден в HAR")

    request = entry["request"]
    body = request["postData"]["text"].encode("latin1")

    allowed_headers = {
        "content-type",
        "x-grpc-web",
        "x-user-agent",
        "fr24-device-id",
        "fr24-platform",
        "origin",
        "referer",
        "user-agent",
        "accept",
    }

    headers = {
        header["name"]: header["value"]
        for header in request.get("headers", [])
        if header["name"].lower() in allowed_headers
    }

    return body, headers


def decode_response(data: bytes, request_index: int):
    frames = decode_grpc_web_body(data)

    feeds = []
    errors = []

    for frame_index, frame in enumerate(frames):
        if not frame.is_data:
            continue

        if frame.is_compressed:
            errors.append(
                f"Кадр {frame_index}: сжатый payload пропущен"
            )
            continue

        source = SourceRef(
            http_record_index=request_index,
            frame_index=frame_index,
            protobuf_message_index=0,
        )

        try:
            result = decode_feed_response(
                frame.payload,
                source,
                AnalysisContext(),
            )
            feeds.extend(result.feeds)

        except Exception as exc:
            errors.append(f"Кадр {frame_index}: {exc}")

    return feeds, errors


def restore_history():
    feeds = []

    if not CAPTURE_DIR.exists():
        return feeds

    files = sorted(CAPTURE_DIR.glob("*.bin"))

    print(f"Восстановление истории: файлов {len(files)}")

    for index, file_path in enumerate(files):
        try:
            data = file_path.read_bytes()
            decoded, errors = decode_response(data, index)
            feeds.extend(decoded)

            if errors:
                print(
                    f"Предупреждение {file_path.name}: "
                    f"{len(errors)} ошибок декодирования"
                )

        except Exception as exc:
            print(f"Ошибка чтения {file_path.name}: {exc}")

    return feeds


def save_exports(feeds):
    tracks = build_aircraft_tracks(feeds)

    csv_file = export_csv(tracks, EXPORT_DIR)
    json_file = export_json(
        tracks,
        EXPORT_DIR,
        source_file="FR24 LiveFeed",
    )
    geojson_file = export_geojson(tracks, EXPORT_DIR)

    point_count = sum(track.point_count for track in tracks)

    print(
        f"Самолётов: {len(tracks)} | "
        f"Точек истории: {point_count}"
    )

    print(f"CSV:     {csv_file}")
    print(f"JSON:    {json_file}")
    print(f"GeoJSON: {geojson_file}")


def main():
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    body, headers = load_request_template()

    print("FR24 LiveFeed Collector")
    print("=======================")
    print(f"Размер POST: {len(body)} байт")
    print("Cookie: не передаётся")
    print("Authorization: не передаётся")
    print(f"Интервал: {INTERVAL_SECONDS} сек.")
    print()

    all_feeds = restore_history()

    if all_feeds:
        print(f"Восстановлено Feed: {len(all_feeds)}")
        save_exports(all_feeds)

    session = requests.Session()
    request_index = len(list(CAPTURE_DIR.glob("*.bin")))
    successful_requests = 0

    try:
        while True:
            started = time.monotonic()
            request_index += 1

            timestamp = datetime.now(timezone.utc).strftime(
                "%Y%m%d_%H%M%S_%f"
            )

            print(
                f"\n[{datetime.now().astimezone():%H:%M:%S}] "
                f"Запрос #{request_index}"
            )

            try:
                response = session.post(
                    URL,
                    data=build_request_body(body),
                    headers=headers,
                    timeout=TIMEOUT_SECONDS,
                )

                print("HTTP:", response.status_code)
                print("Размер ответа:", len(response.content))

                if response.status_code in (401, 403):
                    print(
                        "Доступ запрещён. "
                        "Сбор остановлен."
                    )
                    break

                response.raise_for_status()

                if not response.content:
                    print("Пустой ответ — пропуск")
                else:
                    capture_file = (
                        CAPTURE_DIR / f"live_{timestamp}.bin"
                    )
                    capture_file.write_bytes(response.content)

                    new_feeds, errors = decode_response(
                        response.content,
                        request_index,
                    )

                    all_feeds.extend(new_feeds)
                    successful_requests += 1

                    print("Новых Feed:", len(new_feeds))
                    print("Всего Feed:", len(all_feeds))

                    if errors:
                        for error in errors[:5]:
                            print("Ошибка декодирования:", error)

                    if (
                        successful_requests
                        % SAVE_EVERY_REQUESTS == 0
                    ):
                        save_exports(all_feeds)

            except requests.RequestException as exc:
                print(
                    "Ошибка HTTP:",
                    type(exc).__name__,
                    str(exc),
                )

            elapsed = time.monotonic() - started
            delay = max(0, INTERVAL_SECONDS - elapsed)

            print(f"Следующий запрос через {delay:.1f} сек.")
            time.sleep(delay)

    except KeyboardInterrupt:
        print("\nОстановка по Ctrl+C")

    finally:
        session.close()

        if all_feeds:
            print("\nФинальное сохранение...")
            save_exports(all_feeds)

        print("Сборщик остановлен.")


if __name__ == "__main__":
    main()
