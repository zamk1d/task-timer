import sys
import os
import json
import math
import time
import uuid
import socket
import signal
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QStackedWidget, QScrollArea, QFrame, QButtonGroup,
    QGraphicsDropShadowEffect, QMenu
)
from PySide6.QtCore import (
    Qt, QTimer, Signal, QPropertyAnimation, QEasingCurve, QSize,
    QFileSystemWatcher, QSocketNotifier
)
from PySide6.QtGui import (
    QColor, QPainter, QBrush, QRadialGradient, QLinearGradient, QFont,
    QShortcut, QKeySequence, QAction
)

# ---------------------------------------------------------------------------
# Пути: данные (записи/теги) отдельно от конфига интерфейса
# ---------------------------------------------------------------------------

DATA_DIR = Path.home() / ".local" / "share" / "task-timer"
DATA_FILE = DATA_DIR / "history.json"
TAGS_FILE = DATA_DIR / "tags.json"
PID_FILE = DATA_DIR / "app.pid"

# Конфиг интерфейса лежит отдельно, в ~/.config — так его удобно шарить
# между несколькими твоими скриптами и редактировать вручную/скриптом,
# не трогая данные приложения.
CONFIG_DIR = Path.home() / ".config" / "task-timer"
STYLE_FILE = CONFIG_DIR / "style.json"


DEFAULT_STYLE_CONFIG = {
    "colors": {
        "window_grad_start": "#10101a",
        "window_grad_end": "#0a0a12",
        "blob_1": "#4f46e5",
        "blob_2": "#7c3aed",
        "blob_3": "#2563eb",
        "edge_highlight": "#ffffff",
        "input_bg": "#ffffff",
        "input_border": "#ffffff",
        "text_primary": "#ffffff",
        "text_secondary": "#ffffff",
        "accent_idle_start": "#6d28d9",
        "accent_idle_end": "#4f46e5",
        "accent_running_start": "#ef4444",
        "accent_running_end": "#f97316",
        "difficulty_easy": "#22c55e",
        "difficulty_medium": "#a3e635",
        "difficulty_hard": "#ef4444",
        "difficulty_epic": "#a855f7",
        "danger": "#f87171"
    },
    "opacity": {
        "window_grad_start_alpha": 95,
        "window_grad_end_alpha": 120,
        "blob_alpha": 135,
        "edge_highlight_alpha": 35,
        "input_bg_alpha": 35,
        "input_border_alpha": 50,
        "input_border_focus_alpha": 110,
        "text_primary_alpha": 220,
        "text_secondary_alpha": 170,
        "title_btn_text_alpha": 190,
        "title_btn_hover_alpha": 35,
        "title_btn_checked_alpha": 55,
        "difficulty_idle_alpha": 65,
        "difficulty_idle_border_alpha": 150,
        "difficulty_hover_alpha": 100,
        "difficulty_hover_border_alpha": 210,
        "difficulty_checked_alpha": 225,
        "danger_alpha": 200
    },
    "difficulty_labels": ["Лёгкая", "Средняя", "Сложная", "Эпик"],
    "difficulty_color_keys": [
        "difficulty_easy", "difficulty_medium", "difficulty_hard", "difficulty_epic"
    ],
    # Палитра для тегов: тег №0 получает tag_palette[0], тег №1 — tag_palette[1]
    # и т.д. Когда теги закончат палитру, цвет для новых тегов генерируется
    # автоматически (см. resolve_tag_color) так, чтобы соседние оттенки не
    # повторялись.
    "tag_palette": [
        "#f87171", "#fb923c", "#facc15", "#a3e635",
        "#4ade80", "#2dd4bf", "#38bdf8", "#818cf8",
        "#c084fc", "#f472b6", "#fb7185", "#94a3b8"
    ],
    "animation": {
        "theme_transition_ms": 450,
        "blob_speed_scale": 1.0
    },
    "window": {
        "full_width": 320,
        "full_height": 440,
        "compact_width": 190,
        "compact_height": 46
    }
}


def deep_merge(base: dict, override: dict) -> dict:
    """Рекурсивно дополняет base значениями override, не теряя новые
    ключи по умолчанию, если в файле пользователя их ещё нет."""
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def ensure_data_file():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        DATA_FILE.write_text("[]", encoding="utf-8")
    if not TAGS_FILE.exists():
        TAGS_FILE.write_text("[]", encoding="utf-8")


