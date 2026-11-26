from __future__ import annotations

import json
import math
import sys
import urllib.request
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QPolygonF, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QDoubleSpinBox, QSpinBox, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QSplitter, QGroupBox,
    QAbstractItemView,
)

API_URL = "http://127.0.0.1:8765/aircraft"
POLL_MS = 5000
RANGES_KM = [6, 10, 20, 50, 100, 200, 400, 600, 1000]
AIRPORTS = {
    "Баку — GYD": (40.4675, 50.0467),
    "Москва — SVO": (55.9726, 37.4146),
    "Москва — DME": (55.4088, 37.9063),
    "Москва — VKO": (55.5915, 37.2615),
    "Новосибирск — OVB": (55.0126, 82.6507),
    "Стамбул — IST": (41.2753, 28.7519),
    "Стамбул — SAW": (40.8986, 29.3092),
}


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


class FeedWorker(QThread):
    received = Signal(dict)
    failed = Signal(str)

    def run(self):
        try:
            req = urllib.request.Request(API_URL, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.received.emit(payload)
        except Exception as exc:
            self.failed.emit(str(exc))


class RadarWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 420)
        self.center_lat, self.center_lon = 40.4675, 50.0467
        self.radius_km = 50
        self.aircraft = []
        self.selected_id = None

    def set_data(self, aircraft, lat, lon, radius, selected_id):
        self.aircraft = aircraft
        self.center_lat, self.center_lon = lat, lon
        self.radius_km, self.selected_id = radius, selected_id
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#777d82"))
        side = min(self.width(), self.height()) - 54
        cx, cy, radius = self.width() / 2, self.height() / 2, (min(self.width(), self.height()) - 54) / 2
        if radius < 20:
            p.end()
            return

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#c5c9cc"), 1))
        for f in (0.25, 0.5, 0.75, 1.0):
            p.drawEllipse(QPointF(cx, cy), radius * f, radius * f)
        p.setPen(QPen(QColor("#aeb4b8"), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(cx - radius, cy), QPointF(cx + radius, cy))
        p.drawLine(QPointF(cx, cy - radius), QPointF(cx, cy + radius))

        p.setPen(QPen(QColor("#e1e4e6"), 1))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(int(cx + 5), int(cy - radius - 7), "N")
        p.drawText(int(cx + radius + 7), int(cy + 4), "E")
        p.drawText(int(cx - radius - 15), int(cy + 4), "W")
        p.drawText(int(cx + 5), int(cy + radius + 17), "S")
        p.drawText(12, 20, f"RADIUS {self.radius_km} km")
        p.drawText(12, 38, f"CENTER {self.center_lat:.4f}, {self.center_lon:.4f}")

        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.drawLine(QPointF(cx - 8, cy), QPointF(cx + 8, cy))
        p.drawLine(QPointF(cx, cy - 8), QPointF(cx, cy + 8))

        cos_lat = max(0.01, math.cos(math.radians(self.center_lat)))
        for item in self.aircraft:
            north = math.radians(item["lat"] - self.center_lat) * 6371.0088
            east = math.radians(item["lon"] - self.center_lon) * 6371.0088 * cos_lat
            x = cx + east / self.radius_km * radius
            y = cy - north / self.radius_km * radius
            if math.hypot(x - cx, y - cy) > radius:
                continue

            heading = item.get("heading")
            if heading is None:
                heading = 0
            a = math.radians(float(heading))
            forward = QPointF(math.sin(a), -math.cos(a))
            right = QPointF(math.cos(a), math.sin(a))
            size = 10 if item.get("id") == self.selected_id else 7
            tip = QPointF(x, y) + forward * size
            left = QPointF(x, y) - forward * (size * 0.7) - right * (size * 0.55)
            right_pt = QPointF(x, y) - forward * (size * 0.7) + right * (size * 0.55)
            color = QColor("#fff36a") if item.get("id") == self.selected_id else QColor("#f2f4f5")
            p.setPen(QPen(QColor("#30363a"), 1))
            p.setBrush(QBrush(color))
            p.drawPolygon(QPolygonF([tip, left, right_pt]))
        p.end()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FR24 LIVE RADAR")
        self.resize(1320, 820)
        self.all_aircraft = []
        self.visible_aircraft = []
        self.selected_id = None
        self.worker = None

        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)

        controls = QGroupBox("Параметры радара")
        row = QHBoxLayout(controls)
        row.addWidget(QLabel("Аэропорт:"))
        self.airport = QComboBox()
        self.airport.addItems(list(AIRPORTS) + ["Ручные координаты"])
        self.airport.currentTextChanged.connect(self.on_airport_changed)
        row.addWidget(self.airport, 2)

        row.addWidget(QLabel("Широта:"))
        self.lat = QDoubleSpinBox()
        self.lat.setRange(-90, 90)
        self.lat.setDecimals(6)
        self.lat.setValue(40.4675)
        row.addWidget(self.lat)

        row.addWidget(QLabel("Долгота:"))
        self.lon = QDoubleSpinBox()
        self.lon.setRange(-180, 180)
        self.lon.setDecimals(6)
        self.lon.setValue(50.0467)
        row.addWidget(self.lon)

        row.addWidget(QLabel("Радиус:"))
        self.range = QComboBox()
        for value in RANGES_KM:
            self.range.addItem(f"{value} км", value)
        self.range.setCurrentIndex(RANGES_KM.index(50))
        row.addWidget(self.range)

        row.addWidget(QLabel("Самолётов:"))
        self.limit = QSpinBox()
        self.limit.setRange(1, 500)
        self.limit.setValue(15)
        row.addWidget(self.limit)
        main.addWidget(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Позывной", "ID", "Высота", "Скорость", "Курс", "Км"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.on_table_selection)
        splitter.addWidget(self.table)

        self.radar = RadarWidget()
        splitter.addWidget(self.radar)
        splitter.setSizes([470, 850])
        main.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.status = QLabel("Ожидание подключения к сборщику...")
        footer.addWidget(self.status, 1)
        self.updated = QLabel("Обновление: —")
        footer.addWidget(self.updated)
        main.addLayout(footer)

        self.lat.valueChanged.connect(self.refresh_view)
        self.lon.valueChanged.connect(self.refresh_view)
        self.range.currentIndexChanged.connect(self.refresh_view)
        self.limit.valueChanged.connect(self.refresh_view)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.fetch_data)
        self.timer.start(POLL_MS)
        self.fetch_data()

    def on_airport_changed(self, name):
        if name in AIRPORTS:
            lat, lon = AIRPORTS[name]
            self.lat.blockSignals(True)
            self.lon.blockSignals(True)
            self.lat.setValue(lat)
            self.lon.setValue(lon)
            self.lat.blockSignals(False)
            self.lon.blockSignals(False)
            self.refresh_view()

    def fetch_data(self):
        if self.worker is not None and self.worker.isRunning():
            return
        self.worker = FeedWorker(self)
        self.worker.received.connect(self.on_data)
        self.worker.failed.connect(self.on_error)
        self.worker.start()

    def on_data(self, payload):
        self.all_aircraft = payload.get("aircraft", [])
        self.refresh_view()
        stamp = payload.get("generated_at")
        if stamp:
            self.updated.setText("Данные сборщика: " + datetime.fromtimestamp(stamp).astimezone().strftime("%H:%M:%S"))
        self.status.setText(f"Связь установлена · Самолётов в потоке: {len(self.all_aircraft)}")
        self.status.setStyleSheet("color: #167a3e;")

    def on_error(self, message):
        self.status.setText("Нет связи с live_collector.py — запусти сборщик")
        self.status.setStyleSheet("color: #b3261e;")

    def refresh_view(self, *_):
        lat, lon = self.lat.value(), self.lon.value()
        radius = int(self.range.currentData() or 50)
        candidates = []
        now = datetime.now().timestamp()
        for item in self.all_aircraft:
            try:
                age = now - float(item.get("updated_at", now))
                if age < -60 or age > 120:
                    continue
                distance = haversine_km(lat, lon, float(item["lat"]), float(item["lon"]))
                if distance <= radius:
                    entry = dict(item)
                    entry["distance_km"] = distance
                    candidates.append(entry)
            except (KeyError, TypeError, ValueError):
                continue

        candidates.sort(key=lambda x: x["distance_km"])
        self.visible_aircraft = candidates[:self.limit.value()]
        ids = [x["id"] for x in self.visible_aircraft]
        if self.selected_id not in ids:
            self.selected_id = None

        self.table.blockSignals(True)
        self.table.setRowCount(len(self.visible_aircraft))
        for r, item in enumerate(self.visible_aircraft):
            values = [
                item.get("callsign") or "—",
                str(item.get("id", "")),
                self.format_number(item.get("altitude_ft"), " ft"),
                self.format_number(item.get("speed_knots"), " kt"),
                self.format_number(item.get("heading"), "°"),
                f'{item["distance_km"]:.1f}',
            ]
            for c, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if c == 1:
                    cell.setData(Qt.ItemDataRole.UserRole, item["id"])
                self.table.setItem(r, c, cell)
                if item.get("id") == self.selected_id:
                    cell.setBackground(QColor("#fff2a8"))
        self.table.blockSignals(False)
        self.radar.set_data(self.visible_aircraft, lat, lon, radius, self.selected_id)

    @staticmethod
    def format_number(value, suffix):
        if value is None:
            return "—"
        try:
            number = float(value)
            if not math.isfinite(number):
                return "—"
            return f"{int(number)}{suffix}" if number.is_integer() else f"{number:.0f}{suffix}"
        except (TypeError, ValueError):
            return "—"

    def on_table_selection(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.selected_id = None
        else:
            cell = self.table.item(rows[0].row(), 1)
            self.selected_id = cell.data(Qt.ItemDataRole.UserRole) if cell else None
        self.radar.selected_id = self.selected_id
        self.radar.update()

    def closeEvent(self, event):
        self.timer.stop()
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(2500)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
