import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6 import QtCharts, QtCore, QtGui, QtWidgets


APP_NAME = "Capacity Scout"
APP_VERSION = "0.1.0.1"
APP_AUTHOR_NAME = "Baz00k@"
APP_AUTHOR_EMAIL = "baz00k@email.cz"
APP_AUTHOR_WEBSITE = "https://bazooka-cz.webnode.cz/"
APP_RELEASE_YEAR = "2026/01"
DB_PATH = "capacity.db"
CONFIG_PATH = "cs_config.json"


def bytes_to_gb(value: int) -> float:
    return value / (1024 ** 3)


def format_delta(value: int) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}{abs(bytes_to_gb(value)):.2f} GB"


@dataclass
class Location:
    location_id: int
    name: str
    path: str
    kind: str


class ConfigManager:
    def __init__(self, path: str = CONFIG_PATH) -> None:
        self.path = Path(path)
        self.data = {
            "colors": {
                "capacity": "#ff0000",
                "delta": "#00aa00",
            },
            "poll_minutes": 10,
            "session_update_minutes": 60,
            "db_flush_minutes": 60,
        }
        self.load()

    def load(self) -> None:
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as handle:
                self.data.update(json.load(handle))

    def save(self) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(self.data, handle, indent=2, ensure_ascii=False)


class Database:
    def __init__(self, path: str = DB_PATH) -> None:
        self.path = path
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.dirty = False
        self.init_schema()

    def init_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                kind TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                location_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                capacity_bytes INTEGER NOT NULL,
                delta_bytes INTEGER NOT NULL,
                UNIQUE(location_id, day),
                FOREIGN KEY(location_id) REFERENCES locations(id)
            )
            """
        )
        self.conn.commit()

    def add_location(self, name: str, path: str, kind: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO locations (name, path, kind) VALUES (?, ?, ?)",
            (name, path, kind),
        )
        self.dirty = True
        return cursor.lastrowid

    def remove_location(self, location_id: int) -> None:
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM daily_stats WHERE location_id = ?", (location_id,))
        cursor.execute("DELETE FROM locations WHERE id = ?", (location_id,))
        self.dirty = True

    def list_locations(self) -> List[Location]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, name, path, kind FROM locations ORDER BY name")
        rows = cursor.fetchall()
        return [Location(row["id"], row["name"], row["path"], row["kind"]) for row in rows]

    def last_stat_for_location(self, location_id: int) -> Optional[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM daily_stats WHERE location_id = ? ORDER BY day DESC LIMIT 1",
            (location_id,),
        )
        return cursor.fetchone()

    def stat_for_day(self, location_id: int, day: str) -> Optional[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM daily_stats WHERE location_id = ? AND day = ?",
            (location_id, day),
        )
        return cursor.fetchone()

    def last_stat_before_day(self, location_id: int, day: str) -> Optional[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM daily_stats WHERE location_id = ? AND day < ? ORDER BY day DESC LIMIT 1",
            (location_id, day),
        )
        return cursor.fetchone()

    def insert_daily_stat(
        self, location_id: int, day: str, capacity_bytes: int, delta_bytes: int
    ) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO daily_stats (location_id, day, capacity_bytes, delta_bytes)
            VALUES (?, ?, ?, ?)
            """,
            (location_id, day, capacity_bytes, delta_bytes),
        )
        self.dirty = True

    def stats_for_location(self, location_id: int) -> List[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT day, capacity_bytes, delta_bytes FROM daily_stats WHERE location_id = ? ORDER BY day",
            (location_id,),
        )
        return cursor.fetchall()

    def commit_if_dirty(self) -> None:
        if self.dirty:
            self.conn.commit()
            self.dirty = False


class LocationSize:
    @staticmethod
    def disk_usage(path: str) -> int:
        usage = shutil.disk_usage(path)
        return usage.used

    @staticmethod
    def folder_size(path: str) -> int:
        total = 0
        for root, _dirs, files in os.walk(path):
            for filename in files:
                file_path = os.path.join(root, filename)
                try:
                    total += os.path.getsize(file_path)
                except OSError:
                    continue
        return total

    @staticmethod
    def compute(path: str, kind: str) -> int:
        if kind == "Disk":
            return LocationSize.disk_usage(path)
        return LocationSize.folder_size(path)


class AddLocationDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Přidat umístění")
        self.name_input = QtWidgets.QLineEdit()
        self.path_input = QtWidgets.QLineEdit()
        self.kind_combo = QtWidgets.QComboBox()
        self.kind_combo.addItems(["Disk", "Adresář"])
        browse_button = QtWidgets.QPushButton("Vybrat")
        browse_button.clicked.connect(self.browse_path)

        form_layout = QtWidgets.QFormLayout()
        form_layout.addRow("Název", self.name_input)

        path_layout = QtWidgets.QHBoxLayout()
        path_layout.addWidget(self.path_input)
        path_layout.addWidget(browse_button)
        form_layout.addRow("Umístění", path_layout)

        form_layout.addRow("Typ", self.kind_combo)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form_layout)
        layout.addWidget(buttons)

    def browse_path(self) -> None:
        if self.kind_combo.currentText() == "Disk":
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "Vybrat disk")
        else:
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "Vybrat adresář")
        if path:
            self.path_input.setText(path)

    def values(self) -> Tuple[str, str, str]:
        kind_map = {"Disk": "Disk", "Adresář": "Folder"}
        return (
            self.name_input.text().strip(),
            self.path_input.text().strip(),
            kind_map[self.kind_combo.currentText()],
        )


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, config: ConfigManager, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Nastavení")
        self.capacity_color = QtWidgets.QPushButton()
        self.delta_color = QtWidgets.QPushButton()
        self.poll_spin = QtWidgets.QSpinBox()
        self.poll_spin.setRange(1, 120)
        self.poll_spin.setSuffix(" min")
        self.poll_spin.setValue(self.config.data.get("poll_minutes", 10))
        self.session_spin = QtWidgets.QSpinBox()
        self.session_spin.setRange(1, 240)
        self.session_spin.setSuffix(" min")
        self.session_spin.setValue(self.config.data.get("session_update_minutes", 60))
        self.flush_combo = QtWidgets.QComboBox()
        self.flush_options = [
            ("30 minut", 30),
            ("1 hodina", 60),
            ("2 hodiny", 120),
            ("4 hodiny", 240),
            ("6 hodin", 360),
            ("8 hodin", 480),
            ("12 hodin", 720),
            ("24 hodin", 1440),
        ]
        for label, minutes in self.flush_options:
            self.flush_combo.addItem(label, minutes)
        current_flush = self.config.data.get("db_flush_minutes", 60)
        current_index = next(
            (
                index
                for index, (_label, minutes) in enumerate(self.flush_options)
                if minutes == current_flush
            ),
            1,
        )
        self.flush_combo.setCurrentIndex(current_index)
        self.update_button_colors()

        self.capacity_color.clicked.connect(lambda: self.pick_color("capacity"))
        self.delta_color.clicked.connect(lambda: self.pick_color("delta"))

        form_layout = QtWidgets.QFormLayout()
        form_layout.addRow("Barva kapacity", self.capacity_color)
        form_layout.addRow("Barva změny", self.delta_color)
        form_layout.addRow("Interval měření", self.poll_spin)
        form_layout.addRow("Aktualizace sezení", self.session_spin)
        form_layout.addRow("Uložit DB na disk", self.flush_combo)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form_layout)
        layout.addWidget(buttons)

    def update_button_colors(self) -> None:
        for key, button in ("capacity", self.capacity_color), ("delta", self.delta_color):
            color = self.config.data["colors"][key]
            button.setStyleSheet(f"background-color: {color};")

    def pick_color(self, key: str) -> None:
        current = QtGui.QColor(self.config.data["colors"][key])
        color = QtWidgets.QColorDialog.getColor(current, self)
        if color.isValid():
            self.config.data["colors"][key] = color.name()
            self.update_button_colors()

    def accept(self) -> None:
        self.config.data["poll_minutes"] = self.poll_spin.value()
        self.config.data["session_update_minutes"] = self.session_spin.value()
        self.config.data["db_flush_minutes"] = self.flush_combo.currentData()
        self.config.save()
        super().accept()


class MonitorWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 700)

        self.config = ConfigManager()
        self.db = Database()
        self.locations: Dict[int, Location] = {
            location.location_id: location for location in self.db.list_locations()
        }

        self.location_combo = QtWidgets.QComboBox()
        self.location_combo.currentIndexChanged.connect(self.refresh_chart)
        self.stats_label = QtWidgets.QLabel("--")
        self.status_label = QtWidgets.QLabel("")

        self.add_button = QtWidgets.QPushButton("Přidat")
        self.remove_button = QtWidgets.QPushButton("Odebrat")
        self.settings_button = QtWidgets.QPushButton("Nastavení")

        self.add_button.clicked.connect(self.add_location)
        self.remove_button.clicked.connect(self.remove_location)
        self.settings_button.clicked.connect(self.open_settings)

        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItems(["Vše", "Týden", "Měsíc", "Rok"])
        self.filter_combo.currentIndexChanged.connect(self.refresh_chart)
        self.filter_date = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.filter_date.setCalendarPopup(True)
        self.filter_date.dateChanged.connect(self.refresh_chart)

        self.chart = QtCharts.QChart()
        self.chart_view = QtCharts.QChartView(self.chart)
        self.chart_view.setRenderHint(QtGui.QPainter.Antialiasing)

        controls_layout = QtWidgets.QHBoxLayout()
        controls_layout.addWidget(QtWidgets.QLabel("Umístění:"))
        controls_layout.addWidget(self.location_combo)
        controls_layout.addWidget(QtWidgets.QLabel("Filtr:"))
        controls_layout.addWidget(self.filter_combo)
        controls_layout.addWidget(self.filter_date)
        controls_layout.addStretch()
        controls_layout.addWidget(self.add_button)
        controls_layout.addWidget(self.remove_button)
        controls_layout.addWidget(self.settings_button)

        main_layout = QtWidgets.QVBoxLayout()
        main_layout.addLayout(controls_layout)
        main_layout.addWidget(self.stats_label)
        main_layout.addWidget(self.chart_view, stretch=1)
        main_layout.addWidget(self.status_label)

        container = QtWidgets.QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.timeout.connect(self.poll_locations)
        self.poll_timer.start(self.config.data.get("poll_minutes", 10) * 60 * 1000)

        self.daily_timer = QtCore.QTimer(self)
        self.daily_timer.timeout.connect(self.check_daily_record)
        self.daily_timer.start(60 * 1000)

        self.session_timer = QtCore.QTimer(self)
        self.session_timer.timeout.connect(self.update_today_stats)
        self.session_timer.start(
            self.config.data.get("session_update_minutes", 60) * 60 * 1000
        )

        self.db_timer = QtCore.QTimer(self)
        self.db_timer.timeout.connect(self.db.commit_if_dirty)
        self.db_timer.start(
            self.config.data.get("db_flush_minutes", 60) * 60 * 1000
        )

        self.populate_locations()
        self.refresh_chart()

    def populate_locations(self) -> None:
        self.location_combo.blockSignals(True)
        self.location_combo.clear()
        for location in self.locations.values():
            self.location_combo.addItem(location.name, location.location_id)
        self.location_combo.blockSignals(False)

    def add_location(self) -> None:
        dialog = AddLocationDialog(self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            name, path, kind = dialog.values()
            if not name or not path:
                QtWidgets.QMessageBox.warning(self, "Chyba", "Vyplňte název a cestu.")
                return
            location_id = self.db.add_location(name, path, kind)
            self.locations[location_id] = Location(location_id, name, path, kind)
            self.populate_locations()
            self.location_combo.setCurrentIndex(self.location_combo.count() - 1)
            self.poll_locations()

    def remove_location(self) -> None:
        location_id = self.location_combo.currentData()
        if location_id is None:
            return
        location = self.locations[location_id]
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Odebrat",
            f"Opravdu odstranit {location.name}?",
        )
        if confirm == QtWidgets.QMessageBox.Yes:
            self.db.remove_location(location_id)
            self.locations.pop(location_id, None)
            self.populate_locations()
            self.refresh_chart()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.poll_timer.start(self.config.data.get("poll_minutes", 10) * 60 * 1000)
            self.session_timer.start(
                self.config.data.get("session_update_minutes", 60) * 60 * 1000
            )
            self.db_timer.start(
                self.config.data.get("db_flush_minutes", 60) * 60 * 1000
            )
            self.refresh_chart()

    def poll_locations(self) -> None:
        if not self.locations:
            return
        for location in self.locations.values():
            try:
                size_bytes = LocationSize.compute(location.path, location.kind)
            except (OSError, FileNotFoundError):
                continue
            self.status_label.setText(
                f"{location.name}: {bytes_to_gb(size_bytes):.2f} GB"  # noqa: QLCD001
            )
        self.check_daily_record()

    def check_daily_record(self) -> None:
        self.update_today_stats(force_update=False)

    def update_today_stats(self, force_update: bool = True) -> None:
        today = date.today().isoformat()
        for location in self.locations.values():
            existing = self.db.stat_for_day(location.location_id, today)
            if existing and not force_update:
                continue
            try:
                size_bytes = LocationSize.compute(location.path, location.kind)
            except (OSError, FileNotFoundError):
                continue
            previous = self.db.last_stat_before_day(location.location_id, today)
            delta = 0
            if previous:
                delta = size_bytes - previous["capacity_bytes"]
            self.db.insert_daily_stat(location.location_id, today, size_bytes, delta)
        self.refresh_chart()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.db.commit_if_dirty()
        super().closeEvent(event)

    def refresh_chart(self) -> None:
        location_id = self.location_combo.currentData()
        self.chart.removeAllSeries()
        self.chart.setTitle("Historie kapacity")
        if location_id is None:
            self.stats_label.setText("Vyberte umístění.")
            return
        stats = self.filtered_stats(self.db.stats_for_location(location_id))
        if not stats:
            self.stats_label.setText("Zatím nejsou data.")
            return

        capacity_series = QtCharts.QLineSeries()
        delta_series = QtCharts.QLineSeries()
        capacity_series.setName("Kapacita")
        delta_series.setName("Změna")

        capacity_color = QtGui.QColor(self.config.data["colors"]["capacity"])
        delta_color = QtGui.QColor(self.config.data["colors"]["delta"])
        capacity_series.setColor(capacity_color)
        delta_series.setColor(delta_color)

        for row in stats:
            day_value = datetime.strptime(row["day"], "%Y-%m-%d")
            x_value = QtCore.QDateTime(day_value).toMSecsSinceEpoch()
            capacity_series.append(x_value, bytes_to_gb(row["capacity_bytes"]))
            delta_series.append(x_value, bytes_to_gb(row["delta_bytes"]))

        self.chart.addSeries(capacity_series)
        self.chart.addSeries(delta_series)

        axis_x = QtCharts.QDateTimeAxis()
        axis_x.setFormat("dd.MM")
        axis_x.setTitleText("Datum")
        self.chart.addAxis(axis_x, QtCore.Qt.AlignBottom)
        capacity_series.attachAxis(axis_x)
        delta_series.attachAxis(axis_x)

        axis_y = QtCharts.QValueAxis()
        axis_y.setTitleText("GB")
        self.chart.addAxis(axis_y, QtCore.Qt.AlignLeft)
        capacity_series.attachAxis(axis_y)
        delta_series.attachAxis(axis_y)

        last = stats[-1]
        self.stats_label.setText(
            "Poslední záznam: "
            f"{last['day']} | Kapacita {bytes_to_gb(last['capacity_bytes']):.2f} GB "
            f"| Změna {format_delta(last['delta_bytes'])}"
        )

    def filtered_stats(self, stats: List[sqlite3.Row]) -> List[sqlite3.Row]:
        period = self.filter_combo.currentText()
        if period == "Vše":
            return stats
        selected = self.filter_date.date().toPython()
        if period == "Týden":
            start = selected - timedelta(days=selected.weekday())
            end = start + timedelta(days=6)
        elif period == "Měsíc":
            start = date(selected.year, selected.month, 1)
            if selected.month == 12:
                end = date(selected.year, 12, 31)
            else:
                next_month = date(selected.year, selected.month + 1, 1)
                end = next_month - timedelta(days=1)
        else:
            start = date(selected.year, 1, 1)
            end = date(selected.year, 12, 31)

        filtered: List[sqlite3.Row] = []
        for row in stats:
            row_date = datetime.strptime(row["day"], "%Y-%m-%d").date()
            if start <= row_date <= end:
                filtered.append(row)
        return filtered


def main() -> None:
    app = QtWidgets.QApplication([])
    app.setApplicationName(APP_NAME)
    icon_path = Path(__file__).with_name("cs_icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    window = MonitorWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
