#!/usr/bin/env python3
"""Загрузка protobuf-кадров из сохранённого FR24 JSON."""

from __future__ import annotations

import json
from pathlib import Path

from model import DumpFrame, SourceRef


def iter_dump_frames(path: str | Path):
    path = Path(path)

    with path.open("r", encoding="utf-8-sig") as file:
        records = json.load(file)

    if not isinstance(records, list):
        raise ValueError("Ожидался JSON-массив HTTP-записей")

    for record_index, record in enumerate(records):
        if not isinstance(record, dict):
            continue

        http_index = record.get("index", record_index)

        for frame_index, frame in enumerate(record.get("frames", [])):
            if not isinstance(frame, dict):
                continue

            if frame.get("kind") != "protobuf":
                continue

            raw_hex = frame.get("raw")

            if not isinstance(raw_hex, str):
                continue

            try:
                raw = bytes.fromhex(raw_hex)
            except ValueError as exc:
                raise ValueError(
                    f"Некорректный HEX: HTTP {http_index}, "
                    f"кадр {frame_index}: {exc}"
                ) from exc

            source = SourceRef(
                http_record_index=int(http_index),
                frame_index=frame_index,
                protobuf_message_index=0,
            )

            yield DumpFrame(
                source=source,
                kind="protobuf",
                raw=raw,
                content_type=record.get("content_type"),
                status=record.get("status"),
            )


def load_dump(path: str | Path) -> list[DumpFrame]:
    return list(iter_dump_frames(path))