def ensure_style_file():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not STYLE_FILE.exists():
        STYLE_FILE.write_text(
            json.dumps(DEFAULT_STYLE_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )


def load_style_config() -> dict:
    ensure_style_file()
    try:
        raw = json.loads(STYLE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raw = {}
    return deep_merge(DEFAULT_STYLE_CONFIG, raw)


# ---------------------------------------------------------------------------
# История задач
# ---------------------------------------------------------------------------

def load_history():
    ensure_data_file()
    try:
        history = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        history = []
    for record in history:
        record.setdefault("tags", [])
    return history


def _write_history(history):
    DATA_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def save_record(record: dict):
    history = load_history()
    history.append(record)
    _write_history(history)


def delete_record(record_id: str):
    history = [r for r in load_history() if r.get("id") != record_id]
    _write_history(history)


def clear_all_records():
    _write_history([])


def update_record_tags(record_id: str, tag_ids: list):
    history = load_history()
    for r in history:
        if r.get("id") == record_id:
            r["tags"] = tag_ids
            break
    _write_history(history)


def format_duration(seconds: int) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}ч {m}м"
    if m:
        return f"{m}м {s}с"
    return f"{s}с"


def format_hms(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Теги
# ---------------------------------------------------------------------------

def load_tags():
    ensure_data_file()
    try:
        return json.loads(TAGS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _write_tags(tags):
    TAGS_FILE.write_text(json.dumps(tags, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_tag_color(index: int, palette: list) -> str:
    """Цвет тега не выбирается вручную: либо берётся по номеру из палитры
    в конфиге, либо, если теги уже исчерпали палитру, генерируется
    автоматически по методу золотого угла — так соседние цвета не похожи
    друг на друга даже при сотнях тегов."""
    if 0 <= index < len(palette):
        return palette[index]
    hue = (index * 137.508) % 360.0
    c = QColor.fromHsvF(hue / 360.0, 0.55, 0.92)
    return c.name()


def create_tag(name: str) -> dict:
    tags = load_tags()
    name = name.strip()
    config = load_style_config()
    palette = config.get("tag_palette", DEFAULT_STYLE_CONFIG["tag_palette"])
    tag = {
        "id": uuid.uuid4().hex,
        "name": name,
        "color": resolve_tag_color(len(tags), palette),
        "created_at": datetime.now().isoformat(),
    }
    tags.append(tag)
    _write_tags(tags)
    return tag


def delete_tag(tag_id: str):
    tags = [t for t in load_tags() if t.get("id") != tag_id]
    _write_tags(tags)
    # чистим ссылки на этот тег во всех записях истории
    history = load_history()
    changed = False
    for r in history:
        if tag_id in r.get("tags", []):
            r["tags"] = [t for t in r["tags"] if t != tag_id]
            changed = True
    if changed:
        _write_history(history)


def rgba_from_qcolor(c: QColor, alpha) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{int(alpha)})"


def add_text_shadow(widget, blur=10, alpha=170, dx=0, dy=1):
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setColor(QColor(0, 0, 0, alpha))
    effect.setOffset(dx, dy)
    widget.setGraphicsEffect(effect)


# ---------------------------------------------------------------------------
# Менеджер темы: хранит текущие (анимированные) цвета/прозрачности,
# следит за файлом конфига и плавно переезжает к новым значениям при
# его изменении на диске.
# ---------------------------------------------------------------------------

def _lerp(a, b, t):
    return a + (b - a) * t


def _lerp_color(c1: QColor, c2: QColor, t: float) -> QColor:
    return QColor(
        round(_lerp(c1.red(), c2.red(), t)),
        round(_lerp(c1.green(), c2.green(), t)),
        round(_lerp(c1.blue(), c2.blue(), t)),
    )


class ThemeManager:
    """Плоский объект (не QObject) — сигналы нам тут не нужны, подписчики
    регистрируются напрямую через register(). Внутри держит QTimer и
    QFileSystemWatcher для анимации и слежения за файлом конфига."""

    def __init__(self):
        self.config = load_style_config()
        self.current_colors = {k: QColor(v) for k, v in self.config["colors"].items()}
        self.current_opacity = {k: float(v) for k, v in self.config["opacity"].items()}
        self.tag_palette = list(self.config.get("tag_palette", DEFAULT_STYLE_CONFIG["tag_palette"]))
        self.window_cfg = dict(self.config.get("window", DEFAULT_STYLE_CONFIG["window"]))
        self.blob_speed_scale = float(self.config.get("animation", {}).get("blob_speed_scale", 1.0))

        self._from_colors = dict(self.current_colors)
        self._from_opacity = dict(self.current_opacity)
        self._target_colors = dict(self.current_colors)
        self._target_opacity = dict(self.current_opacity)
        self._anim_duration = 0.45
        self._anim_clock = 0.0
        self._anim_active = False

        self._subscribers = []

        self._anim_timer = QTimer()
        self._anim_timer.timeout.connect(self._step)

        self._watcher = QFileSystemWatcher()
        self._watcher.addPath(str(STYLE_FILE))
        self._watcher.addPath(str(CONFIG_DIR))
        self._watcher.fileChanged.connect(self._on_fs_event)
        self._watcher.directoryChanged.connect(self._on_fs_event)

        self._reload_debounce = QTimer()
        self._reload_debounce.setSingleShot(True)
        self._reload_debounce.timeout.connect(self._reload_from_disk)

    # --- подписка виджетов на изменения темы -----------------------------
    def register(self, fn):
        """Регистрирует функцию без аргументов, которая перечитывает
        tm.current_colors/current_opacity и обновляет стиль виджета.
        Вызывается сразу же, чтобы применить исходную тему."""
        self._subscribers.append(fn)
        fn()

    def _notify(self):
        for fn in list(self._subscribers):
            try:
                fn()
            except RuntimeError:
                # виджет уже удалён (закрыта вкладка и т.п.) — просто пропускаем
                self._subscribers.remove(fn)

    # --- чтение цветов -----------------------------------------------------
    def hexcolor(self, key: str) -> str:
        return self.current_colors.get(key, QColor("#ffffff")).name()

    def rgba(self, color_key: str, alpha_key: str = None, alpha=None) -> str:
        c = self.current_colors.get(color_key, QColor("#ffffff"))
        if alpha is None:
            alpha = self.current_opacity.get(alpha_key, 255) if alpha_key else 255
        return rgba_from_qcolor(c, alpha)

    # --- слежение за файлом -------------------------------------------------
    def _on_fs_event(self, _path):
        if str(STYLE_FILE) not in self._watcher.files() and STYLE_FILE.exists():
            self._watcher.addPath(str(STYLE_FILE))
        # редакторы часто сохраняют файл через replace — путь выпадает из
        # watcher'а, поэтому подписываемся заново и ждём немного, чтобы не
        # реагировать на каждую промежуточную запись отдельно.
        self._reload_debounce.start(150)

    def _reload_from_disk(self):
        try:
            new_config = load_style_config()
        except Exception:
            return
        self.config = new_config
        self.tag_palette = list(new_config.get("tag_palette", self.tag_palette))
        self.window_cfg = dict(new_config.get("window", self.window_cfg))
        self.blob_speed_scale = float(new_config.get("animation", {}).get("blob_speed_scale", 1.0))
        self._begin_transition(new_config["colors"], new_config["opacity"],
                                new_config.get("animation", {}).get("theme_transition_ms", 450))

    def _begin_transition(self, colors: dict, opacity: dict, duration_ms: float):
        self._from_colors = dict(self.current_colors)
        self._from_opacity = dict(self.current_opacity)
        self._target_colors = {k: QColor(v) for k, v in colors.items()}
        self._target_opacity = {k: float(v) for k, v in opacity.items()}
        self._anim_duration = max(0.05, float(duration_ms) / 1000.0)
        self._anim_clock = time.monotonic()
        self._anim_active = True
        if not self._anim_timer.isActive():
            self._anim_timer.start(16)

    def _step(self):
        elapsed = time.monotonic() - self._anim_clock
        t = min(1.0, elapsed / self._anim_duration)
        eased = 1 - (1 - t) ** 3  # ease-out cubic — приятнее линейного

        for key, target in self._target_colors.items():
            start = self._from_colors.get(key, target)
            self.current_colors[key] = _lerp_color(start, target, eased)
        for key, target in self._target_opacity.items():
            start = self._from_opacity.get(key, target)
            self.current_opacity[key] = _lerp(start, target, eased)

        self._notify()

        if t >= 1.0:
            self._anim_timer.stop()
            self._anim_active = False


# ---------------------------------------------------------------------------
# Стили, зависящие от текущей темы. Строятся заново на каждый тик анимации
# для подписанных виджетов (их немного, это дёшево).
# ---------------------------------------------------------------------------

def build_glass_input_style(tm: ThemeManager) -> str:
    return f"""
QLineEdit {{
    background: {tm.rgba('input_bg', 'input_bg_alpha')};
    border: 1px solid {tm.rgba('input_border', 'input_border_alpha')};
    border-radius: 10px;
    padding: 8px 12px;
    color: {tm.rgba('text_primary', 'text_primary_alpha', alpha=255)};
    font-size: 13px;
    font-weight: 500;
}}
QLineEdit:focus {{
    border: 1px solid {tm.rgba('input_border', 'input_border_focus_alpha')};
}}
"""


def build_title_btn_style(tm: ThemeManager) -> str:
    return f"""
QPushButton {{
    background: transparent;
    color: {tm.rgba('text_primary', 'title_btn_text_alpha')};
    border: none;
    font-size: 13px;
    font-weight: 600;
    border-radius: 6px;
    padding: 4px 8px;
}}
QPushButton:hover {{
    background: {tm.rgba('text_primary', 'title_btn_hover_alpha')};
    color: white;
}}
QPushButton:checked {{
    background: {tm.rgba('text_primary', 'title_btn_checked_alpha')};
    color: white;
}}
"""


def build_difficulty_style(tm: ThemeManager, color_key: str) -> str:
    return f"""
QPushButton {{
    background: {tm.rgba(color_key, 'difficulty_idle_alpha')};
    color: white;
    border: 1px solid {tm.rgba(color_key, 'difficulty_idle_border_alpha')};
    border-radius: 9px;
    padding: 5px 10px;
    font-size: 11px;
    font-weight: 700;
}}
QPushButton:hover {{
    background: {tm.rgba(color_key, 'difficulty_hover_alpha')};
    border: 1px solid {tm.rgba(color_key, 'difficulty_hover_border_alpha')};
}}
QPushButton:checked {{
    background: {tm.rgba(color_key, 'difficulty_checked_alpha')};
    border: 1px solid {tm.hexcolor(color_key)};
    color: white;
}}
"""


def build_accent_style(tm: ThemeManager, running: bool) -> str:
    if running:
        c1, c2 = tm.hexcolor('accent_running_start'), tm.hexcolor('accent_running_end')
    else:
        c1, c2 = tm.hexcolor('accent_idle_start'), tm.hexcolor('accent_idle_end')
    return f"""
QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c1}, stop:1 {c2});
    color: white;
    border: none;
    border-radius: 12px;
    padding: 8px 18px;
    font-size: 14px;
    font-weight: 700;
}}
"""


def build_danger_label_style(tm: ThemeManager) -> str:
    return f"""
QPushButton {{
    background: transparent;
    color: {tm.rgba('danger', 'danger_alpha')};
    border: none;
    font-size: 11px;
    font-weight: 700;
}}
QPushButton:hover {{ color: {tm.hexcolor('danger')}; text-decoration: underline; }}
"""


def build_menu_style(tm: ThemeManager) -> str:
    # QMenu не наследует стили родителя автоматически, а на прозрачном
    # фоне без явного стиля выглядит как чёрный прямоугольник, где текст
    # виден только под курсором — поэтому задаём непрозрачный фон и цвет
    # текста явно.
    return f"""
QMenu {{
    background-color: rgb(28, 28, 38);
    border: 1px solid {tm.rgba('edge_highlight', None, alpha=60)};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{
    background: transparent;
    color: white;
    padding: 6px 24px 6px 10px;
    border-radius: 6px;
    font-size: 12px;
}}
QMenu::item:selected {{
    background-color: {tm.rgba('accent_idle_start', None, alpha=140)};
    color: white;
}}
QMenu::item:disabled {{
    color: rgba(255, 255, 255, 120);
}}
QMenu::separator {{
    height: 1px;
    background: {tm.rgba('edge_highlight', None, alpha=45)};
    margin: 5px 4px;
}}
QMenu::indicator {{
    width: 12px;
    height: 12px;
    left: 6px;
}}
"""


def make_difficulty_button(tm: ThemeManager, label: str, color_key: str) -> QPushButton:
    btn = QPushButton(label)
    btn.setCheckable(True)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    tm.register(lambda: btn.setStyleSheet(build_difficulty_style(tm, color_key)))
    return btn


# ---------------------------------------------------------------------------
# Стеклянная "жидкая" панель — базовый класс окна, теперь ещё и умеет
# перетаскивается мышью за любую точку (размер фиксирован).
# ---------------------------------------------------------------------------

class LiquidGlassWindow(QWidget):
    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.tm = tm
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self._blob_keys = ["blob_1", "blob_2", "blob_3"]
        self._blob_layout = [
            (0.15, 0.2, 150, 0.6, 0.0),
            (0.85, 0.3, 160, 0.45, 2.1),
            (0.6, 0.85, 170, 0.5, 4.0),
        ]
        self._tick = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._advance_animation)
        self._anim_timer.start(33)

        self._drag_pos = None

        # перекраска фона при смене темы (цвета читаются напрямую из tm
        # в paintEvent, тут только просим Qt перерисовать окно)
        self.tm.register(lambda: self.update())

    def _advance_animation(self):
        self._tick += 0.02 * self.tm.blob_speed_scale
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        base = QLinearGradient(0, 0, rect.width(), rect.height())
        c1 = QColor(self.tm.current_colors["window_grad_start"])
        c1.setAlpha(int(self.tm.current_opacity["window_grad_start_alpha"]))
        c2 = QColor(self.tm.current_colors["window_grad_end"])
        c2.setAlpha(int(self.tm.current_opacity["window_grad_end_alpha"]))
        base.setColorAt(0.0, c1)
        base.setColorAt(1.0, c2)
        painter.fillRect(rect, QBrush(base))

        w, h = rect.width(), rect.height()
        blob_alpha = int(self.tm.current_opacity["blob_alpha"])
        for key, (rel_x, rel_y, blob_radius, speed, phase) in zip(self._blob_keys, self._blob_layout):
            dx = math.sin(self._tick * speed + phase) * (w * 0.08)
            dy = math.cos(self._tick * speed * 0.8 + phase) * (h * 0.08)
            cx = rel_x * w + dx
            cy = rel_y * h + dy
            grad = QRadialGradient(cx, cy, blob_radius)
            c = QColor(self.tm.current_colors[key])
            c.setAlpha(blob_alpha)
            grad.setColorAt(0.0, c)
            c_edge = QColor(c)
            c_edge.setAlpha(0)
            grad.setColorAt(1.0, c_edge)
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(int(cx - blob_radius), int(cy - blob_radius),
                                 blob_radius * 2, blob_radius * 2)

        edge_c = QColor(self.tm.current_colors["edge_highlight"])
        edge_c.setAlpha(int(self.tm.current_opacity["edge_highlight_alpha"]))
        painter.setPen(edge_c)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

    # --- перетаскивание окна (без ресайза — размер фиксирован) --------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


# ---------------------------------------------------------------------------
# Плавный скролл
# ---------------------------------------------------------------------------

class SmoothScrollArea(QScrollArea):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(320)
        self._target = 0

    def wheelEvent(self, event):
        bar = self.verticalScrollBar()
        delta = event.angleDelta().y()
        step = int(delta * 0.7)

        if self._anim.state() == QPropertyAnimation.State.Running:
            current_target = self._target
        else:
            current_target = bar.value()

        self._target = max(bar.minimum(), min(bar.maximum(), current_target - step))
        self._anim.stop()
        self._anim.setStartValue(bar.value())
        self._anim.setEndValue(self._target)
        self._anim.start()
        event.accept()


# ---------------------------------------------------------------------------
# Страница таймера
# ---------------------------------------------------------------------------

class TimerPage(QWidget):
    record_saved = Signal()
    elapsed_tick = Signal(str)
    started = Signal()
    stopped = Signal()

    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.tm = tm
        self._running = False
        self._start_time = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 20)
        layout.setSpacing(14)

        self.task_input = QLineEdit()
        self.task_input.setPlaceholderText("Название задачи…")
        tm.register(lambda: self.task_input.setStyleSheet(build_glass_input_style(tm)))
        layout.addWidget(self.task_input)

        diff_row = QHBoxLayout()
        diff_row.setSpacing(6)
        self.diff_group = QButtonGroup(self)
        self.diff_group.setExclusive(True)
        self.diff_buttons = []
        labels = tm.config.get("difficulty_labels", DEFAULT_STYLE_CONFIG["difficulty_labels"])
        color_keys = tm.config.get("difficulty_color_keys", DEFAULT_STYLE_CONFIG["difficulty_color_keys"])
        self.difficulty_color_keys = color_keys
        for i, (label, color_key) in enumerate(zip(labels, color_keys)):
            btn = make_difficulty_button(tm, label, color_key)
            self.diff_group.addButton(btn, i)
            diff_row.addWidget(btn)
            self.diff_buttons.append(btn)
        if self.diff_buttons:
            self.diff_buttons[0].setChecked(True)
        layout.addLayout(diff_row)

        layout.addStretch(1)

        self.time_label = QLabel("00:00:00")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont("Monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(34)
        font.setWeight(QFont.Weight.Bold)
        self.time_label.setFont(font)
        tm.register(lambda: self.time_label.setStyleSheet(
            f"color: {tm.rgba('text_primary', 'text_primary_alpha', alpha=255)};"))
        add_text_shadow(self.time_label, blur=14, alpha=190, dy=2)
        layout.addWidget(self.time_label)

        layout.addStretch(1)

        self.toggle_btn = QPushButton("▶  Старт")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setFixedHeight(42)
        tm.register(self._restyle_toggle)
        self.toggle_btn.clicked.connect(self._on_toggle)
        layout.addWidget(self.toggle_btn)

        self.qtimer = QTimer(self)
        self.qtimer.timeout.connect(self._update_elapsed)

    def _restyle_toggle(self):
        self.toggle_btn.setText("⏸  Стоп" if self._running else "▶  Старт")
        self.toggle_btn.setStyleSheet(build_accent_style(self.tm, self._running))

    def _on_toggle(self):
        if not self._running:
            self._start()
        else:
            self._stop_and_save()

    def _start(self):
        self._running = True
        self._start_time = datetime.now()
        self.qtimer.start(200)
        self.task_input.setEnabled(False)
        for b in self.diff_buttons:
            b.setEnabled(False)
        self._restyle_toggle()
        self.started.emit()

    def _update_elapsed(self):
        elapsed = (datetime.now() - self._start_time).total_seconds()
        text = format_hms(elapsed)
        self.time_label.setText(text)
        self.elapsed_tick.emit(text)

    def _stop_and_save(self, discard=False):
        if not self._running:
            return
        self.qtimer.stop()
        end_time = datetime.now()
        duration = int((end_time - self._start_time).total_seconds())
        self._running = False

        if not discard and duration >= 1:
            task_name = self.task_input.text().strip() or "Без названия"
            diff_idx = self.diff_group.checkedId()
            if diff_idx < 0:
                diff_idx = 0
            labels = self.tm.config.get("difficulty_labels", DEFAULT_STYLE_CONFIG["difficulty_labels"])
            diff_label = labels[diff_idx]
            diff_color = self.tm.hexcolor(self.difficulty_color_keys[diff_idx])
            record = {
                "id": uuid.uuid4().hex,
                "task": task_name,
                "difficulty": diff_label,
                "difficulty_color": diff_color,
                "started_at": self._start_time.isoformat(),
                "ended_at": end_time.isoformat(),
                "duration_seconds": duration,
                "tags": [],
            }
            save_record(record)
            self.record_saved.emit()

        self.task_input.setEnabled(True)
        self.task_input.clear()
        for b in self.diff_buttons:
            b.setEnabled(True)
        self.time_label.setText("00:00:00")
        self._restyle_toggle()
        self.stopped.emit()

    def stop_if_running_for_quit(self):
        if self._running:
            self._stop_and_save(discard=False)


# ---------------------------------------------------------------------------
# Страница истории
# ---------------------------------------------------------------------------

class TagChip(QLabel):
    def __init__(self, tag: dict):
        super().__init__(tag["name"])
        c = QColor(tag["color"])
        self.setStyleSheet(f"""
            background: {rgba_from_qcolor(c, 210)};
            color: white;
            border-radius: 7px;
            padding: 1px 7px;
            font-size: 10px;
            font-weight: 700;
        """)


class HistoryRow(QFrame):
    deleted = Signal(str)
    tags_updated = Signal()
    manage_tags_requested = Signal()

    def __init__(self, tm: ThemeManager, record: dict):
        super().__init__()
        self.tm = tm
        self.record = record
        self.record_id = record["id"]
        tm.register(self._restyle)

        row = QVBoxLayout(self)
        row.setContentsMargins(4, 9, 4, 9)
        row.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        dot = QLabel()
        dot.setFixedSize(9, 9)
        dot.setStyleSheet(f"background: {record['difficulty_color']}; border-radius: 4px;")
        top_row.addWidget(dot, alignment=Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        title = QLabel(record["task"])
        title.setStyleSheet("color: white; font-size: 13px; font-weight: 700;")
        add_text_shadow(title, blur=6, alpha=180, dy=1)
        text_col.addWidget(title)

        dt = datetime.fromisoformat(record["started_at"])
        meta = QLabel(
            f"{record['difficulty']} · {format_duration(record['duration_seconds'])} · "
            f"{dt.strftime('%d.%m %H:%M')}"
        )
        tm.register(lambda: meta.setStyleSheet(
            f"color: {tm.rgba('text_secondary', 'text_secondary_alpha')}; font-size: 11px; font-weight: 600;"))
        add_text_shadow(meta, blur=5, alpha=170, dy=1)
        text_col.addWidget(meta)
        top_row.addLayout(text_col, 1)

        self.tag_btn = QPushButton("🏷+")
        self.tag_btn.setFixedSize(28, 20)
        self.tag_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tm.register(lambda: self.tag_btn.setStyleSheet(build_title_btn_style(tm)))
        self.tag_btn.clicked.connect(self._open_tag_menu)
        top_row.addWidget(self.tag_btn, alignment=Qt.AlignmentFlag.AlignTop)

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(20, 20)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tm.register(lambda: del_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {tm.rgba('text_primary', 'title_btn_hover_alpha', alpha=120)};
                border: none;
                font-size: 12px;
            }}
            QPushButton:hover {{ color: {tm.hexcolor('danger')}; }}
        """))
        del_btn.clicked.connect(lambda: self.deleted.emit(self.record_id))
        top_row.addWidget(del_btn, alignment=Qt.AlignmentFlag.AlignTop)

        row.addLayout(top_row)

        self.chips_row = QHBoxLayout()
        self.chips_row.setSpacing(4)
        self.chips_row.setContentsMargins(19, 0, 0, 0)
        row.addLayout(self.chips_row)
        self._rebuild_chips()

    def _restyle(self):
        self.setStyleSheet(f"""
            QFrame {{
                background: transparent;
                border-bottom: 1px solid {self.tm.rgba('edge_highlight', None, alpha=25)};
            }}
            QFrame:hover {{
                background: {self.tm.rgba('text_primary', None, alpha=16)};
            }}
        """)

    def _rebuild_chips(self):
        while self.chips_row.count():
            item = self.chips_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        all_tags = {t["id"]: t for t in load_tags()}
        record_tag_ids = self.record.get("tags", [])
        for tid in record_tag_ids:
            tag = all_tags.get(tid)
            if tag:
                self.chips_row.addWidget(TagChip(tag))
        self.chips_row.addStretch(1)

    def _open_tag_menu(self):
        all_tags = load_tags()
        record_tag_ids = set(self.record.get("tags", []))
        menu = QMenu(self)
        menu.setStyleSheet(build_menu_style(self.tm))
        if not all_tags:
            empty_action = QAction("Нет тегов — создайте на вкладке «Теги»", self)
            empty_action.setEnabled(False)
            menu.addAction(empty_action)
        else:
            for tag in all_tags:
                action = QAction(tag["name"], self)
                action.setCheckable(True)
                action.setChecked(tag["id"] in record_tag_ids)
                action.triggered.connect(lambda checked, tid=tag["id"]: self._toggle_tag(tid, checked))
                menu.addAction(action)
        menu.addSeparator()
        manage_action = QAction("Управление тегами…", self)
        manage_action.triggered.connect(self.manage_tags_requested.emit)
        menu.addAction(manage_action)
        menu.exec(self.tag_btn.mapToGlobal(self.tag_btn.rect().bottomLeft()))

    def _toggle_tag(self, tag_id, checked):
        current = set(self.record.get("tags", []))
        if checked:
            current.add(tag_id)
        else:
            current.discard(tag_id)
        self.record["tags"] = list(current)
        update_record_tags(self.record_id, self.record["tags"])
        self._rebuild_chips()
        self.tags_updated.emit()


class HistoryPage(QWidget):
    manage_tags_requested = Signal()

    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.tm = tm
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.total_label = QLabel("Всего: 0с")
        tm.register(lambda: self.total_label.setStyleSheet(
            f"color: {self.tm.rgba('text_primary', 'text_primary_alpha')}; font-size: 12px; font-weight: 700;"))
        add_text_shadow(self.total_label, blur=5, alpha=170, dy=1)
        header.addWidget(self.total_label)
        header.addStretch(1)

        self.clear_btn = QPushButton("Удалить всё")
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tm.register(lambda: self.clear_btn.setStyleSheet(build_danger_label_style(tm)))
        self.clear_btn.clicked.connect(self._on_clear_all)
        header.addWidget(self.clear_btn)
        layout.addLayout(header)

        self.scroll = SmoothScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().setStyleSheet("background: transparent;")

        self.list_container = QWidget()
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_container)

        layout.addWidget(self.scroll, 1)
        self.refresh()

    def refresh(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        history = sorted(load_history(), key=lambda r: r["started_at"], reverse=True)
        total = sum(r["duration_seconds"] for r in history)
        self.total_label.setText(f"Записей: {len(history)} · {format_duration(total)}")

        if not history:
            empty = QLabel("Пока нет сохранённых задач")
            self.tm.register(lambda: empty.setStyleSheet(
                f"color: {self.tm.rgba('text_secondary', 'text_secondary_alpha')}; font-size: 12px; padding: 20px;"))
            add_text_shadow(empty, blur=5, alpha=160, dy=1)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list_layout.addWidget(empty)
        else:
            for record in history:
                row = HistoryRow(self.tm, record)
                row.deleted.connect(self._on_delete)
                row.tags_updated.connect(self.refresh)
                row.manage_tags_requested.connect(self.manage_tags_requested.emit)
                self.list_layout.addWidget(row)

        self.list_layout.addStretch(1)

    def _on_delete(self, record_id):
        delete_record(record_id)
        self.refresh()

    def _on_clear_all(self):
        clear_all_records()
        self.refresh()


# ---------------------------------------------------------------------------
# Страница тегов
# ---------------------------------------------------------------------------

class TagRow(QFrame):
    deleted = Signal(str)

    def __init__(self, tm: ThemeManager, tag: dict):
        super().__init__()
        self.tag_id = tag["id"]
        tm.register(lambda: self.setStyleSheet(
            f"QFrame {{ background: transparent; border-bottom: 1px solid {tm.rgba('edge_highlight', None, alpha=25)}; }}"
            f"QFrame:hover {{ background: {tm.rgba('text_primary', None, alpha=16)}; }}"
        ))
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 8, 4, 8)
        row.setSpacing(10)

        chip = TagChip(tag)
        row.addWidget(chip)
        row.addStretch(1)

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(20, 20)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tm.register(lambda: del_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {tm.rgba('text_primary', None, alpha=120)};
                border: none;
                font-size: 12px;
            }}
            QPushButton:hover {{ color: {tm.hexcolor('danger')}; }}
        """))
        del_btn.clicked.connect(lambda: self.deleted.emit(self.tag_id))
        row.addWidget(del_btn)


class TagsPage(QWidget):
    tags_changed = Signal()

    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.tm = tm
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        create_row = QHBoxLayout()
        create_row.setSpacing(6)
        self.new_tag_input = QLineEdit()
        self.new_tag_input.setPlaceholderText("Новый тег…")
        tm.register(lambda: self.new_tag_input.setStyleSheet(build_glass_input_style(tm)))
        self.new_tag_input.returnPressed.connect(self._on_create)
        create_row.addWidget(self.new_tag_input, 1)

        self.create_btn = QPushButton("Добавить")
        self.create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.create_btn.setMinimumHeight(34)
        self.create_btn.setMinimumWidth(92)
        tm.register(lambda: self.create_btn.setStyleSheet(build_accent_style(tm, running=False)))
        self.create_btn.clicked.connect(self._on_create)
        create_row.addWidget(self.create_btn)
        layout.addLayout(create_row)

        self.hint_label = QLabel("Цвет тега назначается автоматически и не меняется")
        tm.register(lambda: self.hint_label.setStyleSheet(
            f"color: {tm.rgba('text_secondary', 'text_secondary_alpha')}; font-size: 10px;"))
        layout.addWidget(self.hint_label)

        self.scroll = SmoothScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().setStyleSheet("background: transparent;")

        self.list_container = QWidget()
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_container)

        layout.addWidget(self.scroll, 1)
        self.refresh()

    def _on_create(self):
        name = self.new_tag_input.text().strip()
        if not name:
            return
        existing_names = {t["name"].lower() for t in load_tags()}
        if name.lower() in existing_names:
            self.new_tag_input.clear()
            return
        create_tag(name)
        self.new_tag_input.clear()
        self.refresh()
        self.tags_changed.emit()

    def refresh(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        tags = load_tags()
        if not tags:
            empty = QLabel("Тегов пока нет")
            self.tm.register(lambda: empty.setStyleSheet(
                f"color: {self.tm.rgba('text_secondary', 'text_secondary_alpha')}; font-size: 12px; padding: 20px;"))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list_layout.addWidget(empty)
        else:
            for tag in tags:
                row = TagRow(self.tm, tag)
                row.deleted.connect(self._on_delete)
                self.list_layout.addWidget(row)

        self.list_layout.addStretch(1)

    def _on_delete(self, tag_id):
        delete_tag(tag_id)
        self.refresh()
        self.tags_changed.emit()


# ---------------------------------------------------------------------------
# Свёрнутый режим ("таблетка")
# ---------------------------------------------------------------------------

class CompactBubble(QWidget):
    expand_requested = Signal()
    quit_requested = Signal()

    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.tm = tm
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 8, 10)
        layout.setSpacing(8)

        self._running = False
        self.dot = QLabel("●")
        tm.register(self._restyle_dot)
        layout.addWidget(self.dot)

        self.text_label = QLabel("Task Timer")
        self.text_label.setStyleSheet("color: white; font-size: 12px; font-weight: 700;")
        add_text_shadow(self.text_label, blur=6, alpha=180, dy=1)
        layout.addWidget(self.text_label)
        layout.addStretch(1)

        self.expand_btn = QPushButton("⤢")
        self.expand_btn.setFixedSize(22, 22)
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tm.register(lambda: self.expand_btn.setStyleSheet(build_title_btn_style(tm)))
        self.expand_btn.clicked.connect(self.expand_requested.emit)
        layout.addWidget(self.expand_btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tm.register(lambda: self.close_btn.setStyleSheet(build_title_btn_style(tm)))
        self.close_btn.clicked.connect(self.quit_requested.emit)
        layout.addWidget(self.close_btn)

    def _restyle_dot(self):
        color = self.tm.hexcolor("accent_running_start") if self._running \
            else self.tm.rgba("text_primary", None, alpha=140)
        self.dot.setStyleSheet(f"color: {color}; font-size: 9px;")

    def set_running(self, running: bool):
        self._running = running
        self._restyle_dot()
        if not running:
            self.text_label.setText("Task Timer")

    def set_elapsed(self, text: str):
        self.text_label.setText(text)


# ---------------------------------------------------------------------------
# Главное окно
# ---------------------------------------------------------------------------

class MainWindow(LiquidGlassWindow):
    def __init__(self, tm: ThemeManager):
        super().__init__(tm)
        self.window_cfg = tm.window_cfg

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- полная страница ---
        self.full_page = QWidget()
        full_layout = QVBoxLayout(self.full_page)
        full_layout.setContentsMargins(0, 0, 0, 0)
        full_layout.setSpacing(0)

        title_bar = QHBoxLayout()
        title_bar.setContentsMargins(14, 10, 10, 0)

        app_title = QLabel("Task Timer")
        tm.register(lambda: app_title.setStyleSheet(
            f"color: {tm.rgba('text_primary', 'text_primary_alpha')}; font-size: 12px; font-weight: 700;"))
        add_text_shadow(app_title, blur=6, alpha=180, dy=1)
        title_bar.addWidget(app_title)
        title_bar.addStretch(1)

        self.btn_timer = QPushButton("⏱")
        self.btn_history = QPushButton("📜")
        self.btn_tags = QPushButton("🏷")
        for b in (self.btn_timer, self.btn_history, self.btn_tags):
            b.setCheckable(True)
            tm.register(lambda b=b: b.setStyleSheet(build_title_btn_style(tm)))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            title_bar.addWidget(b)
        self.btn_timer.setChecked(True)

        self.btn_min = QPushButton("—")
        tm.register(lambda: self.btn_min.setStyleSheet(build_title_btn_style(tm)))
        self.btn_min.clicked.connect(lambda: self._set_compact(True))
        title_bar.addWidget(self.btn_min)

        self.btn_close = QPushButton("✕")
        tm.register(lambda: self.btn_close.setStyleSheet(build_title_btn_style(tm)))
        self.btn_close.clicked.connect(self._quit)
        title_bar.addWidget(self.btn_close)

        full_layout.addLayout(title_bar)

        self.content_stack = QStackedWidget()
        self.timer_page = TimerPage(tm)
        self.history_page = HistoryPage(tm)
        self.tags_page = TagsPage(tm)
        self.content_stack.addWidget(self.timer_page)
        self.content_stack.addWidget(self.history_page)
        self.content_stack.addWidget(self.tags_page)
        full_layout.addWidget(self.content_stack, 1)

        self.btn_timer.clicked.connect(lambda: self._switch_page(0))
        self.btn_history.clicked.connect(lambda: self._switch_page(1))
        self.btn_tags.clicked.connect(lambda: self._switch_page(2))
        self.timer_page.record_saved.connect(self.history_page.refresh)
        self.tags_page.tags_changed.connect(self.history_page.refresh)
        self.history_page.manage_tags_requested.connect(lambda: self._switch_page(2))

        root.addWidget(self.full_page)

        # --- компактная "таблетка" ---
        self.compact_bubble = CompactBubble(tm)
        self.compact_bubble.expand_requested.connect(lambda: self._set_compact(False))
        self.compact_bubble.quit_requested.connect(self._quit)
        root.addWidget(self.compact_bubble)

        self.timer_page.elapsed_tick.connect(self.compact_bubble.set_elapsed)
        self.timer_page.started.connect(lambda: self.compact_bubble.set_running(True))
        self.timer_page.stopped.connect(lambda: self.compact_bubble.set_running(False))

        QShortcut(QKeySequence("Meta+C"), self, activated=self._quit)

        self._set_compact(True)

    def _switch_page(self, index):
        self.content_stack.setCurrentIndex(index)
        self.btn_timer.setChecked(index == 0)
        self.btn_history.setChecked(index == 1)
        self.btn_tags.setChecked(index == 2)
        if index == 1:
            self.history_page.refresh()
        elif index == 2:
            self.tags_page.refresh()

    def _set_compact(self, compact: bool):
        # Размер окна фиксирован в обоих режимах (не тянется мышкой) —
        # значения берутся из конфига (window.*), так их тоже можно
        # настраивать централизованно.
        cfg = self.window_cfg
        if compact:
            self.full_page.hide()
            self.compact_bubble.show()
            self.setFixedSize(cfg["compact_width"], cfg["compact_height"])
        else:
            self.compact_bubble.hide()
            self.full_page.show()
            self.setFixedSize(cfg["full_width"], cfg["full_height"])

    def closeEvent(self, event):
        # На случай, если оконный менеджер (например, Win+C в Hyprland)
        # закрывает окно через протокольный запрос закрытия (xdg-shell
        # "close"), а не через POSIX-сигнал — обрабатываем это так же
        # аккуратно, как и обычный сигнал.
        self._quit()
        event.accept()

    def _quit(self):
        self.timer_page.stop_if_running_for_quit()
        QApplication.instance().quit()


# ---------------------------------------------------------------------------
# PID-файл + надёжное завершение по сигналу (Win+C и т.п. из оконного
# менеджера, а также обычный SIGINT/SIGTERM из терминала).
#
# Раньше сигнал обрабатывался чистым signal.signal(...), но Qt держит цикл
# событий в нативном (не-Python) коде, и интерпретатор мог "увидеть" сигнал
# только на следующем тике таймера — из-за этого Win+C иногда как будто не
# срабатывал и приходилось жать Ctrl+C ещё раз прямо в терминале.
#
# Правильное решение — self-pipe trick через signal.set_wakeup_fd():
# при получении сигнала ОС гарантированно и сразу пишет байт в сокет,
# на который подписан QSocketNotifier, и это сразу же будит цикл событий
# Qt, не дожидаясь никаких таймеров.
# ---------------------------------------------------------------------------

_window_ref = {"win": None}
_wakeup_read_sock = None
_wakeup_write_sock = None
_wakeup_notifier = None
_quit_requested = False
_quit_in_progress = False


def _write_pid():
    ensure_data_file()
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def _remove_pid():
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _graceful_quit():
    win = _window_ref.get("win")
    app = QApplication.instance()
    if win is not None:
        win._quit()
    elif app is not None:
        app.quit()
    # страховка: если что-то зависло при сохранении/закрытии, всё равно
    # завершаем процесс через полторы секунды, чтобы не приходилось
    # давить Ctrl+C руками второй раз.
    QTimer.singleShot(1500, lambda: os._exit(0))


def _install_signal_handling(app: QApplication):
    global _wakeup_read_sock, _wakeup_write_sock, _wakeup_notifier

    _wakeup_read_sock, _wakeup_write_sock = socket.socketpair()
    _wakeup_read_sock.setblocking(False)
    _wakeup_write_sock.setblocking(False)
    signal.set_wakeup_fd(_wakeup_write_sock.fileno())

    def _on_wakeup(_fd):
        try:
            _wakeup_read_sock.recv(4096)
        except (BlockingIOError, OSError):
            pass
        _graceful_quit()

    _wakeup_notifier = QSocketNotifier(
        _wakeup_read_sock.fileno(), QSocketNotifier.Type.Read, app
    )
    _wakeup_notifier.activated.connect(_on_wakeup)

    # Python-обработчики нужны хотя бы как заглушки: set_wakeup_fd пишет
    # байт в сокет только для тех сигналов, для которых явно назначен
    # обработчик (иначе для SIG_DFL система пишет байт не гарантированно
    # и вдобавок сразу же убивает процесс по умолчанию).
    signal.signal(signal.SIGTERM, lambda *_: None)
    signal.signal(signal.SIGINT, lambda *_: None)


def main():
    app = QApplication(sys.argv)

    _write_pid()
    app.aboutToQuit.connect(_remove_pid)

    theme_manager = ThemeManager()

    window = MainWindow(theme_manager)
    _window_ref["win"] = window
    window.show()

    _install_signal_handling(app)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()