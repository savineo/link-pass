# -*- coding: utf-8 -*-
"""
LinkPass — менеджер паролей
Автор: Савин Евгений Олегович
Сайт: www.linkpass.ru
"""
import sys, os, json, shutil, base64, hashlib, secrets, zipfile, sqlite3, csv, math, io, traceback, subprocess, ctypes, tempfile, signal
APP_TITLE = "LinkPass — менеджер паролей"
CURRENT_VERSION = 1
SHARE_TEMP_TTL_SEC = 60
from datetime import datetime, timedelta
import importlib
import re
from urllib.parse import quote
MAX_CARDS = 300
CARD_FIELDS_LIMIT = 12
SEARCH_DEBOUNCE_MS = 250
TREE_RENDER_DELAY_MS = 60
DEFAULT_EXPAND_DEPTH = -1
ARGON2_TIME_COST = 4
ARGON2_MEMORY_COST = 128 * 1024
ARGON2_PARALLELISM = 2
SALT_LEN = 32
KDF_DEFAULTS = {
    "t": ARGON2_TIME_COST,
    "m": ARGON2_MEMORY_COST,
    "p": ARGON2_PARALLELISM,
}
def _mix_master_with_pepper(master: str) -> str:
    try:
        pep_b64 = os.environ.get("LINKPASS_PEPPER_B64", "").strip()
        if pep_b64:
            pep = base64.b64decode(pep_b64)
            h = hashlib.sha256(pep).hexdigest()
            return f"{master}\u2063{h}"
    except Exception:
        pass
    return master
REQUIRED_PKGS = [
    ("PySide6",       "PySide6",      "GUI"),
    ("cryptography",  "cryptography", "Шифрование (Fernet)"),
    ("pandas",        "pandas",       "Экспорт/импорт таблиц"),
    ("openpyxl",      "openpyxl",     ".xlsx для pandas"),
    ("argon2-cffi",   "argon2",       "Современный KDF Argon2id"),
]
OPTIONAL_PKGS = [
    ("pillow",  "PIL",     "Вложения-изображения (опц.)"),
    ("pyotp",   "pyotp",   "TOTP (опц.)"),
    ("qrcode",  "qrcode",  "QR-коды (опц.)"),
    ("pysqlite3-binary", "pysqlite3", "FTS5 там, где SQLite урезан (опц.)"),
]
if sys.version_info >= (3, 13):
    OPTIONAL_PKGS = [t for t in OPTIONAL_PKGS if t[0] != "pysqlite3-binary"]
def ensure_dependencies():
    missing = []
    for pip_name, import_name, _ in REQUIRED_PKGS:
        try:
            importlib.import_module(import_name)
        except Exception:
            missing.append(pip_name)
    if missing:
        print("\n[LinkPass] Не найдены обязательные пакеты:\n  " + ", ".join(missing))
        print("Установите:\n  pip install " + " ".join(missing))
        sys.exit(1)
    opt_missing = []
    for pip_name, import_name, _ in OPTIONAL_PKGS:
        if pip_name == "pysqlite3-binary" and sys.version_info >= (3, 13):
            continue
        try:
            importlib.import_module(import_name)
        except Exception:
            opt_missing.append(pip_name)
    if opt_missing:
        print("[LinkPass] Опциональные пакеты не найдены: " + ", ".join(opt_missing))
        print("  По желанию: pip install " + " ".join(opt_missing))
ensure_dependencies()
import pandas as pd
from PySide6 import QtCore
from PySide6.QtCore import Qt, QTimer, QObject, QEvent, Signal, QCoreApplication, QUrl, QLocale, QUrlQuery, QSize
from PySide6.QtGui import QAction, QIcon, QDrag, QDesktopServices, QColor, QPixmap, QCursor, QImageReader, QPainter, QPen, QFont
from PySide6.QtWidgets import (
    QApplication, QWidget, QTreeWidget, QTreeWidgetItem, QHBoxLayout, QVBoxLayout,
    QPushButton, QMenu, QInputDialog, QMessageBox, QLabel, QLineEdit, QScrollArea,
    QFileDialog, QMainWindow, QFrame, QGridLayout, QSplitter, QDialog, QComboBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QListWidget, QTextEdit,
    QSystemTrayIcon, QTabWidget, QDateTimeEdit, QColorDialog, QSpinBox, QDialogButtonBox,
    QCheckBox, QPlainTextEdit, QStackedWidget, QProgressDialog, QLayout,
    QToolButton, QProgressBar, QWidgetItem, QSizePolicy, QHeaderView
)
def tr(s: str, *args, **kwargs):
    try:   
        return s.format(*args, **kwargs)
    except Exception:
        return s
def translate_widget_tree(*_args, **_kwargs):
    return
from typing import Optional, Callable, Any, List, Tuple
try:
    import pyotp
    HAS_TOTP = True
except Exception:
    HAS_TOTP = False
try:
    from argon2.low_level import hash_secret as _argon_hash_secret, Type as _Argon2Type
except Exception:
    _argon_hash_secret = None
    _Argon2Type = None
HAS_ARGON2: bool = _argon_hash_secret is not None and _Argon2Type is not None
from cryptography.fernet import Fernet, InvalidToken
class WrongMasterPasswordError(Exception):
    pass
def brand_icon(name: str) -> QIcon:
    try:
        p = resource_path(f"icons/{name}.png")
        return QIcon(p) if os.path.exists(p) else QIcon()
    except Exception:
        return QIcon()
from typing import Optional
def resource_path(relative_path: str) -> str:
    base: Optional[str] = getattr(sys, "_MEIPASS", None)
    if isinstance(base, str) and base:
        return os.path.join(base, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)
def get_data_dir():
    if sys.platform.startswith('win'):
        return os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'LinkPass')
    elif sys.platform.startswith('darwin'):
        return os.path.expanduser('~/Library/Application Support/LinkPass')
    else:
        return os.path.expanduser('~/.local/share/LinkPass')
DATA_DIR  = get_data_dir()
os.makedirs(DATA_DIR, exist_ok=True)
ICON_PATH = resource_path('favicon.ico')
TREE_FILE   = os.path.join(DATA_DIR, "tree.json")
BLOCKS_FILE = os.path.join(DATA_DIR, "blocks.json")
TRASH_FILE  = os.path.join(DATA_DIR, "trash.json")
META_FILE   = os.path.join(DATA_DIR, "meta.json")
MASTER_FILE = os.path.join(DATA_DIR, "auth.json")
SNAP_DIR    = os.path.join(DATA_DIR, "snapshots")
INDEX_DB    = os.path.join(DATA_DIR, "index.db")
ATTACH_DIR  = os.path.join(DATA_DIR, "attachments")
for d in (SNAP_DIR, ATTACH_DIR):
    os.makedirs(d, exist_ok=True)
PROGRAM_INFO = (
    "LinkPass — менеджер паролей\n"
    "Версия: 1.0.0\n"
    "\n"
    "Copyright (c) 2025 Савин Евгений Олегович\n"
    "Сайт программы: https://linkpass.ru\n"
    "Лицензия: MIT License\n"
     "\n"
    "💖 Поддержать проект: https://yoomoney.ru/to/410011663886937\n"
    
)
MANUAL_TEXT = """\
LinkPass — Руководство пользователя

Оглавление
1. Что такое LinkPass и как он устроен
2. Первый запуск и мастер‑пароль
3. Вход в хранилище и автозавершение сеанса
4. Обзор интерфейса
5. Разделы и подразделы (дерево слева)
6. Блоки (карточки справа)
7. Поля: добавление, редактирование, копирование, ссылки
8. Вложения (файлы): добавление, просмотр, расшифровка, шаринг
9. Заметки по блоку
10. Поиск и «Умные папки»
11. Перемещение, сортировка и удаление
12. Защита разделов паролем, разблокировка цепочки
13. Импорт данных (XLSX/CSV/JSON/TXT)
14. Экспорт данных: обычный и шифрованный (LPX1)
15. Бэкап и восстановление (LPX1/LPBK/LPEX/ZIP)
16. Смена мастер‑пароля
17. Параметры KDF (Argon2id): производительность и миграция
18. Поделиться данными: Telegram / WhatsApp / Email
19. Горячие клавиши
20. Папка данных и перенос на другой компьютер
21. Восстановление при утрате мастер‑пароля — что реально можно сделать
22. Советы по безопасности
Поддержать проект LinkPass

────────────────────────────────────────────────────────
1) Что такое LinkPass и как он устроен
────────────────────────────────────────────────────────
LinkPass — менеджер персональных данных/паролей. Все значения полей и вложения шифруются ключом,
который защищён вашим мастер‑паролем. Дерево слева — это структура разделов. Справа — «канбан»
из карточек (блоков). Внутри каждого блока — поля (пары «Название → Значение»), заметки и вкладка
«Вложения».

Данные хранятся только локально в «папке данных» (см. раздел 20), и/или в ваших экспорт/бэкап‑файлах,
если вы их создаёте.

────────────────────────────────────────────────────────
2) Первый запуск и мастер‑пароль
────────────────────────────────────────────────────────
• При первом запуске появится диалог «Мастер‑пароль». Задайте надёжный пароль (рекомендовано ≥ 10
  символов, с цифрами и символами). Индикатор подсказывает силу пароля.
• Этот пароль будет нужен при каждом входе и при «чувствительных» операциях (например, смена KDF).
• Мастер‑пароль нельзя восстановить алгоритмически (см. раздел 21). Обязательно сделайте бэкап.

────────────────────────────────────────────────────────
3) Вход в хранилище и автозавершение сеанса
────────────────────────────────────────────────────────
• При каждом старте введите мастер‑пароль.
• Сеанс можно принудительно заблокировать: значок в системном трее → «Заблокировать».
• Автоблокировка по неактивности возможна (если включена в сборке): при срабатывании скрываются
  данные и очищается буфер обмена. Для продолжения работы разблокируйте разделы (см. 12).

────────────────────────────────────────────────────────
4) Обзор интерфейса
────────────────────────────────────────────────────────
Верхняя строка — поиск и кнопки:
  • Поле «Поиск…» и селектор «Блоки/Поля».
  • Кнопка «✖» — очистить запрос.
  • «Умные папки», «Шаблоны», «Пресеты».
  • «📎» — фильтр «только блоки с вложениями».
  • «+ Блок» — создать блок.
  • «Открыть данные» — показать/скрыть расшифрованные значения на карточках.

Левая панель — дерево разделов:
  • Кнопки «+ Раздел», «+ Подраздел», «Удалить».
  • Контекстное меню (ПКМ) по разделу: переименование, цвет, установка/снятие пароля, экспорт раздела,
    перемещение, иконка цвета у пункта дерева.

Правая панель — карточки (блоки):
  • Заголовок (Название), бейдж «Категория» (цвет — из раздела), бейдж «📎 N» при наличии вложений.
  • Поля (до N видимых строк на карточке), кнопки «↗» (перейти по URL/mailto), «🗐» (скопировать).
  • Кнопки внизу: «🔍 Открыть», «↔️ Переместить», «📁 Вложения», «📝 Заметки», «🗑️ Удалить».
  • «🔗» — меню «Поделиться» (см. 18), «QR» — показать QR‑код с данными.

Перетаскивание:
  • Перетащите карточку мышью на нужный раздел слева — блок переместится.

────────────────────────────────────────────────────────
5) Разделы и подразделы (дерево слева)
────────────────────────────────────────────────────────
Создание:
  • «+ Раздел» — новый раздел верхнего уровня.
  • Выделите раздел → «+ Подраздел» — вложенный раздел.

Переименование/цвет:
  • ПКМ по разделу → «Переименовать» или «Выбрать цвет». Цвет влияет на бейдж категории блока.

Пароль на раздел:
  • ПКМ по разделу → «🔒 Установить пароль» — задайте пароль. Далее содержимое этого раздела и всех
    его дочерних будет скрыто до ввода пароля (см. 12).
  • Чтобы снять — «🔓 Снять пароль» (нужен текущий пароль раздела).

Экспорт/перемещение раздела: ПКМ → соответствующий пункт.

Удаление раздела:
  • «Удалить» — все блоки из раздела и подразделов перемещаются в «Корзину» (см. 11).

Сортировка разделов:
  • Alt+↑ / Alt+↓ — переместить раздел выше/ниже среди соседей.

────────────────────────────────────────────────────────
6) Блоки (карточки справа)
────────────────────────────────────────────────────────
Создание:
  • Выберите раздел → «+ Блок» → введите название → опционально выберите «Шаблон» (набор полей).
  • Категория блока = имя конечного раздела (последний компонент пути).

Открытие/редактирование:
  • Кнопка «🔍 Открыть» на карточке — детальный редактор блока (поля, вложения, заметки, JSON‑вид).

Перемещение:
  • «↔️ Переместить» (диалог выбора пути) или перетаскиванием карточки на раздел слева.

Удаление:
  • «🗑️ Удалить» — блок уходит в «Корзину», можно восстановить (см. 11).

────────────────────────────────────────────────────────
7) Поля: добавление, редактирование, копирование, ссылки
────────────────────────────────────────────────────────
• В детальном редакторе нажмите «Добавить поле», введите название — появится новая пара.
• Значения полей всегда хранятся в зашифрованном виде. Отображение «Открыть данные» включает
  расшифровку на карточках.
• Кнопка «🗐» рядом со значением — копирует расшифрованный текст в буфер обмена (через 30 секунд
  буфер очищается автоматически).
• Если значение похоже на URL или email, появится «↗» — откроет ссылку/почтовый клиент.
• Кнопка «Копировать все поля в буфер» собирает заголовок, все поля и заметки в текст.

────────────────────────────────────────────────────────
8) Вложения (файлы): добавление, просмотр, расшифровка, шаринг
────────────────────────────────────────────────────────
• Вкладка «Вложения» в детальном редакторе:
  – «Добавить файл» — шифруется и сохраняется внутри папки данных.
  – «Сохранить как…» — расшифровать и сохранить файл на диск.
  – «Удалить вложение» — удаляет файл из хранилища.
  – Предпросмотр: изображения и мелкие тексты отображаются; большие/неподдерживаемые — без предпросмотра.
• «Поделиться» (меню: Telegram, WhatsApp, Email) готовит временную расшифрованную копию в отдельной
  папке. Временная папка удаляется автоматически через 60 секунд (по умолчанию).

────────────────────────────────────────────────────────
9) Заметки по блоку
────────────────────────────────────────────────────────
• Вкладка «Заметки» — свободный шифруемый текст, хранится как единое поле, индексируется поиском.

────────────────────────────────────────────────────────
10) Поиск и «Умные папки»
────────────────────────────────────────────────────────
Поиск:
  • Введите запрос в поле «Поиск…».
  • Режим «Блоки» — ищет по заголовку/категории/содержимому (индексу) блока.
  • Режим «Поля» — ищет по именам полей и их значениям (включая заметки).

Умные папки:
  • «Настройки → 🧠 Умные папки»: создайте правило (имя, запрос, область — весь корень или конкретный раздел,
    режим «Поля/Блоки»).
  • В дереве слева появится ветка «🧠 Умные папки», внутри — виртуальные подборки.

Примечания:
  • При большом числе карточек показываются первые 300, остальные скрыты — уточните запрос.

────────────────────────────────────────────────────────
11) Перемещение, сортировка и удаление
────────────────────────────────────────────────────────
• Перемещение блоков: перетаскиванием на раздел слева или «↔️ Переместить».
• Удаление блока/раздела: перенос в «Корзину» (меню «🗑️ Корзина»). В корзине:
  – «Восстановить выбранный» — возвращает блок в исходный раздел.
  – «Открыть (только просмотр)» — детальный просмотр.
  – «Очистить корзину» — удаляет безвозвратно (потребуется подтвердить мастер‑паролем).

────────────────────────────────────────────────────────
12) Защита разделов паролем, разблокировка цепочки
────────────────────────────────────────────────────────
• ПКМ по разделу → «🔒 Установить пароль». Все дочерние разделы наследуют защиту.
• Для просмотра данных блоков внутри защищённой ветки:
  – Нажмите «Открыть данные» на панели.
  – В заблокированном разделе на карточке будет «🔓» — нажмите и введите пароль раздела.
• «🔓 Снять пароль» — только после ввода текущего пароля раздела.

────────────────────────────────────────────────────────
13) Импорт данных (XLSX/CSV/JSON/TXT)
────────────────────────────────────────────────────────
Меню: «Файл → 📥 Импорт (обычный)».
Поддерживаемые форматы и правила:
• Excel (.xlsx): первая строка — заголовки. «Название блока», «Раздел/Подраздел/Подподраздел» или «Путь».
• CSV (.csv): разделитель определяется автоматически (запятая, точка с запятой, таб, вертикальная черта).
• JSON:
  a) { "A/B/C": [ {block}, ... ], ... }
  b) [ {"Раздел":"…","Подраздел":"…","Название блока":"…", ...}, ... ]
  c) [ {"Путь":"A/B/C","Название блока":"…", ...}, ... ]
• TXT: первая строка — заголовки, далее строки в формате «|»-разделённых столбцов.

Маппинг:
• Если есть «Путь», используется он (должен содержать «/»).
• Иначе используются «Раздел/Подраздел/Подподраздел».
• Остальные столбцы импортируются как поля блока. Пустые значения игнорируются.

────────────────────────────────────────────────────────
14) Экспорт данных: обычный и шифрованный (LPX1)
────────────────────────────────────────────────────────
Обычный экспорт: «Файл → 📤 Экспорт (обычный)» → выберите формат (XLSX/CSV/JSON/TXT/HTML).  
Шифрованный экспорт (LPX1): «Файл → 🔐 Экспорт (шифр.)» → задайте пароль. Получится зашифрованный контейнер.
Экспорт раздела: ПКМ по разделу → «📤 Экспортировать раздел…».

Важно:
• Шифрованный экспорт (LPX1) — отдельный файл с паролем, пригоден для безопасной передачи.
• «Импорт (шифр.)» предназначен для совместимых экспортов той же установки. Для «универсальных»
  бэкапов используйте раздел 15.

────────────────────────────────────────────────────────
15) Бэкап и восстановление (LPX1/LPBK/LPEX/ZIP)
────────────────────────────────────────────────────────
Бэкап (зашифрованный):
• «Файл → 🗄️ Бэкап → 🗄️ Создать бэкап (шифр.)» → задайте пароль → получите .lpx (LPX1).
• Внутри — zip‑контейнер с полным снимком папки данных (включая вложения).

Восстановление:
• «Файл → 🗄️ Бэкап → ♻️ Восстановить из бэкапа» → выберите .lpx/.lpbk/.lpex/.zip.
• Если файл зашифрован — введите пароль бэкапа.
• Программа распакует содержимое во временную папку и предложит перезаписать текущие данные (перед этим
  создаётся резервная копия текущего состояния).
• После восстановления, если «auth.json» из бэкапа отличается, программа может попросить мастер‑пароль
  того бэкапа (то есть тот, который действовал на момент создания бэкапа). Введите его.
• После успешного входа можете сменить мастер‑пароль на новый (см. 16).

────────────────────────────────────────────────────────
16) Смена мастер‑пароля
────────────────────────────────────────────────────────
Меню: «Настройки → 🗝️ Смена мастер‑пароля».
• Введите новый пароль и подтвердите.
• Все поля и вложения будут пере‑зашифрованы под новым ключом.
• На время операции может потребоваться подождать (особенно при больших данных).

────────────────────────────────────────────────────────
17) Параметры KDF (Argon2id): производительность и миграция
────────────────────────────────────────────────────────
• Ключ шифрования получается из мастер‑пароля через KDF Argon2id (по умолчанию с параметрами,
  указанными разработчиком сборки).
• При старте программа может предложить «Обновить параметры KDF…» до рекомендуемых. Согласитесь —
  данные будут пере‑зашифрованы с новым KDF.
• Рекомендованные параметры для баланса безопасности/скорости: t=4, m=128MiB, p=2.
• Смена KDF выполняется безопасно через «миграцию»: старые данные расшифровываются и шифруются заново.

────────────────────────────────────────────────────────
18) Поделиться данными: Telegram / WhatsApp / Email
────────────────────────────────────────────────────────
Текст:
• На карточке блока — «🔗» → выберите канал (Telegram/WhatsApp/Email). Формируется текст с полями.
• Если получательский клиент не открывается, текст копируется в буфер обмена (вставьте вручную).

Файлы:
• В «Вложения» → иконка «поделиться» → выбрать Telegram/WhatsApp/Email.
• Для отправки создаётся временная папка с расшифрованными файлами (удаляется автоматически через
  ~60 секунд). Откроется проводник/чаты — перетащите файлы.

────────────────────────────────────────────────────────
19) Горячие клавиши
────────────────────────────────────────────────────────
• Alt+↑ / Alt+↓ — поднять/опустить раздел в дереве.
• Ctrl+R — создать раздел.
• Ctrl+P — создать подраздел.
• Ctrl+B — создать блок.
• Ctrl+Q — выход из программы.
(Другие шорткаты могут быть добавлены вашей сборкой.)

────────────────────────────────────────────────────────
20) Папка данных и перенос на другой компьютер
────────────────────────────────────────────────────────
Папка данных (создаётся автоматически):
• Windows: %APPDATA%\\LinkPass
• macOS:  ~/Library/Application Support/LinkPass
• Linux:  ~/.local/share/LinkPass

Внутри находятся: tree.json, blocks.json, trash.json, meta.json, auth.json, index.db, папка attachments/
(всё, кроме auth.json и index.db, хранится в зашифрованном виде).
Перенос:
• Закройте программу на исходном компьютере.
• Скопируйте всю папку данных целиком на новый компьютер в соответствующее место.
• Установите/запустите LinkPass и войдите своим мастер‑паролем.
Важно:
• Если при работе использовалась «pepper» (переменная окружения LINKPASS_PEPPER_B64), на новом
  ПК её нужно установить с тем же значением — иначе расшифровка будет невозможна.

────────────────────────────────────────────────────────
21) Восстановление при утрате мастер‑пароля — что реально можно сделать
────────────────────────────────────────────────────────
Коротко: мастер‑пароль не хранится и не восстанавливается. Без него расшифровать текущие данные нельзя.
Возможные сценарии:
A) Есть зашифрованный бэкап (.lpx/.lpbk/.lpex) и известен пароль бэкапа:
   1. «Файл → Бэкап → ♻️ Восстановить из бэкапа».
   2. Введите пароль бэкапа.
   3. Если восстановленный бэкап содержит другой auth.json — программа запросит мастер‑пароль,
      который действовал в момент создания бэкапа. Введите его.
   4. После входа сразу смените мастер‑пароль на новый (см. 16).

B) Есть полная копия папки данных (Time Machine, файловый бэкап и т. п.) от момента, когда пароль ещё помните:
   1. Закройте программу. Сделайте резервную копию текущей папки данных.
   2. Верните из бэкапа всю папку данных LinkPass целиком.
   3. Запустите LinkPass и войдите старым мастер‑паролем.
   4. Смените мастер‑пароль (см. 16).

C) Нет бэкапа и мастер‑пароль забыт:
   • Доступ к текущему хранилищу восстановить нельзя (так задумана безопасность).
   • Создайте новое хранилище:
     – Закройте программу. Переименуйте текущую папку данных в LinkPass_backup_YYYYMMDD.
     – Запустите LinkPass — будет предложено задать новый мастер‑пароль.
     – Если у вас есть любые прежние EXCEL/CSV/JSON/TXT‑экспорты в открытом виде — импортируйте их (см. 13).

Рекомендации:
• Сразу настройте регулярные бэкапы (см. ниже), храните пароли бэкапов отдельно от мастер‑пароля.
• Храните значения LINKPASS_PEPPER_B64 (если использовали) в надёжном месте.

────────────────────────────────────────────────────────
22) Советы по безопасности
────────────────────────────────────────────────────────
• Используйте длинный мастер‑пароль и не используйте его нигде больше.
• Включите регулярный зашифрованный бэкап:
  – «Настройки → ⏱️ Экспорт по расписанию» — задайте папку и пароль для LPX1‑контейнера.
• Защищайте чувствительные ветки дерева отдельными паролями разделов.
• Храните бэкапы и «pepper» отдельно от рабочего ПК.
• При шаринге файлов помните, что временная расшифрованная копия существует короткое время в
  системной временной папке.



  ────────────────────────────────────────────────────────
💖 Поддержать проект LinkPass
────────────────────────────────────────────────────────
Если вам полезен LinkPass, вы можете поддержать разработку добровольным пожертвованием.

- YooMoney: **https://yoomoney.ru/to/410011663886937**
- Быстрая кнопка: [![Donate](https://img.shields.io/badge/Donate-YooMoney-6c3adb?logo=yoomoney)](https://yoomoney.ru/to/410011663886937)

Спасибо! Любая поддержка помогает ускорять развитие и уделять больше внимания качеству и документации.
"""

LICENSE_TEXT = """\
Правовые сведения
• Программа распространяется по лицензии MIT. Полный текст (на английском) ниже без каких‑либо изменений.
• Дополнительное уведомление (НЕ часть лицензии MIT, а разъяснение для пользователя):
  – ПО предоставляется «как есть», без каких‑либо обещаний пригодности, работоспособности, соответствия целям или безопасности.
  – Автор/правообладатель не оказывает сопровождение или поддержку, не несёт ответственности за прямой/косвенный/случайный/косвенно‑вытекающий ущерб, потерю данных, перебои работы, а также за последствия использования в критически важных системах (медицина, транспорт, энергосистемы и т. п.).
  – Используйте только на свой риск. Ответственность автора полностью исключена.


────────────────────────────────────────────────────────
MIT License (canonical English text)
────────────────────────────────────────────────────────
MIT License

Copyright (c) 2025 Savin Evgenii

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

────────────────────────────────────────────────────────
Third‑party notices (licenses/links)
────────────────────────────────────────────────────────
• PySide6 — Qt for Python (LGPLv3): https://doc.qt.io/qtforpython/
• cryptography (Apache 2.0): https://cryptography.io/
• pandas (BSD‑3‑Clause): https://pandas.pydata.org/
• openpyxl (MIT): https://openpyxl.readthedocs.io/
• argon2‑cffi (MIT): https://github.com/hynek/argon2-cffi
• Pillow / PIL (HPND/PIL License): https://python-pillow.org/
• pyotp (MIT): https://pyauth.github.io/pyotp/
• qrcode (BSD): https://github.com/lincolnloop/python-qrcode

Примечание: тексты сторонних лицензий доступны по приведённым ссылкам. При распространении сборок,
соблюдайте условия соответствующих лицензий.
"""
def audit_write(action: str, details: dict):
    return
def install_crash_handler():
    def _hook(exctype, exc, tb):
        if issubclass(exctype, (KeyboardInterrupt, SystemExit)):
            try:
                app = QCoreApplication.instance()
                if app is not None:
                    QCoreApplication.quit()
            except Exception:
                pass
            return
        msg = "".join(traceback.format_exception(exctype, exc, tb))
        try:
            with open(os.path.join(DATA_DIR, "crash.log"), "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {msg}\n\n")
        except Exception:
            pass
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance()
        except Exception:
            app = None
        if app is not None:
            try:
                QMessageBox.critical(
                    None, "Критическая ошибка",
                    "Возникла непредвиденная ошибка. Файл crash.log сохранён в папке данных.\n\n"
                    + (msg[:2000])
                )
            except Exception:
        
                try:
                    print("[LinkPass] FATAL:\n" + msg, file=sys.stderr)
                except Exception:
                    pass
        else:
            try:
                print("[LinkPass] FATAL:\n" + msg, file=sys.stderr)
            except Exception:
                pass
    sys.excepthook = _hook
def install_sigint_quit():
    try:
        signal.signal(signal.SIGINT, lambda *_: QCoreApplication.quit())
    except Exception:
        pass
install_crash_handler()
install_sigint_quit()
def rand_bytes(n: int) -> bytes:
    return secrets.token_bytes(n)
def pbkdf2_key(master: str, salt: bytes, length=32, iterations=300_000) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", master.encode("utf-8"), salt, iterations, dklen=length)
def argon2id_key(master: str, salt: bytes, length: int = 32,
                 t: int = ARGON2_TIME_COST, m: int = ARGON2_MEMORY_COST, p: int = ARGON2_PARALLELISM) -> bytes:
    if not HAS_ARGON2 or _argon_hash_secret is None or _Argon2Type is None:
        raise RuntimeError("Argon2 is not available")
    h = _argon_hash_secret(
        _mix_master_with_pepper(master).encode("utf-8"),
        salt,
        time_cost=int(t),
        memory_cost=int(m),
        parallelism=int(p),
        hash_len=length,
        type=_Argon2Type.ID
    )
    return hashlib.sha256(h).digest()[:length]
def derive_key(master: str, key_salt: bytes,
               prefer_argon: bool = True,
               params: dict | None = None) -> bytes:
    if prefer_argon and HAS_ARGON2:
        p = params or KDF_DEFAULTS
        return argon2id_key(
            master, key_salt, length=32,
            t=int(p.get("t", ARGON2_TIME_COST)),
            m=int(p.get("m", ARGON2_MEMORY_COST)),
            p=int(p.get("p", ARGON2_PARALLELISM)),
        )
    m = _mix_master_with_pepper(master)
    return pbkdf2_key(m, key_salt, length=32)
def make_fernet(master: str, key_salt: bytes,
                *, kdf_name: str = "argon2id",
                params: dict | None = None) -> Fernet:
    key = derive_key(master, key_salt,
                     prefer_argon=(kdf_name == "argon2id"),
                     params=params)
    return Fernet(base64.urlsafe_b64encode(key))
def hash_for_auth(master: str, auth_salt: bytes,
                  prefer_argon: bool = True,
                  params: dict | None = None) -> str:
    if prefer_argon and HAS_ARGON2:
        p = params or KDF_DEFAULTS
        raw = argon2id_key(
            master, auth_salt, length=32,
            t=int(p.get("t", ARGON2_TIME_COST)),
            m=int(p.get("m", ARGON2_MEMORY_COST)),
            p=int(p.get("p", ARGON2_PARALLELISM)),
        )
    else:
        m = _mix_master_with_pepper(master)
        raw = pbkdf2_key(m, auth_salt, length=32)
    return base64.b64encode(raw).decode("utf-8")
def write_auth_file(key_salt: bytes, auth_salt: bytes, verifier_b64: str,
                    kdf_name: str | None = None,
                    kdf_params: dict | None = None):
    rec = {
        "key_salt": base64.b64encode(key_salt).decode("utf-8"),
        "auth_salt": base64.b64encode(auth_salt).decode("utf-8"),
        "verifier": verifier_b64,
        "kdf": kdf_name or ("argon2id" if HAS_ARGON2 else "pbkdf2"),
        "kdf_params": kdf_params or KDF_DEFAULTS,
        "ver": CURRENT_VERSION,
    }
    atomic_write_json(MASTER_FILE, rec)
def is_encrypted(val: str, fernet: Fernet) -> bool:
    if not isinstance(val, str): return False
    try:
        fernet.decrypt(val.encode("utf-8"))
        return True
    except Exception:
        return False
def encrypt_value(val: str, fernet: Fernet) -> str:
    if val is None: val = ""
    if not isinstance(val, str): val = str(val)
    if is_encrypted(val, fernet): return val
    return fernet.encrypt(val.encode("utf-8")).decode("utf-8")
def decrypt_value(val: str, fernet: Fernet) -> str:
    if val is None: return ""
    if not isinstance(val, str): val = str(val)
    try:
        return fernet.decrypt(val.encode("utf-8")).decode("utf-8")
    except Exception:
        return val
def mask_text(_): return "●" * 8
def atomic_write_json(path: str, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
SECURE_JSON_PREFIX = b"LPJS1"
def secure_write_json(path: str, obj, fernet: Fernet) -> None:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
    enc = fernet.encrypt(raw)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(SECURE_JSON_PREFIX + enc)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
def secure_read_json(path: str, fernet: Fernet, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data.startswith(SECURE_JSON_PREFIX):
            enc = data[len(SECURE_JSON_PREFIX):]
            try:
                raw = fernet.decrypt(enc)
            except InvalidToken as e:
                raise WrongMasterPasswordError("invalid master password") from e
            return json.loads(raw.decode("utf-8"))
        try:
            obj = json.loads(data.decode("utf-8"))
        except Exception:
            with open(path, "r", encoding="utf-8") as fr:
                obj = json.load(fr)
        try:
            secure_write_json(path, obj, fernet)
        except Exception:
            pass
        return obj
    except WrongMasterPasswordError:
        raise
    except InvalidToken as e:
        raise WrongMasterPasswordError("invalid master password") from e
    except Exception:
        return default
def snapshot_now(prefix="blocks"):
    os.makedirs(SNAP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    src = BLOCKS_FILE if prefix=="blocks" else TREE_FILE
    dst = os.path.join(SNAP_DIR, f"{prefix}.{ts}.json")
    try:
        if os.path.exists(src): shutil.copy2(src, dst)
    except Exception:
        pass
DOMAIN_LIKE_RE = re.compile(
    r"^(localhost|([a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})(:\d{1,5})?(?:[/?#].*)?$",
    re.IGNORECASE
)
def is_url(s: str) -> bool:
    if not s:
        return False
    s = s.strip()
    sl = s.lower()
    if sl.startswith("http://") or sl.startswith("https://") or "://" in sl:
        return True
    if " " in s:
        return False
    return DOMAIN_LIKE_RE.match(s) is not None
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
def is_email_addr(s: str) -> bool:
    if not s: return False
    s = s.strip()
    if " " in s: return False
    return EMAIL_RE.fullmatch(s) is not None
def to_qurl_from_text(s: str) -> QUrl:
    t = (s or "").strip()
    if not t: return QUrl()
    if is_email_addr(t):
        return QUrl("mailto:" + t)
    if "://" not in t:
        t = "https://" + t
    return QUrl(t)
def _lpx_encrypt_bytes(raw: bytes, password: str) -> bytes:
    if not password:
        raise ValueError("Пароль шифрования не задан")
    salt = rand_bytes(16)
    key = argon2id_key(password, salt) if HAS_ARGON2 else pbkdf2_key(password, salt)
    tag = b"A" if HAS_ARGON2 else b"P"
    f = Fernet(base64.urlsafe_b64encode(key))
    enc = f.encrypt(raw)
    return b"LPX1" + tag + salt + enc
def _lpx_decrypt_bytes_or_file(data_or_path: bytes | bytearray | memoryview | str, password: str) -> bytes:
    if isinstance(data_or_path, str):
        with open(data_or_path, "rb") as fr:
            data = fr.read()
    else:
        data = bytes(data_or_path)

    if data.startswith(b"LPX1"):
        off = 4
    elif data.startswith(b"LPEX1"):
        off = 5
    elif data.startswith(b"LPBK1"):
        off = 5
    else:
        raise ValueError("Неизвестный зашифрованный формат (LPX1/LPEX1/LPBK1)")
    kdf_tag = data[off:off+1]
    salt = data[off+1:off+17]
    enc  = data[off+17:]
    key = argon2id_key(password, salt) if (kdf_tag == b"A" and HAS_ARGON2) else pbkdf2_key(password, salt)
    f = Fernet(base64.urlsafe_b64encode(key))
    return f.decrypt(enc)
ATTACH_CNT_TTL = 2.0
_attach_cnt_cache: dict[str, tuple[float, int]] = {}
def attachments_count(block_id: str) -> int:    
    try:
        import time
        now = time.time()
        rec = _attach_cnt_cache.get(block_id)
        if rec and now - rec[0] < ATTACH_CNT_TTL:
            return rec[1]
        d = os.path.join(ATTACH_DIR, block_id)
        n = 0
        if os.path.isdir(d):
            with os.scandir(d) as it:
                for entry in it:
                    if entry.is_file() and not entry.name.endswith(".meta.json"):
                        n += 1
        _attach_cnt_cache[block_id] = (now, n)
        return n
    except Exception:
        return 0
def attachments_count_invalidate(block_id: str) -> None:
    _attach_cnt_cache.pop(block_id, None)
class WorkerThread(QtCore.QThread):
    ok = Signal(object)
    fail = Signal(str)
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self._fn = fn
    def run(self):
        try:
            res = self._fn()
            self.ok.emit(res)
        except Exception as e:
            self.fail.emit(str(e))
def run_long_task(parent: QWidget, title: str, fn: Callable[[], Any], on_ok: Callable[[Any], None] | None = None):
    dlg = QProgressDialog(title, "Отмена", 0, 0, parent)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(300)
    th = WorkerThread(fn)
    th.ok.connect(lambda res: (dlg.close(), on_ok(res) if on_ok else None))
    th.fail.connect(lambda msg: (dlg.close(), custom_error(parent, title, msg)))
    th.start()
    dlg.exec()
class IndexDB:
    def __init__(self, path: str, fernet: Fernet, autosave: bool = False):
        self.path = path
        self.fernet = fernet
        self.autosave = autosave
        self._data: dict[str, str] = {}
        class _DummyConn:
            def close(self): pass
        self.conn = _DummyConn()
        self._load()
    def _load(self):
        try:
            if not os.path.exists(self.path):
                self._data = {}
                return
            with open(self.path, "rb") as f:
                head = f.read(16)
            if head.startswith(SECURE_JSON_PREFIX):
                obj = secure_read_json(self.path, self.fernet, {"index": {}})
                self._data = dict(obj.get("index", {})) if isinstance(obj, dict) else {}
                return
            if head.startswith(b"SQLite format 3"):
                try:
                    conn = sqlite3.connect(self.path)
                    c = conn.cursor()
                    try:
                        rows = c.execute("SELECT block_id, text FROM idx").fetchall()
                    except Exception:
                        rows = c.execute("SELECT block_id, text FROM idx_fallback").fetchall()
                    conn.close()
                    self._data = {str(b): str(t) for (b, t) in rows}
                except Exception:
                    self._data = {}
                self.save()
                return
            try:
                with open(self.path, "r", encoding="utf-8") as fr:
                    obj = json.load(fr)
                self._data = dict(obj.get("index", obj)) if isinstance(obj, dict) else {}
                self.save()
            except Exception:
                self._data = {}
        except Exception:
            self._data = {}

    def save(self):
        try:
            secure_write_json(self.path, {"index": self._data}, self.fernet)
        except Exception:
            pass
    def _maybe_save(self):
        if self.autosave:
            self.save()
    def clear(self):
        self._data.clear()
        self._maybe_save()
    def upsert(self, block_id: str, text: str):
        self._data[str(block_id)] = text or ""
        self._maybe_save()
    def delete(self, block_id: str):
        if str(block_id) in self._data:
            del self._data[str(block_id)]
            self._maybe_save()
    def search(self, query: str) -> list[str]:
        q = (query or "").strip().lower()
        if not q:
            return list(self._data.keys())
        return [bid for bid, txt in self._data.items() if q in (txt or "").lower()]
class PasswordDialog(QDialog):
    def __init__(self, title, label, echo_password=True):
        super().__init__()
        self.setWindowTitle(title)
        if os.path.exists(ICON_PATH): self.setWindowIcon(QIcon(ICON_PATH))
        self.setModal(True)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password if echo_password else QLineEdit.EchoMode.Normal)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(label))
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        okb = QPushButton("ОК"); okb.clicked.connect(self.accept)
        cb = QPushButton("Отмена"); cb.clicked.connect(self.reject)
        row.addWidget(okb); row.addWidget(cb)
        lay.addLayout(row)
        self.edit.returnPressed.connect(self.accept)
        okb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
    def value(self): return self.edit.text()
class MasterPasswordDialog(QDialog):
    def __init__(self, parent=None, first_run: bool = False):
        super().__init__(parent)
        self.setObjectName("MasterPwdDlg")
        self.setWindowTitle("Мастер‑пароль")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.setModal(True)
        self.setFixedWidth(420)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        hdrv = QVBoxLayout()
        hdrv.setContentsMargins(0, 0, 0, 0)
        hdrv.setSpacing(4)
        ico = QLabel()
        try:
            pm = QPixmap(ICON_PATH)
            if not pm.isNull():
                ico.setPixmap(pm.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))
            else:
                ico.setText("🔐")
                ico.setStyleSheet("font-size:24px;")
        except Exception:
            ico.setText("🔐")
            ico.setStyleSheet("font-size:24px;")
        ico.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        t = QLabel("LinkPass"); t.setObjectName("mp_title")
        t.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        s = QLabel("Задайте мастер‑пароль" if first_run else "Введите мастер‑пароль"); s.setObjectName("mp_sub")
        s.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        hdrv.addWidget(ico)
        hdrv.addWidget(t)
        hdrv.addWidget(s)
        hdrw = QWidget()
        hdrw.setLayout(hdrv)
        root.addWidget(hdrw, 0, Qt.AlignmentFlag.AlignHCenter)
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Мастер‑пароль")
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        input_row.addWidget(self.edit, 1)
        self.btn_eye = QToolButton()
        self.btn_eye.setCheckable(True)
        self.btn_eye.setText("👁")
        self.btn_eye.setToolTip("Показать/скрыть пароль")
        self.btn_eye.setFixedSize(28, 26)
        self.btn_eye.toggled.connect(
            lambda on: self.edit.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        )
        input_row.addWidget(self.btn_eye, 0)
        self.btn_paste = QToolButton()
        self.btn_paste.setText("📋")
        self.btn_paste.setToolTip("Вставить из буфера обмена")
        self.btn_paste.setFixedSize(28, 26)
        self.btn_paste.clicked.connect(lambda: self.edit.setText(QApplication.clipboard().text()))
        input_row.addWidget(self.btn_paste, 0)
        root.addLayout(input_row)
        hint_row = QHBoxLayout()
        hint_row.setContentsMargins(0, 0, 0, 0)
        hint_row.setSpacing(6)
        self.lbl_caps = QLabel("")
        self.lbl_caps.setObjectName("mp_caps")
        hint_row.addWidget(self.lbl_caps, 0, Qt.AlignmentFlag.AlignVCenter)
        hint_row.addStretch(1)
        if first_run:
            lbl_tip = QLabel("Рекомендуется ≥ 10 символов, с цифрами и символами.")
            lbl_tip.setObjectName("mp_tip")
            hint_row.addWidget(lbl_tip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(hint_row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setVisible(first_run)
        if first_run:
            self.edit.textChanged.connect(self._update_strength)
        root.addWidget(self.bar)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        btn_row.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Создать" if first_run else "Войти")
        ok.setProperty("variant", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        btn_row.addStretch(1)
        root.addLayout(btn_row)
        self.edit.setFocus()
        self.edit.returnPressed.connect(self.accept)
        for w in (self.btn_eye, self.btn_paste, ok, cancel):
            w.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
        QDialog#MasterPwdDlg { background: #FFFFFF; }
        QLabel#mp_title { font-size: 16px; font-weight: 700; color: #111; }
        QLabel#mp_sub   { font-size: 12px; color: #666; margin-top: 2px; }
        QLabel#mp_caps  { font-size: 11px; color: #b00020; }
        QLabel#mp_tip   { font-size: 11px; color: #555; }
        QLineEdit {
            padding: 6px 8px;
            border: 1px solid #d7d7d7; border-radius: 6px;
            font-size: 14px;
        }
        QLineEdit:focus { border-color: #4979e1; }
        QToolButton {
            border: 1px solid #d7d7d7; border-radius: 6px;
            padding: 0px; background: #f4f4f4;
        }
        QToolButton:hover { background: #eeeeee; }
        QPushButton[variant="primary"] {
            background: #2f6f3a; color: #fff; border: none; border-radius: 6px; padding: 6px 12px;
        }
        QPushButton[variant="primary"]:hover { background: #2b6335; }
        QPushButton {
            background: #f7f7f7; border: 1px solid #e1e1e1; border-radius: 6px; padding: 6px 12px;
        }
        QProgressBar { background: #ececec; border: 1px solid #e3e3e3; border-radius: 3px; }
        QProgressBar::chunk { background: #2f6f3a; border-radius: 3px; }
        """)
        self._caps_timer = QTimer(self)
        self._caps_timer.setInterval(300)
        self._caps_timer.timeout.connect(self._check_caps)
        self._caps_timer.start()
    def _check_caps(self):
        try:
            import ctypes
            on = bool(ctypes.WinDLL("user32").GetKeyState(0x14) & 1)
            self.lbl_caps.setText("Включён CAPS LOCK" if on else "")
        except Exception:
            self.lbl_caps.setText("")
            self._caps_timer.stop()
    def _update_strength(self, s: str):
        score = 0
        L = len(s or "")
        if L >= 8: score += 30
        elif L >= 6: score += 15
        if any(c.islower() for c in s): score += 15
        if any(c.isupper() for c in s): score += 15
        if any(c.isdigit() for c in s): score += 15
        if any(c in "!@#$%^&*()-_=+[]{};:,.?/|\\~`" for c in s): score += 25
        self.bar.setValue(min(100, score))
    def value(self) -> str:
        return self.edit.text()
class CursorFilter(QObject):
    def eventFilter(self, obj, event):
        if isinstance(obj, QPushButton) and event.type() == QEvent.Type.Enter:
            obj.setCursor(Qt.CursorShape.PointingHandCursor)
        return super().eventFilter(obj, event)
class InactivityFilter(QObject):
    activity = Signal()
    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel):
            self.activity.emit()
        return super().eventFilter(obj, event)
class SectionTree(QTreeWidget):
    blockDropped = Signal(str, list)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setUniformRowHeights(True)
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat("application/x-linkpass-block-id"): e.acceptProposedAction()
        else: super().dragEnterEvent(e)
    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat("application/x-linkpass-block-id"): e.acceptProposedAction()
        else: super().dragMoveEvent(e)
    def dropEvent(self, e):
        if not e.mimeData().hasFormat("application/x-linkpass-block-id"):
            return super().dropEvent(e)
        item = self.itemAt(e.position().toPoint())
        if not item: return
        path = []
        cur = item
        while cur and cur.text(0) != "🧠 Умные папки":
            path.insert(0, cur.text(0))
            cur = cur.parent()
        target = "/".join(path)
        data = bytes(e.mimeData().data("application/x-linkpass-block-id")).decode("utf-8")
        ids = [x for x in data.split(",") if x]
        self.blockDropped.emit(target, ids)
        e.acceptProposedAction()
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidgetItem, QWidget, QSizePolicy
from PySide6.QtCore import QRect, QSize, Qt
class GridWrapLayout(QLayout):
    def __init__(self, parent=None, *, margin=8, hSpacing=14, vSpacing=14, card_width=280):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h = int(hSpacing)
        self._v = int(vSpacing)
        self._cw = int(card_width)
        self.setContentsMargins(margin, margin, margin, margin)
    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)
    def count(self) -> int:
        return len(self._items)
    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None
    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None
    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))
    def hasHeightForWidth(self) -> bool:
        return True
    def heightForWidth(self, width: int) -> int:
        return self._doLayout(QRect(0, 0, width, 0), testOnly=True)
    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._doLayout(rect, testOnly=False)
    def sizeHint(self) -> QSize:
        return self.minimumSize()
    def minimumSize(self) -> QSize:
        m = self.contentsMargins()
        w = self._cw + m.left() + m.right()
        h_max = 24
        for it in self._items:
            h_max = max(h_max, it.sizeHint().height())
        h = h_max + m.top() + m.bottom()
        return QSize(w, h)
    def _doLayout(self, rect: QRect, *, testOnly: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        if eff.width() <= 0:
            return rect.height()
        ncols = max(1, int((eff.width() + self._h) // (self._cw + self._h)))
        col_x = [eff.x() + i * (self._cw + self._h) for i in range(ncols)]
        rows: list[list[QLayoutItem]] = []
        buf: list[QLayoutItem] = []
        for it in self._items:
            buf.append(it)
            if len(buf) == ncols:
                rows.append(buf); buf = []
        if buf:
            rows.append(buf)
        y = eff.y()
        for row_items in rows:
            row_h = max((it.sizeHint().height() for it in row_items), default=1)
            if not testOnly:
                for c, it in enumerate(row_items):
                    it.setGeometry(QRect(col_x[c], y, self._cw, row_h))
            y += row_h + self._v
        if rows:
            y -= self._v
        return (y - eff.y()) + m.top() + m.bottom()
def default_theme():
    return {
        "window_bg": "#F2F2F2",
        "menubar_bg": "#FFFFFF",
        "menubar_fg": "#202020",
        "menu_bg": "#FFFFFF",
        "menu_fg": "#202020",
        "left_bg": "#FAFAFA",
        "tree_fg": "#202020",
        "kanban_bg": "#FFFFFF",
        "card_bg": "#FFFFFF",
        "block_title_fg": "#101010",
        "field_label_fg": "#333333",
        "field_text_fg": "#000000",
        "tag_bg": "#FFF2B2",
        "attach_badge_bg": "#E7F0FF",
        "attach_badge_fg": "#0F172A",
        "btn_open_bg": "#2f6f3a",
        "btn_bottoms_bg": "#f3f3f3",
        "btn_move_bg": "#0e7490",
        "btn_files_bg": "#4338ca",
        "btn_delete_bg": "#7f1d1d",
        "btn_add_section_bg": "#1d4ed8",
        "btn_add_subsection_bg": "#065f46",
        "btn_delete_section_bg": "#7f1d1d",
        "btn_fg": "#FFFFFF",
        "btn_radius": 10,
    }
def merge_theme(t):
    base = default_theme()
    if t:
        for k, v in t.items(): base[k] = v
    return base
ALPHANUM = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*()-_=+[]{};:,.?/|"
class PasswordToolsDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Пароли и шифрование")
        if os.path.exists(ICON_PATH): self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(700, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>Генератор паролей</b>"))
        row1 = QHBoxLayout()
        self.combo_enc = QComboBox(); self.combo_enc.addItems(["Base64url", "Hex", "Алфавит"])
        row1.addWidget(QLabel("Кодировка:")); row1.addWidget(self.combo_enc); row1.addStretch(1)
        lay.addLayout(row1)
        self.out_edit = QLineEdit(); self.out_edit.setReadOnly(True)
        lay.addWidget(self.out_edit)
        rowb = QHBoxLayout()
        for bits in (32, 64, 128, 256, 512, 1024, 2048):
            b = QPushButton(f"{bits} бит")
            b.clicked.connect(lambda _, n=bits: self.generate(n))
            rowb.addWidget(b)
        rowb.addStretch(1)
        lay.addLayout(rowb)
        rowc = QHBoxLayout()
        bc = QPushButton("Скопировать")
        bc.clicked.connect(lambda: (QApplication.clipboard().setText(self.out_edit.text()), self.win.clip_timer.start(self.win.CLIPBOARD_SEC * 1000)))
        rowc.addWidget(bc); rowc.addStretch(1)
        lay.addLayout(rowc)
        lay.addSpacing(12)
        lay.addWidget(QLabel("<b>Шифрование / Дешифрование (Fernet)</b>"))
        self.in_edit = QTextEdit(); self.in_edit.setPlaceholderText("Текст или шифротекст…")
        lay.addWidget(self.in_edit)
        self.out2 = QTextEdit(); self.out2.setReadOnly(True)
        lay.addWidget(self.out2)
        rowd = QHBoxLayout()
        btn_enc = QPushButton("Зашифровать"); btn_enc.clicked.connect(self.do_encrypt)
        btn_dec = QPushButton("Расшифровать"); btn_dec.clicked.connect(self.do_decrypt)
        rowd.addWidget(btn_enc); rowd.addWidget(btn_dec); rowd.addStretch(1)
        lay.addLayout(rowd)
        close = QPushButton("Закрыть"); close.clicked.connect(self.accept)
        lay.addWidget(close)
    def generate(self, bits: int):
        enc = self.combo_enc.currentText()
        if enc == "Base64url":
            b = rand_bytes(bits // 8)
            s = base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")
            self.out_edit.setText(s)
        elif enc == "Hex":
            b = rand_bytes(bits // 8)
            self.out_edit.setText(b.hex())
        else:
            alphabet = ALPHANUM
            length = math.ceil(bits / math.log2(len(alphabet)))
            s = "".join(secrets.choice(alphabet) for _ in range(length))
            self.out_edit.setText(s)
    def do_encrypt(self):
        txt = self.in_edit.toPlainText()
        try:
            enc = self.win.fernet.encrypt(txt.encode("utf-8")).decode("utf-8")
            self.out2.setPlainText(enc)
        except Exception as e:
            self.out2.setPlainText(f"[Ошибка шифрования] {e}")
    def do_decrypt(self):
        txt = self.in_edit.toPlainText()
        try:
            dec = self.win.fernet.decrypt(txt.encode("utf-8")).decode("utf-8")
            self.out2.setPlainText(dec)
        except Exception as e:
            self.out2.setPlainText(f"[Ошибка расшифровки] {e}")
class BlockEditorDialog(QDialog):
    def __init__(self, win, block, open_tab: str | None = None):
        super().__init__(win)
        self.win = win
        self.block = block
        self.setWindowTitle(f"Блок: {block.get('title','')}")
        if os.path.exists(ICON_PATH): 
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(820, 700)
        self.tabs = QTabWidget(self)
        main = QWidget()
        self.main_l = QVBoxLayout(main)
        self.main_l.setContentsMargins(8, 8, 8, 8)
        self.LABEL_W = 110
        self._build_title_row()
        self.fields_container = QWidget()
        self.fields_layout = QVBoxLayout(self.fields_container)
        self.fields_layout.setContentsMargins(0, 0, 0, 0)
        self.main_l.addWidget(self.fields_container)
        self.field_edits = {}
        self.rebuild_fields()
        add_row = QHBoxLayout()
        self.new_key = QLineEdit()
        self.new_key.setPlaceholderText("Название поля")
        btn_add = QPushButton("Добавить поле")
        btn_add.clicked.connect(self.add_field)
        add_row.addWidget(self.new_key)
        add_row.addWidget(btn_add)
        self.main_l.addLayout(add_row)
        btn_copy = QPushButton("Копировать все поля в буфер")
        btn_copy.clicked.connect(self.copy_all)
        self.main_l.addWidget(btn_copy)
        idx_main = self.tabs.addTab(main, "Основное")
        att = QWidget()
        al = QVBoxLayout(att)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        self.lst_att = QListWidget()
        self.lst_att.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        ll.addWidget(self.lst_att)
        row = QHBoxLayout()
        b_add = QPushButton("Добавить файл")
        b_add.clicked.connect(self.add_attachment)
        b_save = QPushButton("Сохранить как…")
        b_save.clicked.connect(self.save_attachment_as)
        b_del = QPushButton("Удалить вложение")
        b_del.clicked.connect(self.delete_attachment)
        row.addWidget(b_add)
        row.addWidget(b_save)
        row.addWidget(b_del)
        btn_share_files = QPushButton()
        btn_share_files.setProperty("minsize", "compact")
        btn_share_files.setToolTip("Поделиться выбранными файлами")
        share_ico = resource_path("icons/share.png")
        if os.path.exists(share_ico):
            btn_share_files.setIcon(QIcon(share_ico))
            btn_share_files.setIconSize(QtCore.QSize(18, 18))
        else:
            btn_share_files.setText("📤")
        def _share_menu():
            m = QMenu(self)
            a_tg   = QAction(brand_icon("telegram"), "Telegram", self)
            a_wa   = QAction(brand_icon("whatsapp"), "WhatsApp", self)
            a_mail = QAction(brand_icon("mail"),     "Email",    self)
            a_tg.triggered.connect(self._share_files_tg)
            a_wa.triggered.connect(self._share_files_wa)
            a_mail.triggered.connect(self._share_files_email)
            m.addAction(a_tg); m.addAction(a_wa); m.addAction(a_mail)
            m.exec(QCursor.pos())
        btn_share_files.clicked.connect(_share_menu)
        row.addWidget(btn_share_files)
        row.addStretch(1)
        ll.addLayout(row)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        self.preview_stack = QStackedWidget()
        self.preview_img = QLabel("Предпросмотр")
        self.preview_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_blank = QLabel("Выберите файл во вложениях")
        self.preview_blank.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_stack.addWidget(self.preview_blank)
        self.preview_stack.addWidget(self.preview_img)
        self.preview_stack.addWidget(self.preview_text)
        rl.addWidget(self.preview_stack)
        split.addWidget(right)
        al.addWidget(split)
        self.populate_attachments()
        idx_att = self.tabs.addTab(att, "Вложения")
        idx_notes = self._build_notes_tab()
        self.lst_att.currentItemChanged.connect(lambda *_: self._show_preview())
        raw = QWidget()
        rlay = QVBoxLayout(raw)
        self.raw_edit = QTextEdit()
        self.refresh_json_view()
        rlay.addWidget(self.raw_edit)
        row_json = QHBoxLayout()
        b_apply_json = QPushButton("Применить JSON")
        b_apply_json.clicked.connect(self.apply_json_changes)
        row_json.addStretch(1)
        row_json.addWidget(b_apply_json)
        rlay.addLayout(row_json)
        idx_json = self.tabs.addTab(raw, "JSON")
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        btns = QHBoxLayout()
        okb = QPushButton("Сохранить")
        okb.clicked.connect(self.save)
        cb = QPushButton("Закрыть")
        cb.clicked.connect(self.reject)
        btns.addWidget(okb)
        btns.addWidget(cb)
        lay.addLayout(btns)
        self.idx_main, self.idx_att, self.idx_notes, self.idx_json = idx_main, idx_att, idx_notes, idx_json
        if open_tab == "attachments":
            self.tabs.setCurrentIndex(self.idx_att)
        elif open_tab == "notes":
            self.tabs.setCurrentIndex(self.idx_notes)   
    def refresh_json_view(self):
        self.raw_edit.setPlainText(json.dumps(self.block, ensure_ascii=False, indent=2))
    def _build_title_row(self) -> None:
        self.LABEL_W = getattr(self, "LABEL_W", 110)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_lab = QLabel("Название:")
        title_lab.setMinimumWidth(self.LABEL_W)
        title_lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        title_lab.setStyleSheet(f"color:{self.win.theme.get('field_label_fg', '#222')};")
        self.title = QLineEdit(self.block.get("title", ""))
        title_lab.setBuddy(self.title)
        title_row.addWidget(title_lab)
        title_row.addWidget(self.title, 1)
        self.main_l.addLayout(title_row)
    def _build_notes_tab(self) -> int:
        notes = QWidget()
        nl = QVBoxLayout(notes)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMaximumBlockCount(0)
        self.notes_edit.setPlaceholderText("Свободные заметки по блоку…")
        if self._can_show():
            try:
                self.notes_edit.setPlainText(decrypt_value(self.block.get("notes", ""), self.win.fernet))
            except Exception:
                self.notes_edit.setPlainText("")
            self.notes_edit.setReadOnly(False)
        else:
            self.notes_edit.setPlainText(mask_text(""))
            self.notes_edit.setReadOnly(True)
        nl.addWidget(self.notes_edit)
        return self.tabs.addTab(notes, "Заметки")
    def _block_path_list(self) -> list[str]:
        ref = self.win.id_to_ref.get(self.block.get("id",""))
        key = ref[0] if ref else ""
        return [p for p in key.split("/") if p]
    def _can_show(self) -> bool:
        ref = self.win.id_to_ref.get(self.block.get("id", ""))
        key = ref[0] if ref else ""
        path_list = [p for p in key.split("/") if p]
        locked = self.win._locked_prefixes(path_list)
        return all(p in self.win.unlocked_sections for p in locked)
    def commit_edited_fields(self):
        if not self._can_show():
            return
        for k, le in self.field_edits.items():
            self.block["fields"][k] = encrypt_value(le.text(), self.win.fernet)
        try:
            if hasattr(self, "notes_edit"):
                self.block["notes"] = encrypt_value(self.notes_edit.toPlainText(), self.win.fernet)
        except Exception:
            pass
    def rebuild_fields(self):
        def clear_layout(lay: QLayout) -> None:
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
                child = item.layout()
                if child is not None:
                    clear_layout(child)
        clear_layout(self.fields_layout)
        self.field_edits = {}
        def _clean_label(s: str) -> str:
            s = "" if s is None else str(s)
            s = s.replace("\u00A0", " ")
            for ch in ("\u200B","\u200C","\u200D","\u2060","\uFEFF","\u180E","\u202A","\u202B","\u202C","\u202D","\u202E"):
                s = s.replace(ch, "")
            if s.endswith(":"): s = s[:-1].strip()
            return s.strip() or "Поле"
        for k, v in (self.block.get("fields") or {}).items():
            row = QHBoxLayout()
            lab = QLabel(_clean_label(k) + ":")
            lab.setStyleSheet(f"color:{self.win.theme.get('field_label_fg', '#222')};")
            if hasattr(self, "LABEL_W"):
                lab.setMinimumWidth(self.LABEL_W)
            lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(lab)
            if self._can_show():
                val = decrypt_value(v, self.win.fernet)
                le = QLineEdit(val)
            else:
                le = QLineEdit(mask_text(""))
            le.setReadOnly(not self._can_show())
            le.setStyleSheet(f"color:{self.win.theme.get('field_text_fg', '#000')};")
            row.addWidget(le)
            if self._can_show():
                try_val = decrypt_value(v, self.win.fernet)
                if try_val and (is_url(try_val) or is_email_addr(try_val)):
                    btn_go = QPushButton("↗")
                    btn_go.setFixedWidth(28); btn_go.setProperty("minsize", "compact")
                    btn_go.clicked.connect(lambda _=False, url=try_val: QDesktopServices.openUrl(to_qurl_from_text(url)))
                    row.addWidget(btn_go)
            btn_del = QPushButton("Удалить поле")
            btn_del.clicked.connect(lambda _, kk=k: self.delete_field(kk))
            row.addWidget(btn_del)
            self.fields_layout.addLayout(row)
            self.field_edits[k] = le
    def att_dir(self):
        bid = self.block.get("id","")
        d = os.path.join(ATTACH_DIR, bid)
        os.makedirs(d, exist_ok=True)
        return d
    def populate_attachments(self):
        self.lst_att.clear()
        d = self.att_dir()
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".meta.json"): continue
            self.lst_att.addItem(fn)
    def _show_preview(self):
        it = self.lst_att.currentItem()
        if not it:
            self.preview_stack.setCurrentIndex(0)
            return
        fn = it.text()
        src = os.path.join(self.att_dir(), fn)
        try:
            with open(src, "rb") as f:
                enc = f.read()
            data = self.win.fernet.decrypt(enc)
        except Exception:
            self.preview_stack.setCurrentIndex(0)
            return
        pm = QPixmap()
        if pm.loadFromData(data):
            self.preview_img.setPixmap(
                pm.scaled(480, 480, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
            self.preview_stack.setCurrentIndex(1)
            return
        if len(data) <= 1_000_000:
            try:
                txt = data.decode("utf-8")
                self.preview_text.setPlainText(txt)
                self.preview_stack.setCurrentIndex(2)
                return
            except Exception:
                pass
        self.preview_stack.setCurrentIndex(0)
    def add_attachment(self):
        path, _ = QFileDialog.getOpenFileName(self, "Добавить файл", "", "All Files (*)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            enc = self.win.fernet.encrypt(data)
            base = os.path.basename(path)
            ts   = datetime.now().strftime("%Y%m%d-%H%M%S")
            anon = secrets.token_hex(12)
            out  = os.path.join(self.att_dir(), f"{ts}_{anon}.bin")
            with open(out, "wb") as w:
                w.write(enc)
            meta = {"orig_name": base, "size": len(data), "added": datetime.utcnow().isoformat()+"Z"}
            secure_write_json(out + ".meta.json", meta, self.win.fernet)
            self.populate_attachments()
            attachments_count_invalidate(self.block.get("id", ""))
            self.win.schedule_render()
            audit_write("attachment_add", {"block_id": self.block.get("id",""), "file": os.path.basename(out)})
        except Exception as e:
            QMessageBox.warning(self, "Вложения", f"Ошибка: {e}")
    def save_attachment_as(self):
        it = self.lst_att.currentItem()
        if not it:
            return
        fn = it.text()
        src = os.path.join(self.att_dir(), fn)
        try:
            with open(src, "rb") as f:
                enc = f.read()
            data = self.win.fernet.decrypt(enc)
            suggested = fn.replace(".bin", "")
            mpath = src + ".meta.json"
            if os.path.exists(mpath):
                try:
                    mj = secure_read_json(mpath, self.win.fernet, {})
                    orig = (mj.get("orig_name") or "").strip()
                    if orig:
                        suggested = orig
                except Exception:
                    pass
            save, _ = QFileDialog.getSaveFileName(self, "Сохранить как", suggested)
            if not save:
                return
            with open(save, "wb") as w:
                w.write(data)
            audit_write("attachment_save_as", {"block_id": self.block.get("id",""), "file": fn, "target": save})
        except Exception as e:
            QMessageBox.warning(self, "Вложения", f"Ошибка: {e}")
    def delete_attachment(self):
        it = self.lst_att.currentItem()
        if not it: 
            return
        fn = it.text()
        if not custom_question(self, "Удалить вложение?", f"Удалить «{fn}»?"):
            return
        try:
            d = self.att_dir()
            os.remove(os.path.join(d, fn))
            m = os.path.join(d, fn + ".meta.json")
            if os.path.exists(m):
                os.remove(m)
            self.populate_attachments()
            attachments_count_invalidate(self.block.get("id", ""))
            self.win.schedule_render()
            audit_write("attachment_delete", {"block_id": self.block.get("id",""), "file": fn})
        except Exception as e:
            QMessageBox.warning(self, "Вложения", f"Ошибка: {e}")
    def _export_selected_attachments_for_share(self, open_folder: bool = True) -> list[str]:
        items = self.lst_att.selectedItems()
        if not items:
            QMessageBox.information(self, "Вложения", "Выберите один или несколько файлов.")
            return []
        out_dir = self.win.make_temp_share_dir(self.block.get("id", "files"))
        saved: list[str] = []
        for it in items:
            fn = it.text()
            src = os.path.join(self.att_dir(), fn)
            try:
                with open(src, "rb") as f:
                    enc = f.read()
                data = self.win.fernet.decrypt(enc)
                meta_name = None
                mpath = src + ".meta.json"
                if os.path.exists(mpath):
                    try:
                        mj = secure_read_json(mpath, self.win.fernet, {})
                        meta_name = (mj.get("orig_name") or "").strip() or None
                    except Exception:
                        pass
                base = meta_name or fn.replace(".bin", "")
                dst = os.path.join(out_dir, base)
                root, ext = os.path.splitext(dst)
                k = 1
                while os.path.exists(dst):
                    dst = f"{root} ({k}){ext}"
                    k += 1
                with open(dst, "wb") as w:
                    w.write(data)
                saved.append(dst)
            except Exception as e:
                QMessageBox.warning(self, "Вложения", f"Не удалось подготовить «{fn}»: {e}")
        if open_folder:
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(out_dir))
            except Exception:
                pass
        return saved
    def _share_files_tg(self) -> None:
        files = self._export_selected_attachments_for_share()
        if files:
            self.win.share_files_telegram(files)
    def _share_files_wa(self) -> None:
        files = self._export_selected_attachments_for_share(open_folder=True)
        if files:
            self.win.share_files_whatsapp(files)
    def _share_files_email(self) -> None:
        files = self._export_selected_attachments_for_share()
        if files:
            self.win.share_files_email(self.block, files)
    def build_share_menu(self) -> QMenu:
        m = QMenu(self)
        a_tg   = QAction(brand_icon("telegram"), "✈️", self)
        a_wa   = QAction(brand_icon("whatsapp"), "💬", self)
        a_mail = QAction(brand_icon("mail"),     "✉️", self)
        a_tg.setToolTip("Отправить файлы в Telegram")
        a_wa.setToolTip("Отправить файлы в WhatsApp")
        a_mail.setToolTip("Открыть письмо (прикрепите файлы вручную)")
        a_tg.triggered.connect(self._share_files_tg)
        a_wa.triggered.connect(self._share_files_wa)
        a_mail.triggered.connect(self._share_files_email)
        m.addAction(a_tg); m.addAction(a_wa); m.addAction(a_mail)
        return m
    def add_field(self):
        self.commit_edited_fields()
        self.win.on_block_changed(self.block, meta={"autosave_before_add_field": True})
        k = (self.new_key.text() or "").strip()
        if not k:
            return
        if k in self.block.setdefault("fields", {}):
            return
        self.block["fields"][k] = encrypt_value("", self.win.fernet)
        self.win.on_block_changed(self.block, meta={"add_field": k})
        self.new_key.clear()
        self.rebuild_fields()
        self.refresh_json_view()
    def delete_field(self, key):
        self.commit_edited_fields()
        self.win.on_block_changed(self.block, meta={"autosave_before_delete_field": True})
        if not custom_question(self, "Удалить поле?", f"Поле «{key}» будет удалено безвозвратно. Продолжить?"):
            return
        prev = decrypt_value(self.block["fields"][key], self.win.fernet) if self._can_show() else ""
        del self.block["fields"][key]
        self.win.on_block_changed(self.block, meta={"delete_field": key, "prev": prev})

        self.rebuild_fields()
        self.refresh_json_view()

    def copy_all(self):
        title_txt = (self.title.text() if hasattr(self, "title") else self.block.get("title", ""))
        title_txt = (title_txt or "").strip()
        lines = [f"Название: {title_txt}"] if title_txt else ["Название: (без названия)"]

        if not self._can_show():
            path_list = self._block_path_list()
            if not self.win.ensure_chain_unlocked(path_list):
                QApplication.clipboard().setText("\n".join(lines))
                self.win.clip_timer.start(self.win.CLIPBOARD_SEC * 1000)
                return
        for k, v in (self.block.get("fields") or {}).items():
            lines.append(f"{k}: {decrypt_value(v, self.win.fernet)}")
        notes_plain = decrypt_value(self.block.get("notes", ""), self.win.fernet)
        if (notes_plain or "").strip():
            lines.append("Заметки:")
            lines.append(notes_plain)
        QApplication.clipboard().setText("\n".join(lines))
        self.win.clip_timer.start(self.win.CLIPBOARD_SEC * 1000)
    def save(self):
        self.commit_edited_fields()
        old = self.block.get("title", "")
        new_title = self.title.text() if hasattr(self, "title") else str(old)
        self.block["title"] = new_title
        self.win.on_block_changed(self.block, meta={"title_from": old, "title_to": new_title})
        self.accept()
    def apply_json_changes(self):
        try:
            j = json.loads(self.raw_edit.toPlainText())
            if not isinstance(j, dict):
                raise ValueError("Ожидается JSON-объект блока (dict).")
            j.setdefault("id", self.block.get("id", secrets.token_hex(12)))
            j.setdefault("title", "")
            j.setdefault("category", self.block.get("category",""))
            j.setdefault("fields", {})
            j.setdefault("notes", "")
            fields = {}
            for k, v in (j.get("fields") or {}).items():
                plain, ok = self.win._try_decrypt_once(v)
                if ok:
                    fields[k] = encrypt_value(plain, self.win.fernet)
                else:
                    fields[k] = encrypt_value(str(v) if v is not None else "", self.win.fernet)
            j["fields"] = fields
            n_plain, okn = self.win._try_decrypt_once(j.get("notes", ""))
            j["notes"] = encrypt_value(n_plain if okn else str(j.get("notes","") or ""), self.win.fernet)
            self.block.clear()
            self.block.update(j)
            self.win.on_block_changed(self.block, meta={"apply_json": True})
            self.rebuild_fields()
            try:
                if hasattr(self, "notes_edit"):
                    self.notes_edit.setPlainText(decrypt_value(self.block.get("notes",""), self.win.fernet))
            except Exception:
                pass
            self.refresh_json_view()
            custom_info(self, "JSON", "Изменения применены.")
        except Exception as e:
            custom_error(self, "JSON", f"Ошибка: {e}")
class MainWindow(QMainWindow):
    CLIPBOARD_SEC = 30
    def __init__(self, master_pass: str):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(1340, 880)
        try:
            QLocale.setDefault(QLocale(QLocale.Language.Russian, QLocale.Country.Russia))
        except Exception:
            pass
        self.master = master_pass
        self.key_salt, self.auth_salt = self.ensure_salts()
        self.fernet = make_fernet(self.master, self.key_salt,
                                kdf_name=self.kdf_name, params=self.kdf_params)
        self.meta = self.load_meta()
        self.theme = default_theme()
        self.data_tree = self.load_tree()
        self.blocks_data = self.load_blocks()
        self._temp_share_dirs: set[str] = set()
        self.trash = self.load_trash()
        self.id_to_ref = {}
        self.index = IndexDB(INDEX_DB, self.fernet, autosave=False)
        self.rebuild_index()
        self.current_path: List[str] = []
        self.show_data = False
        self.unlocked_sections: set[str] = set()
        self.search_mode = "Блоки"
        self._rendering = False
        self.clip_timer = QTimer(self); self.clip_timer.setSingleShot(True)
        self.clip_timer.timeout.connect(lambda: QApplication.clipboard().clear())
        self.scheduler = QTimer(self); self.scheduler.timeout.connect(self.tick_scheduler); self.scheduler.start(60 * 1000)
        self.inact_timer = QTimer(self); self.inact_timer.setSingleShot(True); self.inact_timer.timeout.connect(self.auto_lock)
        self.inact_filter = InactivityFilter(self)
        self.inact_filter.activity.connect(lambda: self.inact_timer.start(int(self.meta.get("autolock_sec", 0) or 0) * 1000) if int(self.meta.get("autolock_sec", 0) or 0) > 0 else None)
        app = QApplication.instance()
        if app is not None:
            self.cursor_filter = CursorFilter(self)
            app.installEventFilter(self.cursor_filter)
            app.installEventFilter(self.inact_filter)
        self._render_timer = QTimer(self); self._render_timer.setSingleShot(True); self._render_timer.timeout.connect(self.render_dashboard)
        self.build_menu()
        self.build_ui()
        self.apply_theme()
        self.render_tree()
        self.schedule_render()
        self.init_tray()
        self.ensure_verifier_current()
        try:
            want = KDF_DEFAULTS
            if (getattr(self, "kdf_name", "argon2id") == "argon2id" and
                (self.kdf_params.get("t") != want["t"] or
                self.kdf_params.get("m") != want["m"] or
                self.kdf_params.get("p") != want["p"])):
                if custom_question(self, "Параметры KDF",
                                f"Обновить Argon2id до t={want['t']}, m={want['m']//1024} MiB, p={want['p']}?"):
                    self.migrate_kdf_params(want, "argon2id")
        except Exception:
            pass
        audit_write("app_start", {"version": CURRENT_VERSION})
    def run_startup_migrations(self) -> None:
        try:
            st = os.path.join(DATA_DIR, "_share_text")
            if os.path.isdir(st):
                for fn in os.listdir(st):
                    if fn.lower().endswith(".txt"):
                        p = os.path.join(st, fn)
                        try:
                            with open(p, "r", encoding="utf-8") as f:
                                txt = f.read()
                            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                            out = os.path.join(st, f"message_{ts}.json")
                            secure_write_json(out, {"ts": datetime.utcnow().isoformat()+"Z", "text": txt}, self.fernet)
                        except Exception:
                            pass
                        try:
                            os.remove(p)
                        except Exception:
                            pass
        except Exception:
            pass
    def ensure_salts(self):
        if os.path.exists(MASTER_FILE):
            with open(MASTER_FILE, "r", encoding="utf-8") as f:
                j = json.load(f)
            key_salt = base64.b64decode(j["key_salt"])
            auth_salt = base64.b64decode(j["auth_salt"])
            self.kdf_name = j.get("kdf", "argon2id" if HAS_ARGON2 else "pbkdf2")
            self.kdf_params = j.get("kdf_params") or KDF_DEFAULTS
            if "kdf_params" not in j:
                write_auth_file(key_salt, auth_salt, j.get("verifier", ""),
                                self.kdf_name, self.kdf_params)
            return key_salt, auth_salt
        key_salt = rand_bytes(SALT_LEN)
        auth_salt = rand_bytes(SALT_LEN)
        self.kdf_name = "argon2id" if HAS_ARGON2 else "pbkdf2"
        self.kdf_params = KDF_DEFAULTS
        ver = hash_for_auth(self.master, auth_salt,
                            prefer_argon=(self.kdf_name == "argon2id"),
                            params=self.kdf_params)
        write_auth_file(key_salt, auth_salt, ver, self.kdf_name, self.kdf_params)
        return key_salt, auth_salt
    def _locked_prefixes(self, path_list: list[str]) -> list[str]:
        res: list[str] = []
        cur: list[str] = []
        for name in path_list:
            cur.append(name)
            n = self._node_by_path(cur)
            if n and n.get("lock"):
                res.append("/".join(cur))
        return res
    def ensure_chain_unlocked(self, path_list: list[str]) -> bool:
        for i in range(1, len(path_list) + 1):
            pref = path_list[:i]
            key = "/".join(pref)
            n = self._node_by_path(pref)
            if n and n.get("lock") and key not in self.unlocked_sections:
                if not self._verify_section_password(pref):
                    return False
        return True
    def ensure_verifier_current(self):
        try:
            with open(MASTER_FILE, "r", encoding="utf-8") as f:
                j = json.load(f)
            key_salt = base64.b64decode(j["key_salt"])
            auth_salt = base64.b64decode(j["auth_salt"])
            kdf_name = j.get("kdf", getattr(self, "kdf_name", "argon2id" if HAS_ARGON2 else "pbkdf2"))
            kdf_params = j.get("kdf_params") or getattr(self, "kdf_params", KDF_DEFAULTS)
            calc = hash_for_auth(self.master, auth_salt,
                                prefer_argon=(kdf_name == "argon2id"),
                                params=kdf_params)
            init_ver = hash_for_auth("init", auth_salt,
                                    prefer_argon=(kdf_name == "argon2id"),
                                    params=kdf_params)
            if j.get("verifier") in (None, "", init_ver) or "kdf_params" not in j or j.get("kdf") != kdf_name:
                write_auth_file(key_salt, auth_salt, calc, kdf_name, kdf_params)
        except Exception:
            pass
    def verify_master_prompt(self, caption="Подтверждение", label="Введите мастер-пароль для подтверждения:") -> bool:
        pwd = self.ask_password(caption, label)
        if pwd is None:
            return False
        try:
            with open(MASTER_FILE, "r", encoding="utf-8") as f:
                j = json.load(f)
            auth_salt = base64.b64decode(j["auth_salt"])
            kdf = j.get("kdf", "argon2id")
            params = j.get("kdf_params") or KDF_DEFAULTS
            calc = hash_for_auth(pwd, auth_salt, prefer_argon=(kdf=="argon2id"), params=params)
            if calc == j.get("verifier"):
                return True
        except Exception:
            pass
        custom_error(self, "Ошибка", "Неверный мастер-пароль.")
        return False
    def load_meta(self):
        meta = {
            "version": CURRENT_VERSION,
            "smart_folders": [],
            "templates": [],
            "export_presets": [],
            "export_tasks": [],
            "autolock_sec": 0,
            "theme": default_theme()
        }
        obj = secure_read_json(META_FILE, self.fernet, meta)
        meta.update(obj if isinstance(obj, dict) else {})
        meta["theme"] = merge_theme(meta.get("theme"))
        meta["version"] = CURRENT_VERSION
        return meta
    def save_meta(self):
        self.meta["theme"] = self.theme
        self.meta["version"] = CURRENT_VERSION
        secure_write_json(META_FILE, self.meta, self.fernet)
    def load_tree(self):
        obj = secure_read_json(TREE_FILE, self.fernet, [])
        return obj if isinstance(obj, list) else []
    def save_tree(self):
        secure_write_json(TREE_FILE, self.data_tree, self.fernet)
    def load_blocks(self):
        blocks = secure_read_json(BLOCKS_FILE, self.fernet, {})
        if not isinstance(blocks, dict):
            blocks = {}
        changed = False
        for key, arr in list(blocks.items()):
            if not isinstance(arr, list):
                continue
            for b in arr:
                if "id" not in b:
                    b["id"] = secrets.token_hex(12); changed = True
                b.setdefault("fields", {})
                n0 = b.get("notes", "")
                plain_notes, okn = self._try_decrypt_once(n0)
                enc_notes = encrypt_value(plain_notes, self.fernet)
                if n0 != enc_notes:
                    b["notes"] = enc_notes
                    changed = True
                want_cat = key.split("/")[-1] if key else ""
                if b.get("category") != want_cat:
                    b["category"] = want_cat
                    changed = True
                for kf, vf in list(b["fields"].items()):
                    plain, ok = self._try_decrypt_once(vf)
                    if ok:
                        enc = encrypt_value(plain, self.fernet)
                        if enc != vf:
                            b["fields"][kf] = enc
                            changed = True
                if "notes" not in b:
                    b["notes"] = ""
                    changed = True
                else:
                    nval = b.get("notes", "")
                    if nval is None:
                        b["notes"] = ""
                        changed = True
                    else:
                        plain, ok = self._try_decrypt_once(nval)
                        if ok:
                            enc = encrypt_value(plain, self.fernet)
                            if enc != nval:
                                b["notes"] = enc
                                changed = True
                        else:
                            if not (isinstance(nval, str) and nval.startswith("gAAAA")):
                                enc = encrypt_value(str(nval), self.fernet)
                                if enc != nval:
                                    b["notes"] = enc
                                    changed = True

        if changed:
            secure_write_json(BLOCKS_FILE, blocks, self.fernet)
        return blocks
    def save_blocks(self):
        secure_write_json(BLOCKS_FILE, self.blocks_data, self.fernet)
    def load_trash(self):
        obj = secure_read_json(TRASH_FILE, self.fernet, [])
        return obj if isinstance(obj, list) else []
    def save_trash(self):
        secure_write_json(TRASH_FILE, self.trash, self.fernet)
    def rebuild_index(self):
        self.index.clear()
        self.id_to_ref.clear()
        for key, arr in self.blocks_data.items():
            for b in arr:
                self.id_to_ref[b["id"]] = (key, b)
                self.update_index_for_block(b)
        try:
            self.index.save()
        except Exception:
            pass
    def update_index_for_block(self, block):
        parts = [block.get("title", ""), block.get("category", "")]
        for k, v in (block.get("fields", {}) or {}).items():
            parts.append(k); parts.append(decrypt_value(v, self.fernet))
        notes_plain = decrypt_value(block.get("notes", ""), self.fernet)
        if notes_plain:
            parts.append(notes_plain)
        txt = "\n".join(parts)
        self.index.upsert(block["id"], txt)
    def remove_index_for_block(self, block):
        self.index.delete(block["id"])
    def _try_decrypt_once(self, v: str) -> tuple[str, bool]:
        if v is None:
            return "", False
        if not isinstance(v, str):
            v = str(v)
        try:
            return self.fernet.decrypt(v.encode("utf-8")).decode("utf-8"), True
        except Exception:
            return v, False
    def build_menu(self):
        mb = self.menuBar()
        filem = mb.addMenu("🗂️ Файл")
        a_save = QAction("💾 Сохранить", self)
        a_save.setShortcut("Ctrl+S")
        a_save.setToolTip("Сохранить все данные")
        a_save.triggered.connect(self.save_all)
        filem.addAction(a_save)
        filem.addSeparator()
        m_imp = QMenu("📥 Импорт", self)
        a_imp = QAction("📥 Импорт (обычный)", self)
        a_imp.setToolTip("Импорт из XLSX/CSV/JSON/TXT")
        a_imp.triggered.connect(self.import_data)
        m_imp.addAction(a_imp)
        a_imp_lpx = QAction("🔐 Импорт (шифр.)", self)
        a_imp_lpx.setToolTip("Импорт зашифрованного экспорта")
        a_imp_lpx.triggered.connect(self.import_paranoid_lpx1)
        m_imp.addAction(a_imp_lpx)
        filem.addMenu(m_imp)
        m_exp = QMenu("📤 Экспорт", self)
        a_exp_all = QAction("📤 Экспорт (обычный)", self)
        a_exp_all.setToolTip("Экспорт в XLSX/CSV/JSON/TXT/HTML")
        a_exp_all.triggered.connect(self.export_all)
        m_exp.addAction(a_exp_all)
        a_exp_lpx = QAction("🔐 Экспорт (шифр.)", self)
        a_exp_lpx.setToolTip("Зашифрованный экспорт")
        a_exp_lpx.triggered.connect(self.export_paranoid_lpx1)
        m_exp.addAction(a_exp_lpx)
        filem.addMenu(m_exp)
        m_bkp = QMenu("🗄️ Бэкап", self)
        a_bkp = QAction("🗄️ Создать бэкап (шифр.)", self)
        a_bkp.setToolTip("Создать зашифрованный бэкап")
        a_bkp.triggered.connect(self.backup_data_lpx1)
        m_bkp.addAction(a_bkp)
        a_rst = QAction("♻️ Восстановить из бэкапа", self)
        a_rst.setToolTip("Восстановить данные из бэкапа")
        a_rst.triggered.connect(self.restore_backup_unified)
        m_bkp.addAction(a_rst)
        filem.addMenu(m_bkp)
        filem.addSeparator()
        a_exit = QAction("🚪 Выход", self)
        a_exit.setShortcut("Ctrl+Q")
        a_exit.setToolTip("Закрыть программу")
        from PySide6.QtCore import QCoreApplication
        a_exit.triggered.connect(QCoreApplication.quit)
        filem.addAction(a_exit)
        sett = mb.addMenu("⚙️ Настройки")
        a_tools = QAction("🔐 Пароли и шифрование", self)
        a_tools.setToolTip("Генератор паролей, шифрование/дешифрование")
        a_tools.triggered.connect(self.open_password_tools)
        sett.addAction(a_tools)
        a_pwd = QAction("🗝️ Смена мастер‑пароля", self)
        a_pwd.setToolTip("Пере‑шифровка всех данных новым мастер‑паролем")
        a_pwd.triggered.connect(self.change_master_password)
        sett.addAction(a_pwd)
        sett.addSeparator()
        a_sf = QAction("🧠 Умные папки", self)
        a_sf.setToolTip("Управление умными папками")
        a_sf.triggered.connect(self.manage_smart_folders)
        sett.addAction(a_sf)
        a_tpl = QAction("🧩 Шаблоны блоков", self)
        a_tpl.setToolTip("Управление шаблонами полей")
        a_tpl.triggered.connect(self.manage_templates)
        sett.addAction(a_tpl)
        a_prs = QAction("🎛️ Пресеты экспорта", self)
        a_prs.setToolTip("Создать и настроить пресеты экспорта")
        a_prs.triggered.connect(self.manage_export_presets)
        sett.addAction(a_prs)
        a_tasks = QAction("⏱️ Экспорт по расписанию", self)
        a_tasks.setToolTip("Планировщик автоматического экспорта/бэкапа")
        a_tasks.triggered.connect(self.show_export_tasks)
        sett.addAction(a_tasks)
        binm = mb.addMenu("🗑️ Корзина")
        a_open_bin = QAction("🗑️ Открыть корзину", self)
        a_open_bin.setToolTip("Просмотр удалённых блоков")
        a_open_bin.triggered.connect(lambda: RecycleBinDialog(self).exec())
        binm.addAction(a_open_bin)
        a_rest_bin = QAction("♻️ Восстановить из корзины", self)
        a_rest_bin.setToolTip("Вернуть выбранный блок из корзины")
        a_rest_bin.triggered.connect(self.restore_from_trash)
        binm.addAction(a_rest_bin)
        a_clear_bin = QAction("🧹 Очистить корзину", self)
        a_clear_bin.setToolTip("Очистить корзину (с подтверждением)")
        a_clear_bin.triggered.connect(self.clear_trash)
        binm.addAction(a_clear_bin)
        binm.addSeparator()
        a_last = QAction("⏮️ Последний удалённый", self)
        a_last.setToolTip("Перейти к последнему удалённому блоку")
        def _open_last():
            try:
                if not self.trash:
                    custom_info(self, "Корзина", "Корзина пуста."); return
                last = self.trash[-1].get("block")
                if not last:
                    custom_info(self, "Корзина", "Нет данных блока."); return
                BlockEditorDialog(self, last).exec()
            except Exception as e:
                custom_warning(self, "Корзина", f"Ошибка открытия: {e}")
        a_last.triggered.connect(_open_last)
        binm.addAction(a_last)
        helpm = mb.addMenu("❓ Справка")
        a_manual = QAction("📖 Инструкция", self)
        a_manual.setToolTip("Краткое руководство пользователя")
        a_manual.triggered.connect(self.show_manual)
        helpm.addAction(a_manual)
        a_license = QAction("🧾 Лицензия", self)
        a_license.setToolTip("Правовые сведения и лицензии")
        a_license.triggered.connect(self.show_license)
        helpm.addAction(a_license)
        a_about = QAction("ℹ️ О программе", self)
        a_about.setToolTip("Сведения о версии и авторе")
        a_about.triggered.connect(lambda: custom_info(self, "О программе", PROGRAM_INFO))
        helpm.addAction(a_about)
        a_donate = QAction("💖 Поддержать проект", self)
        a_donate.setToolTip("Открыть страницу доната (YooMoney)")
        a_donate.triggered.connect(lambda: self.open_url_safe(QUrl("https://yoomoney.ru/to/410011663886937"), "Поддержать проект"))
        helpm.addAction(a_donate)
    from typing import Optional, cast
    def _b64url_pad(self, s: str) -> str:
        if not s:
            return ""
        return s + "=" * (-len(s) % 4)
    def _dec_paranoid_token(self, s: str) -> str:
        try:
            raw = base64.urlsafe_b64decode(self._b64url_pad(s))
            dec = self.fernet.decrypt(raw)
            return dec.decode("utf-8")
        except Exception as e:
            try:
                dec = self.fernet.decrypt((s or "").encode("utf-8"))
                return dec.decode("utf-8")
            except Exception:
                raise
    def import_paranoid_lpx1(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт (LPX1, параноидальный)", "",
            "LPX1 (*.lpx);;Все файлы (*.*)"
        )
        if not path:
            return
        pwd = self.ask_password("Пароль файла", "Введите пароль к .lpx:")
        if pwd is None:
            return
        try:
            data = _lpx_decrypt_bytes_or_file(path, pwd)
            try:
                payload = json.loads(data.decode("utf-8"))
            except Exception as e:
                raise ValueError(f"Не удалось разобрать JSON из файла: {e}")
            if not isinstance(payload, dict) or "items" not in payload or not isinstance(payload["items"], list):
                raise ValueError("Неверный формат: отсутствует список 'items'.")
            items = payload.get("items", [])
            if not items:
                custom_info(self, "Импорт", "В файле нет данных."); 
                return
            imported = 0
            created_paths: set[str] = set()
            for it in items:
                try:
                    k_enc = it.get("k", "")
                    t_enc = it.get("t", "")
                    f_list = it.get("f", [])
                    key = self._dec_paranoid_token(k_enc)
                    title = self._dec_paranoid_token(t_enc)
                    fields: dict[str, str] = {}
                    for pair in f_list or []:
                        if not isinstance(pair, list) or len(pair) != 2:
                            continue
                        name = self._dec_paranoid_token(pair[0])
                        value_plain = self._dec_paranoid_token(pair[1])
                        fields[name] = encrypt_value(value_plain, self.fernet)
                    if key:
                        self._ensure_tree_path(key)
                        created_paths.add(key)
                    block = {
                        "id": secrets.token_hex(12),
                        "title": title or "",
                        "category": key.split("/")[-1] if key else "",
                        "fields": fields,
                        "icon": ""
                    }
                    self.blocks_data.setdefault(key, []).append(block)
                    self.id_to_ref[block["id"]] = (key, block)
                    self.update_index_for_block(block)
                    imported += 1
                except InvalidToken:
                    raise RuntimeError(
                        "Файл расшифрован, но внутренние данные зашифрованы другим мастер‑ключом.\n"
                        "Импорт возможен только в той же установке с тем же master/auth.json, "
                        "с которым делался экспорт."
                    )
            if created_paths:
                self.save_tree()
                self.render_tree()
            self.save_blocks()
            self.schedule_render()
            custom_info(self, "Импорт", f"Готово. Импортировано блоков: {imported}.")
            audit_write("import_paranoid_lpx1", {
                "file": path,
                "items": imported,
                "sections_touched": len(created_paths)
            })
        except InvalidToken:
            custom_error(self, "Импорт", "Неверный пароль к LPX1 файлу.")
        except RuntimeError as e:
            custom_error(self, "Импорт", str(e))
        except Exception as e:
            custom_error(self, "Импорт", f"Ошибка: {e}")
    def apply_theme(self):
        try:
            t = self.theme = merge_theme(self.theme)
            r = int(t.get("btn_radius", 10))
            fg = t.get("btn_fg", "#FFFFFF")
            app_core = QCoreApplication.instance()
            if isinstance(app_core, QApplication):
                app_core.setStyleSheet(f"""
                    QMainWindow {{ background: {t['window_bg']}; }}
                    QMenuBar {{ background: {t['menubar_bg']}; color: {t['menubar_fg']}; }}
                    QMenuBar::item {{ background: transparent; color: {t['menubar_fg']}; padding:4px 10px; border-radius:{r}px; }}
                    QMenuBar::item:selected {{ background: rgba(0,0,0,0.08); }}
                    QMenu {{ background: {t['menu_bg']}; color: {t['menu_fg']}; }}
                    QMenu::item {{ padding:5px 12px; border-radius:{r}px; }}
                    QMenu::item:selected {{ background: rgba(0,0,0,0.08); }}
                    QWidget#leftPane {{ background: {t['left_bg']}; }}
                    QTreeWidget {{ background: {t['left_bg']}; color: {t['tree_fg']}; font-size:14px; }}
                    QTreeWidget::item {{ height:25px; }}
                    QScrollArea#kanbanScroll, QScrollArea#kanbanScroll > QWidget#qt_scrollarea_viewport {{ background: {t['kanban_bg']}; }}
                    QWidget#rightPane {{ background: {t['kanban_bg']}; }}
                    QPushButton {{
                        background: #F5F5F5;
                        color: #111;
                        border: 1px solid rgba(0,0,0,0.08);
                        border-radius: {r}px;
                        padding: 6px 12px;
                    }}
                    QPushButton:hover {{ background: #F0F0F0; }}
                    QPushButton:pressed {{ background: #E9E9E9; }}
                    QPushButton:disabled {{ color: #888; background: #F9F9F9; }}
                    QPushButton[minsize="compact"] {{
                        padding: 2px 6px;
                        min-width: 24px;
                        max-height: 24px;
                        border-radius: {int(r*0.7)}px;
                    }}

                    QFrame#BlockCard {{
                        background: {t['card_bg']};
                        border: 1px solid rgba(17, 24, 39, 0.06);
                        border-radius: 14px;
                    }}
                    QFrame#BlockCard:hover {{
                        border: 1px solid rgba(17, 24, 39, 0.12);
                        background: #FFFFFF;
                    }}

                """)
            if hasattr(self, "btn_add_section"):
                self.btn_add_section.setStyleSheet(f"background:{t['btn_add_section_bg']};color:{fg};border:none;border-radius:{r}px;")
            if hasattr(self, "btn_add_subsection"):
                self.btn_add_subsection.setStyleSheet(f"background:{t['btn_add_subsection_bg']};color:{fg};border:none;border-radius:{r}px;")
            if hasattr(self, "btn_del_section"):
                self.btn_del_section.setStyleSheet(f"background:{t['btn_delete_section_bg']};color:{fg};border:none;border-radius:{r}px;")
            self.render_dashboard()
        except Exception as e:
            custom_warning(self, "Тема", f"Не удалось применить тему: {e}")
    def reset_theme(self):
        self.theme = default_theme()
        self.meta["theme"] = self.theme
        self.save_meta()
        self.apply_theme()
    def build_ui(self):
        central = QWidget(self); central.setObjectName("central")
        self.setCentralWidget(central)
        lay = QHBoxLayout(central); lay.setContentsMargins(8, 8, 8, 8)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        lay.addWidget(self.splitter)
        left = QWidget(); left.setObjectName("leftPane")
        ll = QVBoxLayout(left); ll.setContentsMargins(6,6,6,6)
        self.tree = SectionTree(self)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(0, 220)
        self.tree.setHeaderLabel("Разделы")
        self.tree.blockDropped.connect(self.on_blocks_dropped_to_section)
        self.tree.itemClicked.connect(self.on_tree_item_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_context_menu)
        ll.addWidget(self.tree)
        row = QHBoxLayout()
        self.btn_add_section = QPushButton("+ Раздел"); self.btn_add_section.clicked.connect(self.create_item)
        self.btn_add_subsection = QPushButton("+ Подраздел"); self.btn_add_subsection.clicked.connect(self.create_subsection_by_sel)
        self.btn_del_section = QPushButton("Удалить"); self.btn_del_section.clicked.connect(self.delete_item_by_sel)
        row.addWidget(self.btn_add_section); row.addWidget(self.btn_add_subsection); row.addWidget(self.btn_del_section)
        ll.addLayout(row)
        self.splitter.addWidget(left)
        right = QWidget(); right.setObjectName("rightPane")
        rl = QVBoxLayout(right); rl.setContentsMargins(6,6,6,6)
        srow = QHBoxLayout()
        self.search_input = QLineEdit(); self.search_input.setPlaceholderText("Поиск…")
        self.search_timer = QTimer(self); self.search_timer.setSingleShot(True); self.search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self.search_input.textChanged.connect(lambda _=None: self.search_timer.start())
        self.search_timer.timeout.connect(self.render_dashboard)
        self.filter_box = QComboBox(); self.filter_box.addItems(["Блоки","Поля"])
        self.filter_box.currentIndexChanged.connect(lambda _: self.set_search_mode(self.filter_box.currentText()))
        srow.addWidget(QLabel("🔍")); srow.addWidget(self.search_input); srow.addWidget(self.filter_box)
        self.btn_clear = QPushButton("✖"); self.btn_clear.setFixedWidth(28); self.btn_clear.setProperty("minsize","compact")
        self.btn_clear.clicked.connect(lambda: self.search_input.setText(""))
        srow.addWidget(self.btn_clear)
        btn_sf = QPushButton("Умные папки"); btn_sf.clicked.connect(self.manage_smart_folders)
        btn_tpl = QPushButton("Шаблоны"); btn_tpl.clicked.connect(self.manage_templates)
        btn_prs = QPushButton("Пресеты"); btn_prs.clicked.connect(self.manage_export_presets)
        srow.addWidget(btn_sf); srow.addWidget(btn_tpl); srow.addWidget(btn_prs)
        self.btn_att_only = QPushButton("📎"); self.btn_att_only.setCheckable(True); self.btn_att_only.setProperty("minsize","compact")
        self.btn_att_only.setToolTip("Показывать только блоки с вложениями")
        self.btn_att_only.clicked.connect(self.render_dashboard)
        srow.addWidget(self.btn_att_only)
        self.btn_add_block = QPushButton("+ Блок"); self.btn_add_block.clicked.connect(self.add_block)
        srow.addWidget(self.btn_add_block)
        self.btn_toggle_data = QPushButton("Открыть данные")
        self.btn_toggle_data.setCheckable(True); self.btn_toggle_data.setChecked(False)
        self.btn_toggle_data.clicked.connect(self.toggle_data)
        srow.addWidget(self.btn_toggle_data)
        rl.addLayout(srow)
        self.kanban_area = QScrollArea(); self.kanban_area.setObjectName("kanbanScroll")
        self.kanban_area.setWidgetResizable(True)
        self.kanban_content = QWidget()
        self.kanban_layout = GridWrapLayout(self.kanban_content, margin=8, hSpacing=14, vSpacing=14, card_width=300)
        self.kanban_content.setLayout(self.kanban_layout)
        self.kanban_content.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)
        self.kanban_area.setWidget(self.kanban_content)
        self.kanban_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.kanban_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.kanban_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        rl.addWidget(self.kanban_area)
        self.splitter.addWidget(right)
        self.splitter.setSizes([330, 1050])
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        left.setMinimumWidth(300)
        act = QAction(self); act.setShortcut("Ctrl+R"); act.triggered.connect(self.create_item); self.addAction(act)
        act2= QAction(self); act2.setShortcut("Ctrl+P"); act2.triggered.connect(self.create_subsection_by_sel); self.addAction(act2)
        act3= QAction(self); act3.setShortcut("Ctrl+B"); act3.triggered.connect(self.add_block); self.addAction(act3)
        act4= QAction(self); act4.setShortcut("Ctrl+S"); act4.triggered.connect(lambda: self.search_input.setFocus()); self.addAction(act4)
        act_up = QAction(self); act_up.setShortcut("Alt+Up")
        act_up.triggered.connect(lambda: self.move_item_up(self.tree.currentItem()))
        self.addAction(act_up)
        act_dn = QAction(self); act_dn.setShortcut("Alt+Down")
        act_dn.triggered.connect(lambda: self.move_item_down(self.tree.currentItem()))
        self.addAction(act_dn)
    def init_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu()
        a_show = QAction("Показать", self);    a_show.triggered.connect(self.showNormal)
        a_lock = QAction("Заблокировать", self); a_lock.triggered.connect(self.auto_lock)
        a_exit = QAction("Выход", self);       a_exit.triggered.connect(self.close)
        menu.addAction(a_show)
        menu.addAction(a_lock)
        menu.addSeparator()
        menu.addAction(a_exit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip(APP_TITLE)
        self.tray.show()
    def schedule_temp_cleanup(self, path: str, after_sec: int = SHARE_TEMP_TTL_SEC, is_dir: bool | None = None) -> None:
        if is_dir is None:
            is_dir = os.path.isdir(path)
        def _cleanup():
            try:
                if is_dir and os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                elif os.path.isfile(path):
                    try: os.remove(path)
                    except Exception: pass
            finally:
                try:
                    self._temp_share_dirs.discard(path)
                except Exception:
                    pass
        QtCore.QTimer.singleShot(max(5, int(after_sec)) * 1000, _cleanup)
    def make_temp_share_dir(self, tag: str = "share") -> str:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        d = tempfile.mkdtemp(prefix=f"LinkPass_{tag}_{ts}_")
        self._temp_share_dirs.add(d)
        self.schedule_temp_cleanup(d, SHARE_TEMP_TTL_SEC, is_dir=True)
        return d
    def write_temp_text_for_send(self, text: str) -> str:
        out_dir = self.make_temp_share_dir("text")
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(out_dir, f"message_{ts}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write((text or "").replace("\r\n", "\n"))
        return path
    def archive_share_text_encrypted(self, text: str) -> str:
        return ""
    def open_password_tools(self):
        PasswordToolsDialog(self).exec()
    def show_text_dialog(self, title: str, text: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        if os.path.exists(ICON_PATH):
            dlg.setWindowIcon(QIcon(ICON_PATH))
        dlg.resize(780, 620)
        lay = QVBoxLayout(dlg)
        ed = QPlainTextEdit()
        ed.setReadOnly(True)
        ed.setPlainText(text)
        lay.addWidget(ed)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()
    def show_manual(self) -> None:
        self.show_text_dialog("Инструкция", MANUAL_TEXT)
    def show_license(self) -> None:
        self.show_text_dialog("Лицензия", LICENSE_TEXT)
    def ask_password(self, title, label, echo_password=True):
        dlg = PasswordDialog(title, label, echo_password=echo_password)
        return dlg.value() if dlg.exec() == QDialog.DialogCode.Accepted else None
    def schedule_render(self):
        self._render_timer.start(TREE_RENDER_DELAY_MS)
    def set_search_mode(self, m):
        self.search_mode = m
        self.render_dashboard()
    def current_key(self): return "/".join(self.current_path)
    def get_section_color(self, path):
        node = None; nodes = self.data_tree
        for name in path:
            node = next((n for n in nodes if n["name"] == name), None)
            if node: nodes = node.get("children", [])
            else: break
        return node.get("color", self.theme.get("tag_bg", "#f4e4ae")) if node else self.theme.get("tag_bg", "#f4e4ae")
    def get_all_paths(self):
        out=[]
        def rec(nodes, pref):
            for n in nodes:
                p = pref + [n["name"]]
                out.append("/".join(p))
                rec(n.get("children",[]), p)
        rec(self.data_tree, [])
        return out
    def _ensure_tree_path(self, section_path):
        if not section_path: return
        parts = section_path.split("/")
        nodes = self.data_tree
        for name in parts:
            found = next((n for n in nodes if n["name"] == name), None)
            if not found:
                found = {"name": name, "children": [], "color": self.theme.get("tag_bg","#f4e4ae")}
                nodes.append(found)
            nodes = found["children"]
    def find_node(self, path_list):
        nodes = self.data_tree; parent = None
        for name in path_list:
            parent = next((n for n in nodes if n["name"] == name), None)
            if not parent: return None, None
            nodes = parent.get("children", [])
        return parent, nodes
    def find_parent_and_index(self, path_list):
        if not path_list: return None, None, -1
        nodes = self.data_tree
        parent = None
        for i, name in enumerate(path_list):
            prev_nodes = nodes
            node = next((n for n in nodes if n["name"] == name), None)
            if node is None: return None, None, -1
            if i == len(path_list)-1:
                return prev_nodes, node, prev_nodes.index(node)
            nodes = node.get("children", [])
        return None, None, -1
    from typing import List, Optional, Tuple
    def _item_path(self, item: Optional[QTreeWidgetItem]) -> List[str]:
        path: List[str] = []
        cur = item
        while cur is not None and cur.text(0) != "🧠 Умные папки":
            path.insert(0, cur.text(0))
            cur = cur.parent()
        return path
    def _find_item_by_path(self, path_list: List[str]) -> Optional[QTreeWidgetItem]:
        if not path_list:
            return None
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it is None:
                continue
            if it.text(0) == "🧠 Умные папки":
                continue
            if it.text(0) == path_list[0]:
                cur = it
                for name in path_list[1:]:
                    found: Optional[QTreeWidgetItem] = None
                    for j in range(cur.childCount()):
                        ch = cur.child(j)
                        if ch is not None and ch.text(0) == name:
                            found = ch
                            break
                    if found is None:
                        return None
                    cur = found
                return cur
        return None
    def _select_path_in_tree(self, path_list: List[str]) -> None:
        it = self._find_item_by_path(path_list)
        if it is not None:
            self.tree.setCurrentItem(it)
    def _reorder_section(self, path_list, direction: int):
        parent_list, node, idx = self.find_parent_and_index(path_list)
        if node is None or parent_list is None or idx < 0:
            return
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(parent_list):
            return
        parent_list.pop(idx)
        parent_list.insert(new_idx, node)
        self.save_tree()
        self.render_tree()
        self._select_path_in_tree(path_list)
        self.schedule_render()
        audit_write("section_reorder", {
            "path": "/".join(path_list),
            "direction": "up" if direction < 0 else "down",
            "new_index": new_idx
        })
    def move_item_up(self, item):
        path = self._item_path(item)
        if path:
            self._reorder_section(path, -1)
    def move_item_down(self, item):
        path = self._item_path(item)
        if path:
            self._reorder_section(path, +1)
    def _apply_tree_item_colors(self):
        col = QColor(self.theme.get("tree_fg", "#202020"))
        def rec(it):
            it.setForeground(0, col)
            for i in range(it.childCount()):
                rec(it.child(i))
        for i in range(self.tree.topLevelItemCount()):
            rec(self.tree.topLevelItem(i))
    def render_tree(self) -> None:
        self.tree.clear()
        def norm_name(x: object) -> str:
            s = "" if x is None else str(x)
            s = s.replace("\ufeff", "").strip()
            return s if s else "(без названия)"
        def add_items(parent: QTreeWidgetItem, nodes: List[dict], prefix_path: List[str]) -> None:
            for n in nodes:
                n["name"] = norm_name(n.get("name", ""))
                n.setdefault("children", [])
                n.setdefault("color", self.theme.get("tag_bg", "#f4e4ae"))
                it = QTreeWidgetItem([n["name"]])
                it.setIcon(0, self._color_icon(n.get("color", self.theme.get("tag_bg", "#f4e4ae"))))
                it.setData(0, Qt.ItemDataRole.UserRole, ("section", "/".join(prefix_path + [n["name"]])))
                parent.addChild(it)
                add_items(it, n.get("children", []), prefix_path + [n["name"]])
        if self.meta.get("smart_folders"):
            root_sf = QTreeWidgetItem(["🧠 Умные папки"])
            self.tree.addTopLevelItem(root_sf)
            for sf in self.meta["smart_folders"]:
                it = QTreeWidgetItem([norm_name(sf.get("name", ""))])
                it.setData(0, Qt.ItemDataRole.UserRole, ("smart", sf))
                root_sf.addChild(it)
        for top in self.data_tree:
            top["name"] = norm_name(top.get("name", ""))
            top.setdefault("color", self.theme.get("tag_bg", "#f4e4ae"))
            it = QTreeWidgetItem([top["name"]])
            it.setIcon(0, self._color_icon(top["color"]))
            it.setData(0, Qt.ItemDataRole.UserRole, ("section", top["name"]))
            self.tree.addTopLevelItem(it)
            add_items(it, top.get("children", []), [top["name"]])
        try:
            self.tree.itemClicked.disconnect(self.on_tree_item_clicked)
        except Exception:
            pass
        self.tree.itemClicked.connect(self.on_tree_item_clicked)

        if DEFAULT_EXPAND_DEPTH < 0:
            self.tree.collapseAll()
        else:
            self.tree.expandToDepth(DEFAULT_EXPAND_DEPTH)
        col = QColor(self.theme.get("tree_fg", "#202020"))
        def rec_color(it: Optional[QTreeWidgetItem]):
            if it is None:
                return
            it.setForeground(0, col)
            for i in range(it.childCount()):
                rec_color(it.child(i))
        for i in range(self.tree.topLevelItemCount()):
            rec_color(self.tree.topLevelItem(i))
    def _contrast_text_for(self, bg: str) -> str:
        c = QColor(bg)
        if not c.isValid():
            c = QColor(self.theme.get("tag_bg", "#f4e4ae"))
        yiq = (299 * c.red() + 587 * c.green() + 114 * c.blue()) / 1000.0
        return "#FFFFFF" if yiq < 140 else "#000000"
    def _color_icon(self, color: str, size: int = 14, radius: int = 3) -> QIcon:
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        qc = QColor(color)
        if not qc.isValid():
            qc = QColor(self.theme.get("tag_bg", "#f4e4ae"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        p.setBrush(qc)
        rect = pm.rect().adjusted(1, 1, -1, -1)
        p.drawRoundedRect(rect, radius, radius)
        p.end()
        return QIcon(pm)
    def on_tree_item_clicked(self, item: Optional[QTreeWidgetItem], _col: int) -> None:
        if item is None:
            return
        kind = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(kind, tuple) and len(kind) > 0 and kind[0] == "smart":
            sf = kind[1] if len(kind) > 1 else {}
            name = sf.get("name", "") if isinstance(sf, dict) else ""
            self.current_path = ["__SMART__", name]
            self.schedule_render()
            return
        path: List[str] = []
        cur = item
        while cur is not None and cur.text(0) != "🧠 Умные папки":
            path.insert(0, cur.text(0))
            cur = cur.parent()
        self.current_path = path
        self.schedule_render()
    def tree_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self.tree.itemAt(pos)
        menu = QMenu()
        def _add_action(text: str, slot, png: str | None = None) -> QAction:
            act = QAction(text, self)
            if png:
                p = resource_path(f"icons/{png}")
                if os.path.exists(p):
                    act.setIcon(QIcon(p))
            act.triggered.connect(slot)
            menu.addAction(act)
            return act
        if item is None:
            _add_action("➕ Создать раздел", self.create_item, "add.png")
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return
        kind = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(kind, tuple) and len(kind) > 0 and kind[0] == "smart":
            _add_action("📂 Открыть результаты", lambda: self.on_tree_item_clicked(item, 0), "open.png")
            menu.addSeparator()
            _add_action("🧠 Умные папки…", self.manage_smart_folders, "smart.png")
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return
        _add_action("➕ Создать подраздел", lambda: self.create_subitem(item), "add.png")
        _add_action("✏️ Переименовать",     lambda: self.rename_item(item), "rename.png")
        _add_action("🗑️ Удалить",           lambda: self.delete_item(item), "delete.png")
        menu.addSeparator()
        _add_action("⬆️ Вверх",              lambda: self.move_item_up(item), "arrow_up.png")
        _add_action("⬇️ Вниз",               lambda: self.move_item_down(item), "arrow_down.png")
        menu.addSeparator()
        _add_action("🎨 Выбрать цвет",       lambda: self.set_section_color(item), "color.png")
        path_list = self._path_from_item(item)
        if self.is_path_locked(path_list):
            _add_action("🔓 Снять пароль",   lambda: self._menu_clear_lock(item), "unlock.png")
        else:
            _add_action("🔒 Установить пароль", lambda: self._menu_set_lock(item), "lock.png")
        menu.addSeparator()
        _add_action("📤 Экспортировать раздел…", lambda: self.export_section(item), "export.png")
        _add_action("📦 Переместить раздел…",    lambda: self.move_section_by_menu(item), "move.png")
        share_menu = menu.addMenu("📤 Поделиться…")
        p_tg = resource_path("icons/telegram.png");  p_wa = resource_path("icons/whatsapp.png"); p_ml = resource_path("icons/mail.png")
        def _section_payload() -> str:
            if not self.ensure_section_unlocked(path_list):
                return ""
            key = "/".join(path_list)
            texts = []
            for k, arr in self.blocks_data.items():
                if k == key or k.startswith(key + "/"):
                    for b in arr:
                        texts.append(self._format_block_share_text(b, reveal=True))
            return "\n\n".join(texts) if texts else "(пусто)"
        a_tg = QAction("Telegram", self)
        if os.path.exists(p_tg): a_tg.setIcon(QIcon(p_tg))
        a_tg.triggered.connect(lambda: self.share_text_telegram(_section_payload()))
        share_menu.addAction(a_tg)
        a_wa = QAction("WhatsApp", self)
        if os.path.exists(p_wa): a_wa.setIcon(QIcon(p_wa))
        a_wa.triggered.connect(lambda: self.share_text_whatsapp(_section_payload()))
        share_menu.addAction(a_wa)
        a_ml = QAction("Email", self)
        if os.path.exists(p_ml): a_ml.setIcon(QIcon(p_ml))
        a_ml.triggered.connect(lambda: self._share_email("Данные раздела", _section_payload(), None))
        share_menu.addAction(a_ml)
        menu.exec(self.tree.viewport().mapToGlobal(pos))
    def _path_from_item(self, item) -> list[str]:
        path = []
        cur = item
        while cur and cur.text(0) != "🧠 Умные папки":
            path.insert(0, cur.text(0)); cur = cur.parent()
        return path
    def _node_by_path(self, path_list: list[str]) -> dict | None:
        node, _ = self.find_node(path_list)
        return node
    def is_path_locked(self, path_list: list[str]) -> bool:
        n = self._node_by_path(path_list)
        return bool(n and n.get("lock"))
    def set_section_password(self, path_list: list[str]):
        if not path_list: return
        p1 = self.ask_password("Пароль раздела", f"Задайте пароль для «{'/'.join(path_list)}»:")
        if p1 is None or len(p1) < 4:
            custom_warning(self, "Пароль", "Минимум 4 символа."); return
        p2 = self.ask_password("Подтверждение", "Повторите пароль:")
        if p2 != p1:
            custom_warning(self, "Пароль", "Не совпадает."); return
        salt = rand_bytes(16)
        ver = hash_for_auth(p1, salt, prefer_argon=True)
        n = self._node_by_path(path_list)
        if n is not None:
            n["lock"] = {
                "salt": base64.b64encode(salt).decode("utf-8"),
                "verifier": ver,
                "kdf": "argon2id" if HAS_ARGON2 else "pbkdf2"
            }
            self.save_tree(); self.render_tree()
            custom_info(self, "Раздел", "Пароль установлен.")
    def clear_section_password(self, path_list: list[str]):
        if not path_list: return
        n = self._node_by_path(path_list)
        if not n or "lock" not in n:
            custom_info(self, "Раздел", "Пароль не установлен."); return
        if not self._verify_section_password(path_list):
            return
        n.pop("lock", None)
        self.unlocked_sections.discard("/".join(path_list))
        self.save_tree(); self.render_tree()
        custom_info(self, "Раздел", "Пароль снят.")
    def _verify_section_password(self, path_list: list[str]) -> bool:
        n = self._node_by_path(path_list)
        if not n or "lock" not in n:
            return True
        pwd = self.ask_password("Доступ к разделу", f"Пароль для «{'/'.join(path_list)}»:")
        if pwd is None: return False
        lock = n["lock"] or {}
        salt = base64.b64decode(lock.get("salt","") or b"")
        kdf = lock.get("kdf","argon2id")
        calc = hash_for_auth(pwd, salt, prefer_argon=(kdf=="argon2id"))
        if calc == lock.get("verifier"):
            self.unlocked_sections.add("/".join(path_list))
            return True
        custom_error(self, "Доступ", "Неверный пароль.")
        return False
    def ensure_section_unlocked(self, path_list: list[str]) -> bool:
        if not self.is_path_locked(path_list): return True
        key = "/".join(path_list)
        if key in self.unlocked_sections: return True
        return self._verify_section_password(path_list)
    def can_show_block_data(self, block) -> bool:
        if not self.show_data:
            return False
        ref = self.id_to_ref.get(block.get("id",""))
        key = ref[0] if ref else ""
        path_list = [p for p in key.split("/") if p]
        locked = self._locked_prefixes(path_list)
        if not locked:
            return True
        return all(p in self.unlocked_sections for p in locked)
    def _win_copy_files_to_clipboard(self, files: list[str]) -> None:
        class DROPFILES(ctypes.Structure):
            _fields_ = [
                ("pFiles", ctypes.c_uint32),
                ("pt_x", ctypes.c_long),
                ("pt_y", ctypes.c_long),
                ("fNC", ctypes.c_int32),
                ("fWide", ctypes.c_int32),
            ]
        if not files:
            raise ValueError("empty file list")
        files = [os.path.abspath(p) for p in files]
        files_w = ("\0".join(files) + "\0\0").encode("utf-16le")
        header = DROPFILES()
        header.pFiles = ctypes.sizeof(DROPFILES)
        header.pt_x = 0
        header.pt_y = 0
        header.fNC = 0
        header.fWide = 1
        total = ctypes.sizeof(DROPFILES) + len(files_w)
        GMEM_MOVEABLE = 0x0002
        GMEM_ZEROINIT = 0x0040
        GHND = GMEM_MOVEABLE | GMEM_ZEROINIT
        hglobal = ctypes.windll.kernel32.GlobalAlloc(GHND, total)
        if not hglobal:
            raise RuntimeError("GlobalAlloc failed")
        ptr = ctypes.windll.kernel32.GlobalLock(hglobal)
        if not ptr:
            ctypes.windll.kernel32.GlobalFree(hglobal)
            raise RuntimeError("GlobalLock failed")
        try:
            ctypes.memmove(ptr, ctypes.byref(header), ctypes.sizeof(DROPFILES))
            ctypes.memmove(ctypes.c_void_p(ptr + ctypes.sizeof(DROPFILES)), files_w, len(files_w))
        finally:
            ctypes.windll.kernel32.GlobalUnlock(hglobal)
        CF_HDROP = 15
        if ctypes.windll.user32.OpenClipboard(None) == 0:
            ctypes.windll.kernel32.GlobalFree(hglobal)
            raise RuntimeError("OpenClipboard failed")
        try:
            ctypes.windll.user32.EmptyClipboard()
            if ctypes.windll.user32.SetClipboardData(CF_HDROP, hglobal) == 0:
                ctypes.windll.kernel32.GlobalFree(hglobal)
                raise RuntimeError("SetClipboardData failed")
            hglobal = None
        finally:
            ctypes.windll.user32.CloseClipboard()
    def _win_focus_whatsapp(self) -> None:
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            EnumWindows = user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            GetWindowTextW = user32.GetWindowTextW
            GetWindowTextLengthW = user32.GetWindowTextLengthW
            IsWindowVisible = user32.IsWindowVisible
            SetForegroundWindow = user32.SetForegroundWindow
            ShowWindow = user32.ShowWindow
            SW_RESTORE = 9
            target = {"hwnd": None}
            def _enum_cb(hwnd, _lparam):
                try:
                    if not IsWindowVisible(hwnd):
                        return True
                    length = GetWindowTextLengthW(hwnd)
                    if length == 0:
                        return True
                    buf = ctypes.create_unicode_buffer(length + 1)
                    GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    if "WhatsApp" in title:
                        target["hwnd"] = hwnd
                        return False
                except Exception:
                    pass
                return True
            EnumWindows(EnumWindowsProc(_enum_cb), 0)
            if target["hwnd"]:
                ShowWindow(target["hwnd"], SW_RESTORE)
                SetForegroundWindow(target["hwnd"])
        except Exception:
            pass
    def _win_send_ctrl_v(self) -> None:
        try:
            CREATE_NO_WINDOW = 0x08000000
            ps = (
                'Add-Type -AssemblyName System.Windows.Forms; '
                '[System.Windows.Forms.SendKeys]::SendWait("^v"); '
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                creationflags=CREATE_NO_WINDOW
            )
        except Exception:
            pass
    def _menu_set_lock(self, item):
        path_list = self._path_from_item(item)
        self.set_section_password(path_list)
    def _menu_clear_lock(self, item):
        path_list = self._path_from_item(item)
        self.clear_section_password(path_list)
    def share_section_by_menu(self, item):
        path_list = self._path_from_item(item)
        if not self.ensure_section_unlocked(path_list):
            return
        key = "/".join(path_list)
        texts = []
        for k, arr in self.blocks_data.items():
            if k == key or k.startswith(key + "/"):
                for b in arr:
                    texts.append(self._format_block_share_text(b))
        payload = "\n\n".join(texts) if texts else "(пусто)"
        m = QMenu(self)
        ico_tg   = QIcon(resource_path("icons/telegram.png")) if os.path.exists(resource_path("icons/telegram.png")) else QIcon()
        ico_wa   = QIcon(resource_path("icons/whatsapp.png")) if os.path.exists(resource_path("icons/whatsapp.png")) else QIcon()
        ico_mail = QIcon(resource_path("icons/mail.png"))     if os.path.exists(resource_path("icons/mail.png"))     else QIcon()
        a_tg   = QAction(ico_tg,   "Telegram", self)
        a_wa   = QAction(ico_wa,   "WhatsApp", self)
        a_mail = QAction(ico_mail, "Email",    self)
        a_tg.triggered.connect(lambda: self.share_text_telegram(payload))
        a_wa.triggered.connect(lambda: self.share_text_whatsapp(payload))
        a_mail.triggered.connect(lambda: self._share_email("Данные раздела", payload, None))
        m.addAction(a_tg); m.addAction(a_wa); m.addAction(a_mail)
        m.exec(QCursor.pos())
    def _paranoid_export_json_bytes(self, scope_key: str | None = None) -> bytes:
        def enc(s: str) -> str:
            return base64.urlsafe_b64encode(self.fernet.encrypt((s or "").encode("utf-8"))).decode("utf-8").rstrip("=")
        items = []
        def push(k: str, b: dict):
            items.append({
                "k": enc(k),
                "t": enc(b.get("title","")),
                "f": [[enc(name), enc(decrypt_value(val, self.fernet))] for name, val in (b.get("fields") or {}).items()]
            })
        if scope_key:
            pref = scope_key + "/" if scope_key else ""
            for k, arr in self.blocks_data.items():
                if k == scope_key or k.startswith(pref):
                    for b in arr: push(k, b)
        else:
            for k, arr in self.blocks_data.items():
                for b in arr: push(k, b)
        payload = {"ver": CURRENT_VERSION, "ts": datetime.utcnow().isoformat()+"Z", "items": items}
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

    def export_paranoid_lpx1(self):
        pwd = self.ask_password("Экспорт (LPX1)", "Задайте пароль на экспорт:")
        if pwd is None or len(pwd) < 4:
            custom_warning(self, "Экспорт", "Короткий пароль.")
            return
        raw = self._paranoid_export_json_bytes(None)
        data = _lpx_encrypt_bytes(raw, pwd)

        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить экспорт (LPX1, все разделы)",
            "export_all.lpx", "LPX1 (*.lpx)"
        )
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(data)
            custom_info(self, "Экспорт", "Готово. Экспортированы все разделы и подразделы.")
            audit_write("export_lpx1", {"scope": "ALL", "file": path})
        except Exception as e:
            custom_error(self, "Экспорт", f"Ошибка: {e}")
    def backup_data_lpx1(self):
        pwd = self.ask_password("Бэкап (LPX1)", "Задайте пароль для шифрованного бэкапа:")
        if pwd is None or len(pwd) < 4:
            custom_warning(self, "Бэкап", "Пароль не задан или короткий.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить бэкап (LPX1)", "data_backup.lpx", "LPX1 (*.lpx)")
        if not path:
            return
        def _make_zip() -> bytes:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for fn in (TREE_FILE, BLOCKS_FILE, TRASH_FILE, META_FILE, MASTER_FILE, INDEX_DB):
                    if os.path.exists(fn):
                        z.write(fn, os.path.basename(fn))
                for root, _, files in os.walk(ATTACH_DIR):
                    for f in files:
                        fp = os.path.join(root, f)
                        arc = os.path.relpath(fp, DATA_DIR)
                        z.write(fp, arc)
            return buf.getvalue()
        def work():
            raw = _make_zip()
            enc = _lpx_encrypt_bytes(raw, pwd)
            with open(path, "wb") as f:
                f.write(enc)
            return path
        def done(p):
            custom_info(self, "Бэкап", "Зашифрованный бэкап создан.")
            audit_write("backup_lpx1", {"file": p})
        run_long_task(self, "Бэкап", work, done)
    def _make_full_backup_zip_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for fn in (TREE_FILE, BLOCKS_FILE, TRASH_FILE, META_FILE, MASTER_FILE, INDEX_DB):
                if os.path.exists(fn):
                    z.write(fn, os.path.basename(fn))
            for root, _, files in os.walk(ATTACH_DIR):
                for f in files:
                    fp = os.path.join(root, f)
                    arc = os.path.relpath(fp, DATA_DIR)
                    z.write(fp, arc)
        return buf.getvalue()
    def create_item(self):
        name, ok = QInputDialog.getText(self, "Новый раздел", "Название раздела:")
        if not (ok and name): return
        clean = name.replace("\ufeff", "").strip()
        if not clean: clean = "(без названия)"
        self.data_tree.append({"name": clean, "children": [], "color": self.theme.get("tag_bg", "#f4e4ae")})
        self.save_tree(); self.render_tree(); self.schedule_render()
        audit_write("create_section", {"name": clean})
    def create_subsection_by_sel(self):
        items = self.tree.selectedItems()
        if not items:
            custom_info(self, "Подраздел", "Выберите раздел.")
            return
        self.create_subitem(items[0])
    def create_subitem(self, parent_item):
        name, ok = QInputDialog.getText(self, "Новый подраздел", "Название подраздела:")
        if not (ok and name): return
        clean = name.replace("\ufeff", "").strip()
        if not clean: clean = "(без названия)"
        def rec(nodes, names):
            if not names:
                return nodes
            head = names[0]
            n = next((x for x in nodes if x["name"] == head), None)
            if not n:
                n = {"name": head, "children": [], "color": self.theme.get("tag_bg", "#f4e4ae")}
                nodes.append(n)
            if len(names) == 1:
                return n["children"]
            return rec(n["children"], names[1:])
        path = []
        cur = parent_item
        while cur and cur.text(0) != "🧠 Умные папки":
            path.insert(0, cur.text(0)); cur = cur.parent()
        children = rec(self.data_tree, path)
        children.append({"name": clean, "children": [], "color": self.theme.get("tag_bg", "#f4e4ae")})
        self.save_tree(); self.render_tree(); self.schedule_render()
        audit_write("create_subsection", {"parent": "/".join(path), "name": clean})
    def rename_item(self, item):
        old_path = []; cur = item
        while cur and cur.text(0) != "🧠 Умные папки":
            old_path.insert(0, cur.text(0)); cur = cur.parent()
        old_name = old_path[-1] if old_path else ""
        name, ok = QInputDialog.getText(self, "Переименовать", "Новое имя:", text=old_name)
        if not (ok and name): return
        clean = name.replace("\ufeff", "").strip()
        if not clean:
            custom_warning(self, "Переименование", "Имя не может быть пустым.")
            return
        parent_list, node, idx = self.find_parent_and_index(old_path)
        if node is None: return
        node["name"] = clean
        old_prefix = "/".join(old_path)
        new_prefix = "/".join(old_path[:-1] + [clean])
        self._rename_blocks_prefix(old_prefix, new_prefix)
        self.save_tree(); self.save_blocks()
        self.render_tree(); self.schedule_render()
        audit_write("rename_section", {"from": old_prefix, "to": new_prefix})
    def delete_item_by_sel(self):
        it = self.tree.currentItem()
        if it: self.delete_item(it)
    def delete_item(self, item):
        path = []; cur = item
        while cur and cur.text(0) != "🧠 Умные папки":
            path.insert(0, cur.text(0)); cur = cur.parent()
        if not path: return
        key_prefix = "/".join(path)
        res = custom_question(self, "Удалить раздел?",
            f"Удалить раздел «{key_prefix}» и все его подразделы и блоки?\n(Блоки будут перемещены в Корзину)")
        if not res: return
        def remove_from(nodes, names):
            if not names: return False
            head = names[0]
            for i, n in enumerate(nodes):
                if n["name"] == head:
                    if len(names) == 1:
                        nodes.pop(i); return True
                    return remove_from(n.get("children", []), names[1:])
            return False
        affected_keys = [k for k in list(self.blocks_data.keys()) if k == key_prefix or k.startswith(key_prefix + "/")]
        for k in affected_keys:
            for b in self.blocks_data.get(k, []):
                self.trash.append({"id": b["id"], "from_key": k, "block": b, "ts": datetime.utcnow().isoformat()+"Z"})
                self.remove_index_for_block(b)
            del self.blocks_data[k]
        self.save_trash()
        remove_from(self.data_tree, path)
        self.save_tree(); self.save_blocks()
        self.render_tree(); self.schedule_render()
        audit_write("delete_section", {"path": key_prefix, "moved_blocks": len(affected_keys)})
    def set_section_color(self, item):
        path = []
        cur = item
        while cur and cur.text(0) != "🧠 Умные папки":
            path.insert(0, cur.text(0)); cur = cur.parent()
        cur_color = self.get_section_color(path)
        qcol = QColorDialog.getColor(QColor(cur_color), self, "Цвет раздела")
        if not qcol.isValid():
            return
        color = qcol.name()
        def rec(nodes, names):
            if not names: return False
            head = names[0]
            for n in nodes:
                if n["name"] == head:
                    if len(names) == 1:
                        n["color"] = color; return True
                    return rec(n["children"], names[1:])
            return False
        rec(self.data_tree, path)
        self.save_tree(); self.render_tree(); self.schedule_render()
        audit_write("set_section_color", {"path": "/".join(path), "color": color})
    def move_section_by_menu(self, item):
        path = []; cur = item
        while cur and cur.text(0) != "🧠 Умные папки":
            path.insert(0, cur.text(0)); cur = cur.parent()
        if not path: return
        all_paths = ["(корень)"] + self.get_all_paths()
        forbidden_prefix = "/".join(path)
        target, ok = QInputDialog.getItem(self, "Переместить раздел", "Новый родитель:", all_paths, 0, False)
        if not (ok and target): return
        if target == "/".join(path[:-1]) or (target != "(корень)" and target.startswith(forbidden_prefix)):
            custom_warning(self, "Перемещение", "Некорректная цель.")
            return
        new_parent = "" if target == "(корень)" else target
        self._move_section(path, new_parent)
    from typing import List, Optional
    def _move_section(self, path_list: List[str], new_parent_path: str) -> None:
        if not path_list:
            return
        parent_list, node, idx = self.find_parent_and_index(path_list)
        if node is None or parent_list is None or idx < 0:
            return
        del parent_list[idx]
        def get_children_of(path_str: Optional[str]) -> List[dict]:
            if not path_str:
                return self.data_tree
            parts = path_str.split("/")
            n, _ = self.find_node(parts)
            if n is None:
                return self.data_tree
            return n.setdefault("children", [])
        children = get_children_of(new_parent_path)
        children.append(node)
        old_prefix = "/".join(path_list)
        new_prefix = (new_parent_path + "/" if new_parent_path else "") + path_list[-1]
        self._rename_blocks_prefix(old_prefix, new_prefix)
        self.save_tree()
        self.save_blocks()
        self.render_tree()
        self.schedule_render()
        audit_write("move_section", {"from": old_prefix, "to": new_prefix})
    def _rename_blocks_prefix(self, old_prefix, new_prefix):
        updates = {}
        for k in list(self.blocks_data.keys()):
            if k == old_prefix or k.startswith(old_prefix + "/"):
                suffix = k[len(old_prefix):]
                if suffix.startswith("/"): suffix = suffix[1:]
                newk = new_prefix + (("/" + suffix) if suffix else "")
                updates[k] = newk
        for k, nk in updates.items():
            self.blocks_data.setdefault(nk, []).extend(self.blocks_data[k])
            del self.blocks_data[k]
        for k, arr in self.blocks_data.items():
            for b in arr:
                b["category"] = k.split("/")[-1] if k else b.get("category","")
                self.id_to_ref[b["id"]] = (k, b)
                self.update_index_for_block(b)
    def render_dashboard(self):
        if getattr(self, "_rendering", False):
            return
        self._rendering = True
        _vpos = 0
        _hpos = 0

        try:
            try:
                if hasattr(self, "kanban_area") and self.kanban_area is not None:
                    _vpos = self.kanban_area.verticalScrollBar().value()
                    _hpos = self.kanban_area.horizontalScrollBar().value()
            except Exception:
                _vpos = 0
                _hpos = 0
            while True:
                item = self.kanban_layout.takeAt(0)
                if not item:
                    break
                w = item.widget()
                if w is not None:
                    w.deleteLater()
            t = self.theme
            r = int(t.get("btn_radius", 10))
            fg = t.get("btn_fg", "#FFF")
            self.btn_add_block.setStyleSheet(
                f"padding:6px 12px;background:{t['btn_add_section_bg']};"
                f"color:{fg};border:none;border-radius:{r}px;"
            )
            self.btn_toggle_data.setStyleSheet(
                f"padding:6px 12px;background:{t['btn_move_bg']};"
                f"color:{fg};border:none;border-radius:{r}px;"
            )
            self.kanban_content.setUpdatesEnabled(False)
            try:
                cp = self.current_path if isinstance(self.current_path, list) else []
                if cp and cp[0] == "__SMART__":
                    self.render_smart_folder()
                    return
                if cp:
                    locked_prefixes = self._locked_prefixes(cp)
                    if any(p not in self.unlocked_sections for p in locked_prefixes):
                        stub = QFrame()
                        sl = QVBoxLayout(stub)
                        lab = QLabel(f"🔒 Раздел «{'/'.join(cp)}» заблокирован.")
                        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
                        btn = QPushButton("Разблокировать")
                        btn.setProperty("minsize", "compact")
                        btn.clicked.connect(lambda: self.ensure_chain_unlocked(cp) and self.schedule_render())
                        sl.addWidget(lab)
                        sl.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
                        self.kanban_layout.addWidget(stub)
                        return
                key = self.current_key()
                blocks: list[dict] = []
                if key:
                    pref = key + "/"
                    for k in self.blocks_data.keys():
                        if k == key or k.startswith(pref):
                            blocks.extend(self.blocks_data.get(k, []))
                search = (self.search_input.text() or "").strip()
                mode = self.search_mode
                if search:
                    ids = set(self.index.search(search))
                    if mode == "Блоки":
                        blocks = [b for b in blocks if b.get("id") in ids]
                    else:
                        ql = search.lower()
                        blocks = [
                            b for b in blocks
                            if any(
                                ql in (k or "").lower()
                                or ql in (decrypt_value(v, self.fernet) or "").lower()
                                for k, v in (b.get("fields") or {}).items()
                            )
                        ]
                if self.btn_att_only.isChecked():
                    blocks = [b for b in blocks if attachments_count(b.get("id", "")) > 0]
                if not blocks:
                    self.kanban_layout.addWidget(QLabel("Нет данных"))
                    return
                total = len(blocks)
                truncated = False
                if total > MAX_CARDS:
                    blocks = blocks[:MAX_CARDS]
                    truncated = True
                for block in blocks:
                    ref = self.id_to_ref.get(block.get("id", ""))
                    blk_key = ref[0] if ref else key
                    blk_path = [p for p in (blk_key or "").split("/") if p]
                    blk_color = self.get_section_color(blk_path)
                    self.kanban_layout.addWidget(self.make_block_card(block, blk_color))
                if truncated:
                    info = QLabel(f"Показаны первые {MAX_CARDS} из {total}. Уточните поиск.")
                    self.kanban_layout.addWidget(info)
            finally:
                self.kanban_content.setUpdatesEnabled(True)
                try:
                    self.kanban_area.verticalScrollBar().setValue(_vpos)
                    self.kanban_area.horizontalScrollBar().setValue(_hpos)
                except Exception:
                    pass
        finally:
            self._rendering = False
    def render_smart_folder(self):
        sf_name = self.current_path[1] if len(self.current_path) > 1 else ""
        sf = next((x for x in (self.meta.get("smart_folders") or []) if x.get("name") == sf_name), None)
        if not sf:
            self.kanban_layout.addWidget(QLabel("Умная папка не найдена"))
            return
        query = (sf.get("query") or "").strip()
        scope = (sf.get("scope") or "").strip()
        mode  = sf.get("mode", "Поля")
        if scope and scope != "ALL":
            keys = [k for k in self.blocks_data.keys() if k == scope or k.startswith(scope + "/")]
        else:
            keys = list(self.blocks_data.keys())
        blocks: list[dict] = []
        if query:
            ids = set(self.index.search(query))
            ql = query.lower()
            for k in keys:
                for b in self.blocks_data.get(k, []):
                    if mode == "Блоки":
                        if b.get("id") in ids:
                            blocks.append(b)
                    else:
                        fs = b.get("fields") or {}
                        if any(
                            (ql in (name or "").lower()) or (ql in (decrypt_value(val, self.fernet) or "").lower())
                            for name, val in fs.items()
                        ):
                            blocks.append(b)
        else:
            for k in keys:
                blocks.extend(self.blocks_data.get(k, []))

        if self.btn_att_only.isChecked():
            blocks = [b for b in blocks if attachments_count(b.get("id", "")) > 0]

        if not blocks:
            self.kanban_layout.addWidget(QLabel("Нет результатов"))
            return
        total = len(blocks)
        truncated = False
        if total > MAX_CARDS:
            blocks = blocks[:MAX_CARDS]
            truncated = True
        for b in blocks:
            ref = self.id_to_ref.get(b.get("id", ""))
            blk_key = ref[0] if ref else ""
            blk_path = [p for p in (blk_key or "").split("/") if p]
            blk_color = self.get_section_color(blk_path)
            self.kanban_layout.addWidget(self.make_block_card(b, blk_color))
        if truncated:
            self.kanban_layout.addWidget(QLabel(f"Показаны первые {MAX_CARDS} из {total}. Уточните поиск."))
    def open_attachments(self, block):
        ref = self.id_to_ref.get(block.get("id","")); key = ref[0] if ref else ""
        path_list = [p for p in key.split("/") if p]
        if not self.ensure_chain_unlocked(path_list):
            return
        try:
            BlockEditorDialog(self, block, open_tab="attachments").exec()
        except Exception as e:
            custom_warning(self, "Вложения", f"Ошибка: {e}")
    def _build_qr_plaintext(self, block: dict) -> str:
        title = block.get("title", "") or "(без названия)"
        category = block.get("category", "") or ""
        lines = [f"{title} ({category})" if category else title]
        for k, v in (block.get("fields") or {}).items():
            plain = decrypt_value(v, self.fernet)
            lines.append(f"{k}: {plain}")
        notes_plain = decrypt_value(block.get("notes", ""), self.fernet)
        if (notes_plain or "").strip():
            lines.append("Заметки:")
            lines.append(notes_plain)
        return "\n".join(lines)
    def open_notes(self, block):
        ref = self.id_to_ref.get(block.get("id",""))
        key = ref[0] if ref else ""
        path_list = [p for p in (key or "").split("/") if p]
        if not self.ensure_chain_unlocked(path_list):
            return
        try:
            BlockEditorDialog(self, block, open_tab="notes").exec()
        except Exception as e:
            custom_warning(self, "Заметки", f"Ошибка: {e}")
    def make_block_card(self, block: dict, color: str):
        t = self.theme
        r = int(t.get("btn_radius", 10))
        card = QFrame()
        card.setObjectName("BlockCard")
        card.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        card.setFixedWidth(300)
        from PySide6.QtWidgets import QSizePolicy
        card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        vb = QVBoxLayout(card)
        vb.setContentsMargins(12, 12, 12, 12)
        vb.setSpacing(8)
        top = QHBoxLayout()
        title = QLabel(block.get("title", ""))
        title.setStyleSheet(
             f"font-weight:700; font-size:16px; letter-spacing:0.2px; color:{t['block_title_fg']};"
        )
        title.setWordWrap(True)
        top.addWidget(title)
        top_right_layout = QHBoxLayout()
        top_right_layout.setSpacing(6)
        tag = QLabel(block.get("category", ""))
        tag_fg = self._contrast_text_for(color)
        tag.setStyleSheet(
            f"background:{color}; color:{tag_fg}; "
            "font-size:11px; font-weight:600; "
            "border-radius:10px; padding:4px 8px; min-height:18px; max-height:18px;"
        )
        top_right_layout.addWidget(tag)
        att_n = attachments_count(block.get("id", ""))
        if att_n > 0:
            att_lbl = QLabel(f"📎 {att_n}")
            att_lbl.setToolTip(f"Вложений: {att_n}")
            att_lbl.setStyleSheet(
                f"background:{t['attach_badge_bg']}; color:{t['attach_badge_fg']}; "
                "font-size:11px; border-radius:7px; padding:5px; min-height:18px; max-height:18px;"
            )
            top_right_layout.addWidget(att_lbl)
        btn_share = QPushButton()
        share_ico = resource_path("icons/share.png")
        if os.path.exists(share_ico):
            btn_share.setIcon(QIcon(share_ico))
            btn_share.setIconSize(QtCore.QSize(16, 16))
        else:
            btn_share.setText("🔗")
        btn_share.setProperty("minsize", "compact")
        btn_share.setToolTip("Поделиться")
        btn_share.clicked.connect(lambda _, b=block: self.share_block(b))
        top_right_layout.addWidget(btn_share)
        try:
            btn_qr = QPushButton()
            qr_ico = resource_path("icons/qr.png")
            if os.path.exists(qr_ico):
                btn_qr.setIcon(QIcon(qr_ico))
                btn_qr.setIconSize(QtCore.QSize(16, 16))
            else:
                btn_qr.setText("QR")
            btn_qr.setProperty("minsize", "compact")
            btn_qr.setToolTip("Показать QR")
            btn_qr.clicked.connect(lambda _, b=block: self.show_block_qr(b))
            top_right_layout.addWidget(btn_qr)
        except Exception:
            pass
        ref = self.id_to_ref.get(block.get("id", ""))
        key = ref[0] if ref else ""
        path_list = [p for p in (key or "").split("/") if p]
        locked_prefixes = self._locked_prefixes(path_list)
        if any(p not in self.unlocked_sections for p in locked_prefixes):
            btn_unlock = QPushButton("🔓")
            btn_unlock.setProperty("minsize", "compact")
            btn_unlock.setToolTip("Разблокировать раздел для показа данных")
            def _do_unlock():
                if self.ensure_section_unlocked(path_list):
                    self.schedule_render()
            btn_unlock.clicked.connect(_do_unlock)
            top_right_layout.addWidget(btn_unlock)
        top.addLayout(top_right_layout)
        vb.addLayout(top)
        fields = (block.get("fields") or {})
        can_show = self.can_show_block_data(block)
        items = list(fields.items())
        limit = CARD_FIELDS_LIMIT if isinstance(CARD_FIELDS_LIMIT, int) and CARD_FIELDS_LIMIT > 0 else 6
        shown_cnt = 0
        for (k, vv) in items[:limit]:
            row = QHBoxLayout()
            lab = QLabel(str(k if k is not None else "").rstrip(":") + ":")
            lab.setStyleSheet(
                f"font-size:12px; font-weight:600; margin-right:6px; min-width:50px; color:{t['field_label_fg']};"
            )
            row.addWidget(lab)
            if can_show:
                val_plain = decrypt_value(vv, self.fernet)
                txt = val_plain
            else:
                val_plain = None
                txt = mask_text("")
            edit = QLineEdit(txt)
            edit.setReadOnly(True)
            edit.setStyleSheet(
                "QLineEdit {"
                "  background:#FAFAFB; border:1px solid #ECECEE; border-radius:8px;"
                "  padding:6px 8px; font-size:13px;"
                f"  color:{t['field_text_fg']};"
                "}"
            )
            row.addWidget(edit)
            if can_show and val_plain and (is_url(val_plain) or is_email_addr(val_plain)):
                btn_go = QPushButton("↗")
                btn_go.setFixedWidth(28)
                btn_go.setProperty("minsize", "compact")
                btn_go.clicked.connect(lambda _=False, url=val_plain: QDesktopServices.openUrl(to_qurl_from_text(url)))
                row.addWidget(btn_go)
            btn_copy = QPushButton("🗐")
            btn_copy.setFixedWidth(28)
            btn_copy.setProperty("minsize", "compact")
            def _copy_now(_checked=False, enc_val=vv, pl=tuple(path_list)):
                if not self.ensure_chain_unlocked(list(pl)):
                    return
                try:
                    plain = decrypt_value(enc_val, self.fernet)
                except Exception:
                    plain = ""
                QApplication.clipboard().setText(str(plain))
                self.clip_timer.start(self.CLIPBOARD_SEC * 1000)
            btn_copy.clicked.connect(_copy_now)
            row.addWidget(btn_copy)
            vb.addLayout(row)
            shown_cnt += 1
        rest = max(0, len(items) - shown_cnt)
        if rest > 0:
            more = QLabel(f"… и ещё {rest} полей")
            more.setStyleSheet("font-size:12px; color:#666; margin-top:4px;")
            vb.addWidget(more)
        rowb = QHBoxLayout()
        def _btn_grey(text: str, tooltip: str, handler):
            b = QPushButton(text)
            b.setToolTip(tooltip)
            b.setStyleSheet(
                "padding:6px 10px; font-size:13px;"
                "background:transparent; color:#1F2937;"
                "border:1px solid #E6E7EA; border-radius:%dpx;"
                "QPushButton:hover { background:#F7F8FA; }" % r
            )
            b.clicked.connect(handler)
            return b
        b_open  = _btn_grey("🔍", "Открыть блок",       lambda _, b=block: self.safe_open_block_editor(b))
        b_move  = _btn_grey("↔️", "Переместить блок",   lambda _, b=block: self.move_block_dialog(b))
        b_files = _btn_grey("📁", "Открыть вложения",   lambda _, b=block: self.open_attachments(b))
        b_notes = _btn_grey("📝", "Открыть заметки",    lambda _, b=block: self.open_notes(b))
        b_del   = _btn_grey("🗑️", "Удалить блок",       lambda _, b=block: self.delete_block_soft(b))
        rowb.addWidget(b_open)
        rowb.addWidget(b_move)
        rowb.addWidget(b_files)
        rowb.addWidget(b_notes)
        rowb.addStretch(1)
        rowb.addWidget(b_del)
        vb.addLayout(rowb)
        def _drag_start(event, bid=block["id"]):
            if event.button() != Qt.MouseButton.LeftButton:
                return
            mime = QtCore.QMimeData()
            mime.setData("application/x-linkpass-block-id", bid.encode("utf-8"))
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.MoveAction)
        card.mousePressEvent = _drag_start
        return card
    def _format_block_share_text(self, block, reveal: bool = False) -> str:
        parts = [f"🔐 {block.get('title','')} ({block.get('category','')})"]
        can_show = reveal or self.can_show_block_data(block)
        for k, v in (block.get("fields") or {}).items():
            val = decrypt_value(v, self.fernet) if can_show else "[скрыто]"
            parts.append(f"{k}: {val}")
        return "\n".join(parts)
    def _guess_telegram_exe(self) -> str | None:
        candidates = [
            os.path.join(os.environ.get("APPDATA",""), "Telegram Desktop", "Telegram.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA",""), "Programs", "Telegram Desktop", "Telegram.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA",""), "Telegram Desktop", "Telegram.exe"),
            "Telegram.exe",
        ]
        for p in candidates:
            try:
                if p and os.path.isfile(p):
                    return p
            except Exception:
                pass
        return None
    def share_files_telegram(self, files: list[str]) -> None:
        files = [f for f in files if f and os.path.isfile(f)]
        if not files:
            custom_warning(self, "Telegram", "Нет файлов для отправки.")
            return
        exe = self._guess_telegram_exe()
        if exe:
            try:
                for f in files:
                    subprocess.Popen([exe, "-sendpath", f])
                custom_info(self, "Telegram", "Открыл Telegram и передал файлы. Выберите чат и отправьте.")
                return
            except Exception as e:
                custom_warning(self, "Telegram", f"Не удалось передать напрямую: {e}")
        try:
            self.open_url_safe(QUrl("tg://"), "Telegram")
        except Exception:
            pass
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(files[0])))
        except Exception:
            pass
        custom_info(self, "Telegram", "Если не появилось окно выбора, перетащите файлы из открытой папки в окно чата Telegram.")
    def _guess_whatsapp_exe(self) -> str | None:
        candidates = [
            os.path.join(os.environ.get("LOCALAPPDATA",""), "WhatsApp", "WhatsApp.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA",""), "Programs", "WhatsApp", "WhatsApp.exe"),
            "WhatsApp.exe",
        ]
        for p in candidates:
            try:
                if p and os.path.isfile(p):
                    return p
            except Exception:
                pass
        return None
    def share_files_whatsapp(self, files: list[str]) -> None:
        files = [os.path.abspath(f) for f in files if f and os.path.isfile(f)]
        if not files:
            custom_warning(self, "WhatsApp", "Нет файлов для отправки.")
            return
        opened = False
        for url in (
            "whatsapp://send?text=%20",
            "whatsapp://send",
            "https://wa.me/?text=%20",
            "https://web.whatsapp.com/send?text=%20"
        ):
            try:
                if QDesktopServices.openUrl(QUrl(url)):
                    opened = True
                    break
            except Exception:
                pass
        try:
            folder = os.path.dirname(files[0])
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        except Exception:
            pass
        custom_info(
            self,
            "WhatsApp",
            "Открыл WhatsApp/Web. Если окно выбора не появилось, перетащите файлы из папки в чат."
        )
    def share_files_email(self, block: dict, files: list[str]) -> None:
        self._share_email(None, None, None)
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(files[0])))
        except Exception:
            pass
    def _open_url_any(self, url: str) -> bool:
        qurl = QUrl.fromUserInput(url)
        ok = QDesktopServices.openUrl(qurl)
        if ok:
            return True
        try:
            import webbrowser
            return webbrowser.open(url)
        except Exception:
            return False
    def _share_telegram(self, text: str) -> None:
        from urllib.parse import quote_plus
        payload = quote_plus((text or "").replace("\r\n", "\n"))
        for url in (f"https://t.me/share/url?text={payload}", f"tg://msg?text={payload}"):
            if self._open_url_any(url):
                return
        QApplication.clipboard().setText(text or "")
        custom_info(self, "Поделиться", "Не удалось открыть Telegram. Текст скопирован в буфер обмена.")
    def share_text_whatsapp(self, text: str) -> None:
        text = (text or "(пусто)").replace("\r\n", "\n")
        MAX_DEEPLINK = 2000
        MAX_WEB      = 1000
        def _try(qurl: QUrl) -> bool:
            try:
                return QDesktopServices.openUrl(qurl)
            except Exception:
                return False
        if len(text) <= MAX_DEEPLINK:
            u = QUrl("whatsapp://send")
            q = QUrlQuery()
            q.addQueryItem("text", text)
            u.setQuery(q)
            if _try(u):
                return
        if len(text) <= MAX_WEB:
            u = QUrl("https://web.whatsapp.com/send")
            q = QUrlQuery()
            q.addQueryItem("text", text)
            u.setQuery(q)
            self.open_url_safe(u, "Поделиться в WhatsApp")
            return
        if len(text) <= MAX_WEB:
            u = QUrl("https://wa.me/")
            q = QUrlQuery()
            q.addQueryItem("text", text)
            u.setQuery(q)
            self.open_url_safe(u, "Поделиться в WhatsApp")
            return
        QApplication.clipboard().setText(text)
        custom_info(self, "WhatsApp",
                    "Текст скопирован в буфер обмена.\nОткройте WhatsApp и вставьте (Ctrl+V).")
        exe = self._guess_whatsapp_exe()
        if exe:
            try:
                subprocess.Popen([exe])
            except Exception:
                pass
    def _share_whatsapp(self, text: str) -> None:
        self.share_text_whatsapp(text)
    def _share_email(self,
                    subject: str | None,
                    body: str | None,
                    recipients: list[str] | None = None) -> None:
        to = ",".join(recipients or [])
        u = QUrl(f"mailto:{to}")
        q = QUrlQuery()
        if subject:
            q.addQueryItem("subject", subject)
        if body:
            q.addQueryItem("body", (body or "").replace("\r\n", "\n"))
        u.setQuery(q)
        ok = False
        try:
            ok = QDesktopServices.openUrl(u)
        except Exception:
            ok = False
        if not ok:
            custom_warning(self, "Почта", "Не удалось открыть почтовый клиент. Откройте папку и прикрепите файлы вручную.")
    def share_block(self, block):
        ref = self.id_to_ref.get(block.get("id","")); key = ref[0] if ref else ""
        path_list = [p for p in key.split("/") if p]
        if not self.ensure_chain_unlocked(path_list):
            return
        text = self._format_block_share_text(block, reveal=True)
        m = QMenu(self)
        ico_tg   = QIcon(resource_path("icons/telegram.png")) if os.path.exists(resource_path("icons/telegram.png")) else QIcon()
        ico_wa   = QIcon(resource_path("icons/whatsapp.png")) if os.path.exists(resource_path("icons/whatsapp.png")) else QIcon()
        ico_mail = QIcon(resource_path("icons/mail.png"))     if os.path.exists(resource_path("icons/mail.png"))     else QIcon()
        a_tg   = QAction(ico_tg,   "Telegram", self)
        a_wa   = QAction(ico_wa,   "WhatsApp", self)
        a_mail = QAction(ico_mail, "Email",    self)
        a_tg.triggered.connect(lambda: self.share_text_telegram_appfirst(text))
        a_wa.triggered.connect(lambda: self.share_text_whatsapp(text))
        a_mail.triggered.connect(lambda: self._share_email(f"Данные: {block.get('title','')}", text, None))
        m.addAction(a_tg); m.addAction(a_wa); m.addAction(a_mail)
        m.exec(QCursor.pos())
    def safe_open_block_editor(self, block):
        ref = self.id_to_ref.get(block.get("id","")); key = ref[0] if ref else ""
        path_list = [p for p in key.split("/") if p]
        if not self.ensure_chain_unlocked(path_list):
            return
        prev_show = self.show_data
        try:
            if not self.show_data:
                self.show_data = True
                if hasattr(self, "btn_toggle_data"):
                    self.btn_toggle_data.setChecked(True)
                    self.btn_toggle_data.setText("Скрыть данные")
            BlockEditorDialog(self, block).exec()
        except Exception as e:
            custom_warning(self, "Открыть", f"Ошибка: {e}")
        finally:
            if not prev_show:
                self.show_data = False
                if hasattr(self, "btn_toggle_data"):
                    self.btn_toggle_data.setChecked(False)
                    self.btn_toggle_data.setText("Открыть данные")
            self.schedule_render()
    def on_blocks_dropped_to_section(self, target_path, block_ids):
        for bid in block_ids:
            ref = self.id_to_ref.get(bid)
            if not ref: continue
            from_key, block = ref
            if from_key == target_path: continue
            self.blocks_data.setdefault(target_path, []).append(block)
            self.blocks_data[from_key] = [b for b in self.blocks_data[from_key] if b is not block]
            if not self.blocks_data[from_key]: del self.blocks_data[from_key]
            block["category"] = target_path.split("/")[-1] if target_path else block.get("category","")
            self.id_to_ref[bid] = (target_path, block)
            self.update_index_for_block(block)
            audit_write("move_block", {"block_id": bid, "from": from_key, "to": target_path})
        self.save_blocks()
        self.schedule_render()
    def add_block(self):
        key = self.current_key()
        if not key:
            custom_warning(self, "Внимание", "Сначала выберите раздел.")
            return
        title, ok = QInputDialog.getText(self, "Новый блок", "Название блока:")
        if not (ok and title): return
        block = {"id": secrets.token_hex(12), "title": title.strip(), "category": key.split("/")[-1], "fields": {}, "icon": ""}
        choices = ["(без шаблона)", "Учётка (URL, Логин, Пароль)", "Банк (Номер карты, Срок, CVC)"]
        user_tpls = self.meta.get("templates") or []
        choices += [f"Шаблон: {t['name']}" for t in user_tpls]
        sel, ok2 = QInputDialog.getItem(self, "Шаблон", "Выберите шаблон:", choices, 0, False)
        if ok2 and sel and sel != "(без шаблона)":
            if sel.startswith("Шаблон: "):
                name = sel.split(": ",1)[1]
                t = next((x for x in user_tpls if x["name"] == name), None)
                if t:
                    for f in t.get("fields", []):
                        block["fields"][f] = encrypt_value("", self.fernet)
            elif sel.startswith("Учётка"):
                for f in ("URL", "Логин", "Пароль"):
                    block["fields"][f] = encrypt_value("", self.fernet)
            elif sel.startswith("Банк"):
                for f in ("Номер карты", "Срок", "CVC"):
                    block["fields"][f] = encrypt_value("", self.fernet)
        self.blocks_data.setdefault(key, []).append(block)
        self.id_to_ref[block["id"]] = (key, block)
        self.update_index_for_block(block)
        self.save_blocks()
        self.schedule_render()
        audit_write("add_block", {"key": key, "block_id": block["id"], "title": block["title"]})
    def move_block_dialog(self, block):
        paths = self.get_all_paths()
        target, ok = QInputDialog.getItem(self, "Переместить блок", "Раздел:", paths, 0, True)
        if not (ok and target): return
        self.move_block(block, target)
    def move_block(self, block, target):
        old_key, _ = self.id_to_ref.get(block["id"], (None, None))
        if not old_key: return
        self.blocks_data.setdefault(target, []).append(block)
        self.blocks_data[old_key] = [b for b in self.blocks_data[old_key] if b is not block]
        if not self.blocks_data[old_key]:
            del self.blocks_data[old_key]
        block["category"] = target.split("/")[-1] if target else block.get("category", "")
        self.id_to_ref[block["id"]] = (target, block)
        self.update_index_for_block(block)
        self.save_blocks()
        self.schedule_render()
        audit_write("move_block", {"block_id": block["id"], "from": old_key, "to": target})
    def delete_block_soft(self, block):
        key, _ = self.id_to_ref.get(block["id"], (None, None))
        if not key:
            return
        if not custom_question(self, "Удалить блок?",
                               f"Блок «{block.get('title','(без названия)')}» будет перемещён в Корзину. Продолжить?"):
            return
        self.blocks_data[key] = [b for b in self.blocks_data[key] if b is not block]
        if not self.blocks_data[key]:
            del self.blocks_data[key]
        self.trash.append({
            "id": block["id"],
            "from_key": key,
            "block": block,
            "ts": datetime.utcnow().isoformat() + "Z"
        })
        self.remove_index_for_block(block)
        self.save_blocks()
        self.save_trash()
        self.schedule_render()
        audit_write("trash_move", {"block_id": block["id"], "from": key})
    def restore_from_trash(self):
        if not self.trash:
            custom_info(self, "Корзина", "Корзина пуста.")
            return
        items = []
        for t in self.trash:
            b = t.get("block", {})
            title = b.get("title", "(без названия)")
            frm = t.get("from_key", "")
            ts = t.get("ts", "")
            short = t.get("id", "")[:6]
            items.append(f"{title} — из {frm} — {ts} — {short}")
        sel, ok = QInputDialog.getItem(self, "Восстановление", "Выберите блок:", items, 0, False)
        if not (ok and sel):
            return
        idx = items.index(sel)
        it = self.trash[idx]
        key = it.get("from_key", "")
        b = it["block"]
        if key:
            self._ensure_tree_path(key)
            self.save_tree()
            self.render_tree()
        self.blocks_data.setdefault(key, []).append(b)
        self.id_to_ref[b["id"]] = (key, b)
        self.update_index_for_block(b)
        self.trash.remove(it)
        self.save_blocks()
        self.save_trash()
        self.current_path = [p for p in key.split("/") if p]
        self.schedule_render()
        audit_write("trash_restore", {"block_id": b["id"], "to": key})
    def clear_trash(self):
        if not self.trash:
            custom_info(self, "Корзина", "Корзина уже пуста.")
            return
        if not custom_question(self, "Очистить корзину",
                               "Удалить все элементы из корзины безвозвратно?"):
            return
        if not self.verify_master_prompt("Подтверждение очистки", "Введите мастер-пароль для очистки корзины:"):
            return
        cnt = len(self.trash)
        self.trash.clear()
        self.save_trash()
        custom_info(self, "Корзина", f"Удалено: {cnt}")
        audit_write("trash_clear", {"count": cnt})
    def on_block_changed(self, block, meta=None):
        for k, v in list(block.get("fields", {}).items()):
            plain, ok = self._try_decrypt_once(v)
            if ok:
                block["fields"][k] = encrypt_value(plain, self.fernet)
            else:
                block["fields"][k] = v
        self.update_index_for_block(block)
        self.save_blocks()
        audit_write("block_changed", {"block_id": block["id"], **(meta or {})})
    def toggle_data(self):
        want = self.btn_toggle_data.isChecked()
        if want:
            path = self.current_path or []
            if not self.ensure_chain_unlocked(path):
                self.btn_toggle_data.setChecked(False)
                return
        self.show_data = want
        self.btn_toggle_data.setText("Скрыть данные" if self.show_data else "Открыть данные")
        self.schedule_render()
    def open_url_safe(self, url: QUrl, title: str = "Открыть ссылку"):
        try:
            ok = QDesktopServices.openUrl(url)
            if not ok:
                custom_warning(self, title, "Система отклонила запрос на открытие ссылки.")
        except Exception as e:
            custom_error(self, title, f"Ошибка открытия ссылки:\n{e}")
    def share_text_telegram(self, text: str) -> None:
        try:
            text = (text or "(пусто)").replace("\r\n", "\n")
            first_url = None
            try:
                tokens = re.split(r"\s+", text)
                for tok in tokens:
                    if is_url(tok):
                        first_url = tok if tok.lower().startswith(("http://", "https://")) else ("https://" + tok)
                        break
            except Exception:
                first_url = None
            u = QUrl("https://t.me/share/url")
            q = QUrlQuery()
            if first_url:
                q.addQueryItem("url", first_url)
                rest = text.replace(first_url, "", 1).strip()
                if rest:
                    q.addQueryItem("text", rest)
            else:
                q.addQueryItem("text", text)
            u.setQuery(q)
            try:
                self.open_url_safe(u, "Поделиться в Telegram")
                self.archive_share_text_encrypted(text)
                return
            except Exception:
                pass
            MAX_DEEPLINK = 2000
            if len(text) <= MAX_DEEPLINK:
                for scheme in ("tg://share", "tg://msg", "tg://msg_url"):
                    u2 = QUrl(scheme)
                    q2 = QUrlQuery()
                    q2.addQueryItem("text", text)
                    u2.setQuery(q2)
                    try:
                        if QDesktopServices.openUrl(u2):
                            self.archive_share_text_encrypted(text)
                            return
                    except Exception:
                        pass
            try:
                tmp_txt = self.write_temp_text_for_send(text)
                self.archive_share_text_encrypted(text)
                self.share_files_telegram([tmp_txt])
                return
            except Exception:
                pass
            QApplication.clipboard().setText(text)
            custom_info(self, "Telegram", "Текст скопирован в буфер обмена.\nОткройте Telegram и вставьте (Ctrl+V).")
            self.open_url_safe(QUrl("tg://"), "Telegram")
        except Exception as e:
            QApplication.clipboard().setText(text or "")
            custom_warning(self, "Telegram", f"Не удалось открыть Telegram: {e}\nТекст скопирован в буфер обмена.")
    def share_text_telegram_appfirst(self, text: str) -> None:
        try:
            text = (text or "(пусто)").replace("\r\n", "\n")
            try:
                tmp_txt = self.write_temp_text_for_send(text)
                self.archive_share_text_encrypted(text)
                self.share_files_telegram([tmp_txt])
                return
            except Exception:
                pass
            MAX_DEEPLINK = 2000
            if len(text) <= MAX_DEEPLINK:
                for scheme in ("tg://share", "tg://msg", "tg://msg_url"):
                    u = QUrl(scheme)
                    q = QUrlQuery()
                    q.addQueryItem("text", text)
                    u.setQuery(q)
                    try:
                        if QDesktopServices.openUrl(u):
                            self.archive_share_text_encrypted(text)
                            return
                    except Exception:
                        pass
            try:
                u = QUrl("https://t.me/share/url")
                q = QUrlQuery()
                q.addQueryItem("text", text)
                u.setQuery(q)
                self.open_url_safe(u, "Поделиться в Telegram")
                self.archive_share_text_encrypted(text)
                return
            except Exception:
                pass
            QApplication.clipboard().setText(text)
            custom_info(self, "Telegram", "Текст скопирован в буфер обмена.\nОткройте Telegram и вставьте (Ctrl+V).")
            self.open_url_safe(QUrl("tg://"), "Telegram")
        except Exception as e:
            QApplication.clipboard().setText(text or "")
            custom_warning(self, "Telegram", f"Не удалось открыть Telegram: {e}\nТекст скопирован в буфер обмена.")
    def change_master_password(self):
        p1 = self.ask_password("Новый мастер-пароль", "Введите новый мастер-пароль:")
        if p1 is None:
            return
        if len(p1) < 6:
            custom_warning(self, "Пароль", "Минимальная длина — 6 символов.")
            return
        p2 = self.ask_password("Подтверждение", "Повторите новый мастер-пароль:")
        if p2 is None or p1 != p2:
            custom_warning(self, "Пароль", "Пароли не совпадают.")
            return
        try:
            old_fernet = self.fernet
            new_key_salt = rand_bytes(16)
            new_auth_salt = rand_bytes(16)
            new_fernet = make_fernet(p1, new_key_salt,
                         kdf_name=self.kdf_name, params=self.kdf_params)
            for arr in self.blocks_data.values():
                for b in arr:
                    for kf, vf in list(b.get("fields", {}).items()):
                        plain = decrypt_value(vf, old_fernet)
                        b["fields"][kf] = new_fernet.encrypt(plain.encode("utf-8")).decode("utf-8")
            for bid in os.listdir(ATTACH_DIR):
                d = os.path.join(ATTACH_DIR, bid)
                if not os.path.isdir(d):
                    continue
                for fn in os.listdir(d):
                    fp = os.path.join(d, fn)
                    if fn.endswith(".meta.json"):
                        try:
                            mj = secure_read_json(fp, old_fernet, {})
                            secure_write_json(fp, mj, new_fernet)
                        except Exception:
                            pass
                        continue
                    try:
                        with open(fp, "rb") as f:
                            enc = f.read()
                        data = old_fernet.decrypt(enc)
                        new_enc = new_fernet.encrypt(data)
                        with open(fp, "wb") as w:
                            w.write(new_enc)
                    except Exception:
                        pass
            self.master = p1
            self.key_salt, self.auth_salt = new_key_salt, new_auth_salt
            self.fernet = new_fernet
            write_auth_file(new_key_salt, new_auth_salt,
                hash_for_auth(p1, new_auth_salt,
                              prefer_argon=(self.kdf_name == "argon2id"),
                              params=self.kdf_params),
                self.kdf_name, self.kdf_params)
            self.ensure_verifier_current()
            self.save_blocks()
            self.save_tree()
            self.save_meta()
            self.save_trash()
            custom_info(self, "Пароль", "Мастер-пароль изменён. Все данные пере-шифрованы.")
            audit_write("master_changed", {})
        except Exception as e:
            custom_error(self, "Ошибка", f"Не удалось изменить пароль:\n{e}\n\n{traceback.format_exc()}")
    def migrate_kdf_params(self, new_params: dict, kdf_name: str = "argon2id") -> None:
        if not self.verify_master_prompt("Подтверждение", "Введите мастер‑пароль для смены параметров KDF:"):
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            old_fernet = self.fernet
            new_fernet = make_fernet(self.master, self.key_salt, kdf_name=kdf_name, params=new_params)
            for arr in self.blocks_data.values():
                for b in arr:
                    for kf, vf in list(b.get("fields", {}).items()):
                        plain = decrypt_value(vf, old_fernet)
                        b["fields"][kf] = new_fernet.encrypt(plain.encode("utf-8")).decode("utf-8")
                    nplain = decrypt_value(b.get("notes", ""), old_fernet)
                    b["notes"] = new_fernet.encrypt(nplain.encode("utf-8")).decode("utf-8")
            for bid in os.listdir(ATTACH_DIR):
                d = os.path.join(ATTACH_DIR, bid)
                if not os.path.isdir(d): 
                    continue
                for fn in os.listdir(d):
                    fp = os.path.join(d, fn)
                    if fn.endswith(".meta.json"):
                        try:
                            mj = secure_read_json(fp, old_fernet, {})
                            secure_write_json(fp, mj, new_fernet)
                        except Exception:
                            pass
                        continue
                    try:
                        with open(fp, "rb") as f:
                            enc = f.read()
                        data = old_fernet.decrypt(enc)
                        new_enc = new_fernet.encrypt(data)
                        with open(fp, "wb") as w:
                            w.write(new_enc)
                    except Exception:
                        pass
            self.fernet = new_fernet
            self.kdf_name = kdf_name
            self.kdf_params = dict(new_params)
            write_auth_file(
                self.key_salt, self.auth_salt,
                hash_for_auth(self.master, self.auth_salt, prefer_argon=(kdf_name == "argon2id"), params=self.kdf_params),
                self.kdf_name, self.kdf_params
            )
            self.ensure_verifier_current()
            self.save_blocks(); self.save_tree(); self.save_meta(); self.save_trash()
            try:
                self.index.fernet = new_fernet
                self.index.save()
            except Exception:
                pass
            custom_info(self, "KDF", "Параметры KDF обновлены.")
        finally:
            QApplication.restoreOverrideCursor()
    def export_section(self, item):
        path = []
        cur = item
        while cur and cur.text(0) != "🧠 Умные папки":
            path.insert(0, cur.text(0))
            cur = cur.parent()
        key = "/".join(path)
        self.export_by_key(key)
    def export_by_key(self, key):
        rows = []
        all_fieldnames = set()
        def push_block(k, b):
            parts = k.split("/")
            row = {
                "Раздел": parts[0] if len(parts) > 0 else "",
                "Подраздел": parts[1] if len(parts) > 1 else "",
                "Подподраздел": parts[2] if len(parts) > 2 else "",
                "Название блока": b.get("title", "")
            }
            for kf, vf in b.get("fields", {}).items():
                row[kf] = decrypt_value(vf, self.fernet)
                all_fieldnames.add(kf)
            rows.append(row)
        pref = key + "/" if key else ""
        for k, arr in self.blocks_data.items():
            if k == key or k.startswith(pref):
                for b in arr:
                    push_block(k, b)
        cols = ["Раздел", "Подраздел", "Подподраздел", "Название блока"] + sorted(all_fieldnames)
        self._export_rows(rows, cols, title="Экспорт раздела")
        audit_write("export_section", {"key": key, "rows": len(rows)})
    def export_all(self):
        rows = []
        all_fieldnames = set()
        def push_block(k, b):
            parts = k.split("/")
            row = {
                "Раздел": parts[0] if len(parts) > 0 else "",
                "Подраздел": parts[1] if len(parts) > 1 else "",
                "Подподраздел": parts[2] if len(parts) > 2 else "",
                "Название блока": b.get("title", "")
            }
            for kf, vf in b.get("fields", {}).items():
                row[kf] = decrypt_value(vf, self.fernet)
                all_fieldnames.add(kf)
            rows.append(row)
        for k, arr in self.blocks_data.items():
            for b in arr:
                push_block(k, b)
        cols = ["Раздел", "Подраздел", "Подподраздел", "Название блока"] + sorted(all_fieldnames)
        self._export_rows(rows, cols, title="Экспорт всего")
        audit_write("export_all", {"rows": len(rows)})
    def _export_rows(self, rows, cols, title="Экспорт"):
        if not rows:
            custom_info(self, title, "Нет данных для экспорта.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, title, "",
            "Excel (*.xlsx);;CSV (*.csv);;JSON (*.json);;Text (*.txt);;HTML (*.html)"
        )
        if not path:
            return
        try:
            df = pd.DataFrame(rows, columns=cols)
            if path.endswith(".xlsx"):
                df.to_excel(path, index=False)
            elif path.endswith(".csv"):
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(cols)
                    for r in rows:
                        writer.writerow([r.get(c, "") for c in cols])
            elif path.endswith(".json"):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(rows, f, ensure_ascii=False, indent=2)
            elif path.endswith(".txt"):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("|".join(cols) + "\n")
                    for r in rows:
                        f.write("|".join(str(r.get(c, "")) for c in cols) + "\n")
            elif path.endswith(".html"):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("<html><body><h2>LinkPass Export</h2><table border=1><tr>")
                    for c in cols:
                        f.write(f"<th>{c}</th>")
                    f.write("</tr>")
                    for r in rows:
                        f.write("<tr>")
                        for c in cols:
                            f.write(f"<td>{r.get(c,'')}</td>")
                        f.write("</tr>")
                    f.write("</table></body></html>")
            custom_info(self, title, "Готово!")
        except Exception as e:
            custom_error(self, title, f"Ошибка экспорта: {e}")
    def _norm_cell(self, v: object) -> str:
        if v is None:
            return ""
        s = str(v).replace("\ufeff", "").strip()
        if s.lower() in ("nan", "none", "null"):
            return ""
        return s
    def _find_first_present(self, candidates: list[str], columns: list[str]) -> str | None:
        norm_map = { self._norm_cell(c).lower(): c for c in columns }
        for cand in candidates:
            key = self._norm_cell(cand).lower()
            if key in norm_map:
                return norm_map[key]
        return None
    def _section_columns_in_order(self, columns: list[str]) -> list[str]:
        def n(s: str) -> str:
            return self._norm_cell(s)
        exact: list[tuple[int, str]] = []
        numbered: list[tuple[int, str]] = []
        for c in columns:
            nc = n(c).lower()
            if nc == "раздел" or nc == "section":
                exact.append((0, c))
            elif nc == "подраздел" or nc == "subsection":
                exact.append((1, c))
            elif nc == "подподраздел" or nc in ("subsubsection", "sub-subsection"):
                exact.append((2, c))
            else:
                m = re.fullmatch(r"(?:раздел|section)\s*(\d+)", nc, flags=re.IGNORECASE)
                if m:
                    try:
                        numbered.append((3 + int(m.group(1)), c))
                    except Exception:
                        pass
        exact.sort(key=lambda t: t[0])
        numbered.sort(key=lambda t: t[0])
        return [c for _, c in exact] + [c for _, c in numbered]
    def _detect_path_col(self, columns: list[str]) -> str | None:
        return self._find_first_present([
            "Путь",
            "Path",
            "SectionPath",
            "Section Path",
            "Путь раздела",
            "Путь к разделу",
        ], columns)
    def import_data(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт данных", "",
            "Excel (*.xlsx);;JSON (*.json);;CSV (*.csv);;Text (*.txt)"
        )
        if not path:
            return
        new_blocks: dict[str, list[dict]] = {}
        def push_block_by_parts(parts: list[str], row_fields: dict, title_val: str):
            parts_clean = [self._norm_cell(p) for p in parts if self._norm_cell(p)]
            key = "/".join(parts_clean) if parts_clean else "_"
            want_cat = parts_clean[-1] if parts_clean else ""
            block = {
                "id": secrets.token_hex(12),
                "title": title_val,
                "category": want_cat,
                "fields": {},
                "icon": ""
            }
            for kf, vf in row_fields.items():
                nv = self._norm_cell(vf)
                if nv != "":
                    block["fields"][kf] = encrypt_value(nv, self.fernet)
            if not block["fields"] and not block["title"] and not want_cat:
                return
            new_blocks.setdefault(key, []).append(block)
        try:
            if path.endswith(".xlsx"):
                df = pd.read_excel(path, dtype=str, engine="openpyxl")
                if df is None:
                    custom_warning(self, "Импорт", "Не удалось прочитать Excel.")
                    return
                df = df.fillna("")
                orig_cols = df.columns.tolist()
                title_col = self._find_first_present(
                    ["Название блока", "Название", "Title", "Заголовок"], orig_cols
                )
                sect_cols = self._section_columns_in_order(orig_cols)
                path_col = self._detect_path_col(orig_cols)
                if path_col:
                    try:
                        ser = df[path_col].astype(str)
                        non_empty = ser[ser.str.strip() != ""]
                        frac = float((non_empty.str.contains("/")).mean()) if len(non_empty) else 0.0
                        if frac < 0.5:
                            path_col = None
                    except Exception:
                        path_col = None
                for rec in df.to_dict(orient="records"):
                    title_val = self._norm_cell(rec.get(title_col, "")) if title_col else ""
                    if path_col:
                        parts = [p.strip() for p in str(rec.get(path_col, "")).split("/") if p and p.strip()]
                    else:
                        parts = [self._norm_cell(rec.get(c, "")) for c in sect_cols]
                    row_fields = {}
                    skip = set(sect_cols + ([title_col] if title_col else []) + ([path_col] if path_col else []) + ["Категория"])
                    for col in orig_cols:
                        if col in skip:
                            continue
                        val = self._norm_cell(rec.get(col, ""))
                        if val != "":
                            row_fields[col] = val
                    push_block_by_parts(parts, row_fields, title_val)
            elif path.endswith(".json"):
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                def encrypt_or_passthrough(val):
                    plain, ok = self._try_decrypt_once(val)
                    if ok:
                        return encrypt_value(self._norm_cell(plain), self.fernet)
                    if isinstance(val, str) and val.startswith("gAAAA"):
                        return val
                    return encrypt_value(self._norm_cell(str(val)), self.fernet)
                if isinstance(loaded, dict):
                    for k, arr in loaded.items():
                        if not isinstance(arr, list):
                            continue
                        k_str = "" if k is None else str(k)
                        parts = [p.strip() for p in k_str.split("/") if p and p.strip()]
                        key = "/".join(parts) if parts else "_"
                        for b in arr:
                            if not isinstance(b, dict):
                                continue
                            bid = b.get("id") or secrets.token_hex(12)
                            title_val = self._norm_cell(b.get("title", ""))
                            cat = self._norm_cell(b.get("category", "")) or (key.split("/")[-1] if key and key != "_" else "")
                            fields = {}
                            for fk, fv in (b.get("fields", {}) or {}).items():
                                nv = self._norm_cell(self._try_decrypt_once(fv)[0] if isinstance(fv, str) else str(fv))
                                if nv != "":
                                    fields[fk] = encrypt_or_passthrough(fv)
                            block = {"id": bid, "title": title_val, "category": cat, "fields": fields, "icon": ""}
                            if fields or title_val or cat:
                                new_blocks.setdefault(key, []).append(block)
                elif isinstance(loaded, list):
                    all_cols: list[str] = []
                    for r in loaded:
                        if isinstance(r, dict):
                            for k in r.keys():
                                if k not in all_cols:
                                    all_cols.append(k)
                    title_col = self._find_first_present(["Название блока", "Название", "Title", "Заголовок"], all_cols)
                    sect_cols = self._section_columns_in_order(all_cols)
                    path_col = self._detect_path_col(all_cols)
                    if path_col:
                        vals = [str(r.get(path_col, "")).strip() for r in loaded if isinstance(r, dict)]
                        nz = [v for v in vals if v]
                        if not nz or (sum(1 for v in nz if "/" in v) / len(nz) < 0.5):
                            path_col = None
                    for r in loaded:
                        if not isinstance(r, dict):
                            continue
                        if path_col:
                            parts = [p.strip() for p in str(r.get(path_col, "")).split("/") if p and p.strip()]
                        else:
                            parts = [self._norm_cell(r.get(c, "")) for c in sect_cols]
                        title_val = self._norm_cell(r.get(title_col, "")) if title_col else ""
                        fields = {}
                        skip = set(sect_cols + ([title_col] if title_col else []) + ([path_col] if path_col else []) + ["Категория"])
                        for kcol in all_cols:
                            if kcol in skip:
                                continue
                            raw = r.get(kcol, "")
                            if self._norm_cell(raw) == "":
                                continue
                            fields[kcol] = encrypt_or_passthrough(raw)
                        push_block_by_parts(parts, fields, title_val)
                else:
                    custom_warning(self, "Импорт", "Неподдерживаемая структура JSON.")
                    return
            elif path.endswith(".csv"):
                def read_csv_auto(enc: str):
                    with open(path, newline="", encoding=enc) as f:
                        sample = f.read(8192)
                        f.seek(0)
                        try:
                            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                            reader = csv.DictReader(f, dialect=dialect)
                        except Exception:
                            counts: dict[str, int] = {
                                ",": sample.count(","),
                                ";": sample.count(";"),
                                "\t": sample.count("\t"),
                                "|": sample.count("|"),
                            }
                            delim: str = max(counts.keys(), key=lambda k: counts[k])
                            reader = csv.DictReader(f, delimiter=delim)
                        return list(reader)
                try:
                    rows = read_csv_auto("utf-8-sig")
                except UnicodeDecodeError:
                    rows = read_csv_auto("cp1251")
                if not rows:
                    custom_warning(self, "Импорт", "CSV пустой или без заголовков.")
                    return
                orig_columns = list(rows[0].keys())
                title_col = self._find_first_present(
                    ["Название блока", "Название", "Title", "Заголовок"], orig_columns
                )
                sect_cols = self._section_columns_in_order(orig_columns)
                path_col = self._detect_path_col(orig_columns)
                if path_col:
                    vals = [str(row.get(path_col, "")).strip() for row in rows]
                    nz = [v for v in vals if v]
                    if not nz or (sum(1 for v in nz if "/" in v) / len(nz) < 0.5):
                        path_col = None
                for row in rows:
                    if path_col:
                        raw_path = str(row.get(path_col, "")).strip()
                        parts = [p for p in raw_path.split("/") if p]
                    else:
                        parts = [self._norm_cell(row.get(c, "")) for c in sect_cols]
                    title_val = self._norm_cell(row.get(title_col, "")) if title_col else ""
                    row_fields: dict[str, str] = {}
                    skip = set(sect_cols + ([title_col] if title_col else []) + ([path_col] if path_col else []) + ["Категория"])
                    for col in orig_columns:
                        if col in skip:
                            continue
                        val = self._norm_cell(row.get(col, ""))
                        if val != "":
                            row_fields[col] = val
                    push_block_by_parts(parts, row_fields, title_val)
            elif path.endswith(".txt"):
                def read_txt_try(enc: str):
                    with open(path, "r", encoding=enc) as f:
                        return f.read().splitlines()
                try:
                    lines = read_txt_try("utf-8-sig")
                except UnicodeDecodeError:
                    lines = read_txt_try("cp1251")
                if not lines:
                    custom_warning(self, "Импорт", "TXT пустой.")
                    return
                header = [self._norm_cell(c) for c in lines[0].split("|")]
                title_col = self._find_first_present(
                    ["Название блока", "Название", "Title", "Заголовок"], header
                )
                sect_cols = self._section_columns_in_order(header)
                path_col = self._detect_path_col(header)
                if path_col:
                    non_empty = 0
                    with_slash = 0
                    try:
                        idx = header.index(path_col)
                    except ValueError:
                        idx = -1
                    if idx >= 0:
                        for line in lines[1:]:
                            if not line.strip():
                                continue
                            parts_line = line.split("|")
                            val = parts_line[idx].strip() if idx < len(parts_line) else ""
                            if val:
                                non_empty += 1
                                if "/" in val:
                                    with_slash += 1
                        if non_empty == 0 or (with_slash / non_empty) < 0.5:
                            path_col = None
                    else:
                        path_col = None
                for line in lines[1:]:
                    if not line.strip():
                        continue
                    parts_line = line.split("|")
                    row = {header[i]: (parts_line[i] if i < len(parts_line) else "") for i in range(len(header))}
                    if path_col:
                        raw_path = str(row.get(path_col, "")).strip()
                        parts = [p for p in raw_path.split("/") if p]
                    else:
                        parts = [self._norm_cell(row.get(c, "")) for c in sect_cols]
                    title_val = self._norm_cell(row.get(title_col, "")) if title_col else ""
                    row_fields: dict[str, str] = {}
                    skip = set(sect_cols + ([title_col] if title_col else []) + ([path_col] if path_col else []) + ["Категория"])
                    for col in header:
                        if col in skip:
                            continue
                        val = self._norm_cell(row.get(col, ""))
                        if val != "":
                            row_fields[col] = val
                    push_block_by_parts(parts, row_fields, title_val)
            else:
                custom_warning(self, "Импорт", "Неподдерживаемый формат файла.")
                return
        except Exception as e:
            custom_warning(self, "Импорт", f"Ошибка импорта: {e}")
            return
        for k in new_blocks.keys():
            if k and k != "_":
                self._ensure_tree_path(k)
        added = 0
        for k, arr in new_blocks.items():
            self.blocks_data.setdefault(k, []).extend(arr)
            for b in arr:
                self.id_to_ref[b["id"]] = (k, b)
                self.update_index_for_block(b)
            added += len(arr)
        self.save_tree()
        self.save_blocks()
        self.render_tree()
        self.schedule_render()
        audit_write("import", {"file": path, "sections": len(new_blocks), "blocks": added})
        custom_info(self, "Импорт", f"Готово. Импортировано блоков: {added}.")
    def restore_backup_unified(self):
        path, _ = QFileDialog.getOpenFileName(self, "Восстановить", "", "LPX/LPBK/LPEX/ZIP (*.lpx *.lpbk *.lpex *.zip)")
        if not path: return
        pwd = None
        if not path.lower().endswith(".zip"):
            pwd = self.ask_password("Пароль файла", "Введите пароль:")
            if pwd is None:
                return
        def work():
            tmpdir = tempfile.mkdtemp(prefix="LinkPass_restore_")
            if path.lower().endswith(".zip"):
                with zipfile.ZipFile(path, "r") as z:
                    z.extractall(tmpdir)
            else:
                raw = _lpx_decrypt_bytes_or_file(path, pwd or "")
                with zipfile.ZipFile(io.BytesIO(raw), "r") as z:
                    z.extractall(tmpdir)
            return tmpdir
        def done(tmpdir):
            try:
                if not custom_question(self, "Восстановление", "Перезаписать текущие данные из бэкапа?\n(будет создана копия текущего состояния)"):
                    shutil.rmtree(tmpdir, ignore_errors=True); return
                self._copy_restored(tmpdir)
                shutil.rmtree(tmpdir, ignore_errors=True)
                self._reload_all_from_disk_after_restore()
                restored_blocks = 0
                try:
                    restored_blocks = sum(len(arr) for arr in self.blocks_data.values())
                except Exception:
                    pass
                custom_info(self, "Восстановлено", f"Готово. Восстановлено блоков: {restored_blocks}.")
                audit_write("restore_unified", {"file": path, "restored_blocks": restored_blocks})
            except Exception as e:
                shutil.rmtree(tmpdir, ignore_errors=True)
                custom_error(self, "Восстановление", f"Ошибка: {e}")
        run_long_task(self, "Восстановление", work, done)
    def _copy_restored(self, srcdir):
        bdir = os.path.join(DATA_DIR, "_backup_before_restore_" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        os.makedirs(bdir, exist_ok=True)
        for fn in (TREE_FILE, BLOCKS_FILE, TRASH_FILE, META_FILE, MASTER_FILE, INDEX_DB):
            if os.path.exists(fn):
                shutil.copy2(fn, os.path.join(bdir, os.path.basename(fn)))
        if os.path.isdir(ATTACH_DIR):
            shutil.copytree(ATTACH_DIR, os.path.join(bdir, "attachments"), dirs_exist_ok=True)
        for base in ("tree.json", "blocks.json", "trash.json", "meta.json", "auth.json", "index.db"):
            fp = os.path.join(srcdir, base)
            if os.path.exists(fp):
                shutil.copy2(fp, os.path.join(DATA_DIR, base))
        src_att = os.path.join(srcdir, "attachments")
        if os.path.isdir(src_att):
            shutil.copytree(src_att, ATTACH_DIR, dirs_exist_ok=True)
    def _reload_all_from_disk_after_restore(self) -> None:
        try:
            if os.path.exists(MASTER_FILE):
                with open(MASTER_FILE, "r", encoding="utf-8") as f:
                    j = json.load(f)
                new_key_salt = base64.b64decode(j.get("key_salt", "")) if j.get("key_salt") else self.key_salt
                new_auth_salt = base64.b64decode(j.get("auth_salt", "")) if j.get("auth_salt") else self.auth_salt
                kdf = j.get("kdf", "argon2id")
                params = j.get("kdf_params") or KDF_DEFAULTS
                verifier = j.get("verifier", "")
                calc = hash_for_auth(self.master, new_auth_salt,
                                    prefer_argon=(kdf == "argon2id"), params=params)
                if verifier and calc != verifier:
                    pwd = self.ask_password("Мастер-пароль бэкапа",
                                            "После восстановления бэкапа мастер-пароль изменился.\n"
                                            "Введите пароль, с которым создавался бэкап:")
                    if pwd:
                        calc2 = hash_for_auth(pwd, new_auth_salt,
                                            prefer_argon=(kdf == "argon2id"), params=params)
                        if calc2 == verifier:
                            self.master = pwd
                            self.key_salt, self.auth_salt = new_key_salt, new_auth_salt
                            self.kdf_name, self.kdf_params = kdf, params
                            self.fernet = make_fernet(self.master, self.key_salt,
                                                    kdf_name=self.kdf_name, params=self.kdf_params)
                        else:
                            custom_warning(self, "Бэкап", "Пароль не подошёл. Данные будут загружены без расшифровки.")
                else:
                    self.key_salt, self.auth_salt = new_key_salt, new_auth_salt
                    self.kdf_name, self.kdf_params = kdf, params
                    self.fernet = make_fernet(self.master, self.key_salt,
                                            kdf_name=self.kdf_name, params=self.kdf_params)
        except Exception as e:
            custom_warning(self, "Бэкап", f"Не удалось применить мастер-ключ из бэкапа: {e}")
        prev_path = list(self.current_path)
        self.meta = self.load_meta()
        self.data_tree = self.load_tree()
        self.blocks_data = self.load_blocks()
        self.trash = self.load_trash()
        self.run_startup_migrations()
        self.rebuild_index()
        self.render_tree()
        if prev_path:
            self._select_path_in_tree(prev_path)
        self.schedule_render()
        audit_write("hot_reload_after_restore", {"sections": len(self.get_all_paths())})
    def show_export_tasks(self):
        ExportTasksManager(self).exec()
    def calc_next_run(self, typ, every_min, at_time):
        now = datetime.now()
        if typ == "interval":
            return (now + timedelta(minutes=max(1, int(every_min)))).strftime("%Y-%m-%d %H:%M")
        try:
            hh, mm = (at_time or "02:00").split(":")
            t = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            t = now.replace(hour=2, minute=0, second=0, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        return t.strftime("%Y-%m-%d %H:%M")
    def tick_scheduler(self):
        tasks = self.meta.get("export_tasks", [])
        if not tasks:
            return
        now = datetime.now()
        changed = False
        for t in tasks:
            nr = t.get("next_run")
            if not nr:
                t["next_run"] = self.calc_next_run(t.get("type", "interval"), int(t.get("every_min", 60)), t.get("at_time", "02:00"))
                changed = True
                continue
            try:
                ts = datetime.strptime(nr, "%Y-%m-%d %H:%M")
            except Exception:
                t["next_run"] = self.calc_next_run(t.get("type", "interval"), int(t.get("every_min", 60)), t.get("at_time", "02:00"))
                changed = True
                continue
            if now >= ts:
                self.run_export_task(t)
                t["next_run"] = self.calc_next_run(t.get("type", "interval"), int(t.get("every_min", 60)), t.get("at_time", "02:00"))
                changed = True
        if changed:
            self.save_meta()
    def run_export_task(self, t):
        try:
            path_dir = (t.get("path_dir") or "").strip()
            if not path_dir:
                legacy_path = (t.get("path") or "").strip()
                if legacy_path:
                    pd = os.path.dirname(legacy_path)
                    if pd:
                        path_dir = pd
            if not path_dir:
                path_dir = DATA_DIR
            try:
                os.makedirs(path_dir, exist_ok=True)
            except Exception:
                pass
            try:
                enc_pwd = decrypt_value(t.get("enc_pwd_enc", ""), self.fernet) if t.get("enc_pwd_enc") else ""
            except Exception:
                enc_pwd = ""
            if not enc_pwd:
                audit_write("export_task_skip_no_password", {"name": t.get("name", "")})
                return
            raw_zip = self._make_full_backup_zip_bytes()
            data = _lpx_encrypt_bytes(raw_zip, enc_pwd)
            seq = int(t.get("seq", 1))
            ts  = datetime.now().strftime("%Y%m%d-%H%M%S")
            fname = f"backup_{ts}_{seq:04d}.lpx"
            out_path = os.path.join(path_dir, fname)
            with open(out_path, "wb") as f:
                f.write(data)
            t["seq"] = seq + 1
            self.save_meta()
            audit_write("export_task_run", {"name": t.get("name", ""), "file": out_path})
        except Exception as e:
            audit_write("export_task_error", {"name": t.get("name", ""), "error": str(e)})
    def _tpl(self, s: str, task: dict, seq_for_preview: int | None = None) -> str:
        now = datetime.now()
        seq = int(task.get("seq", 1))
        if seq_for_preview is not None:
            seq = seq_for_preview
        ctx = {
            "ts": now.strftime("%Y%m%d-%H%M%S"),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H-%M-%S"),
            "datetime": now.strftime("%Y-%m-%d_%H-%M-%S"),
            "name": task.get("name", ""),
            "scope": task.get("scope", ""),
            "preset": task.get("preset", ""),
            "seq": seq,
            "seq2": f"{seq:02d}",
            "seq3": f"{seq:03d}",
            "seq4": f"{seq:04d}",
        }
        try:
            return (s or "").format(**ctx)
        except Exception:
            return s or ""
    def _export_bytes(self, rows: list[dict], cols: list[str], ext: str) -> tuple[bytes, str, str]:
        ext = (ext or "").lower()
        base_name = "export"
        if ext == ".xlsx":
            buf = io.BytesIO()
            df = pd.DataFrame(rows, columns=cols)
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                df.to_excel(w, index=False)
            return buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", base_name + ext
        elif ext == ".csv":
            s = io.StringIO()
            wr = csv.writer(s, lineterminator="\n")
            wr.writerow(cols)
            for r in rows:
                wr.writerow([r.get(c, "") for c in cols])
            return ("\ufeff" + s.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", base_name + ext
        elif ext == ".json":
            return json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8", base_name + ext
        elif ext == ".txt":
            lines = ["|".join(cols)]
            for r in rows:
                lines.append("|".join(str(r.get(c, "")) for c in cols))
            return "\n".join(lines).encode("utf-8"), "text/plain; charset=utf-8", base_name + ext
        elif ext == ".html":
            out = ["<html><body><table border=1><tr>"]
            out.extend(f"<th>{c}</th>" for c in cols)
            out.append("</tr>")
            for r in rows:
                out.append("<tr>")
                out.extend(f"<td>{r.get(c,'')}</td>" for c in cols)
                out.append("</tr>")
            out.append("</table></body></html>")
            return "".join(out).encode("utf-8"), "text/html; charset=utf-8", base_name + ext
        s = io.StringIO()
        wr = csv.writer(s, lineterminator="\n")
        wr.writerow(cols)
        for r in rows:
            wr.writerow([r.get(c, "") for c in cols])
        return ("\ufeff" + s.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", base_name + ".csv"
    def _zip_single_file_bytes(self, filename: str, data: bytes) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(filename, data)
        return buf.getvalue()
    def _lpex_encrypt_bytes(self, raw: bytes, password: str) -> bytes:
        if not password:
            raise ValueError("Пароль шифрования не задан")
        salt = rand_bytes(16)
        if HAS_ARGON2:
            key = argon2id_key(password, salt)
            tag = b"A"
        else:
            key = pbkdf2_key(password, salt)
            tag = b"P"
        f = Fernet(base64.urlsafe_b64encode(key))
        enc = f.encrypt(raw)
        return b"LPEX1" + tag + salt + enc
    def _lpex_decrypt_file_to_bytes(self, path: str, password: str) -> bytes:
        with open(path, "rb") as f:
            data = f.read()
        if not data.startswith(b"LPEX1"):
            raise ValueError("Не экспортный зашифрованный файл (ожидается LPEX1)")
        kdf_tag = data[5:6]
        salt = data[6:22]
        enc = data[22:]
        key = argon2id_key(password, salt) if (kdf_tag == b"A" and HAS_ARGON2) else pbkdf2_key(password, salt)
        f = Fernet(base64.urlsafe_b64encode(key))
        return f.decrypt(enc) 
    def decrypt_export_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Расшифровать экспорт (LPX1/LPEX1)", "", "LPX/LPEX (*.lpx *.lpex);;All Files (*)")
        if not path:
            return
        pwd = self.ask_password("Пароль экспорта", "Введите пароль для расшифровки:")
        if pwd is None:
            return
        try:
            data = _lpx_decrypt_bytes_or_file(path, pwd)
            try:
                with zipfile.ZipFile(io.BytesIO(data), "r") as z:
                    names = z.namelist()
                    if not names:
                        raise zipfile.BadZipFile("Пустой ZIP")
                    default = names[0]
                    save, _ = QFileDialog.getSaveFileName(self, "Сохранить расшифрованный файл", default)
                    if not save:
                        return
                    with z.open(default) as src, open(save, "wb") as out:
                        out.write(src.read())
            except zipfile.BadZipFile:
                save, _ = QFileDialog.getSaveFileName(self, "Сохранить расшифрованный файл", "export.bin")
                if not save:
                    return
                with open(save, "wb") as w:
                    w.write(data)
            custom_info(self, "Экспорт", "Готово.")
        except InvalidToken:
            custom_error(self, "Экспорт", "Неверный пароль.")
        except Exception as e:
            custom_error(self, "Экспорт", f"Ошибка: {e}")
    def manage_smart_folders(self):
        dlg = SmartFolderManager(self.meta.get("smart_folders", []))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.meta["smart_folders"] = dlg.value()
            self.save_meta()
            self.render_tree()
    def manage_templates(self):
        dlg = TemplatesManager(self.meta.get("templates", []))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.meta["templates"] = dlg.value()
            self.save_meta()
    def manage_export_presets(self):
        all_fields = set()
        for arr in self.blocks_data.values():
            for b in arr:
                all_fields.update(b.get("fields", {}).keys())
        dlg = ExportPresetsManager(self.meta.get("export_presets", []), sorted(all_fields))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.meta["export_presets"] = dlg.value()
            self.save_meta()
    def save_all(self):
        self.save_tree()
        self.save_blocks()
        self.save_meta()
        custom_info(self, "Сохранено", "Все данные сохранены.")
        audit_write("save_all", {})
    def auto_lock(self):
        self.show_data = False
        try:
            if hasattr(self, "btn_toggle_data") and self.btn_toggle_data is not None:
                self.btn_toggle_data.setChecked(False)
                self.btn_toggle_data.setText("Открыть данные")
        except Exception:
            pass
        QApplication.clipboard().clear()
        try:
            for p in list(self._temp_share_dirs):
                shutil.rmtree(p, ignore_errors=True)
                self._temp_share_dirs.discard(p)
        except Exception:
            pass
        self.schedule_render()
        audit_write("auto_lock", {})
    def show_block_qr(self, block: dict):
        if self.can_show_block_data(block):
            payload = self._build_qr_plaintext(block)
        else:
            payload = self._format_block_share_text(block, reveal=False)
        QRDialog(block.get("title", ""), payload, self).exec()
    def closeEvent(self, e):
        try:
            try:
                if hasattr(self, "scheduler"):
                    self.scheduler.stop()
            except Exception:
                pass
            try:
                if hasattr(self, "inact_timer"):
                    self.inact_timer.stop()
            except Exception:
                pass
            try:
                if hasattr(self, "tray") and self.tray is not None:
                    self.tray.hide()
                    self.tray.deleteLater()
            except Exception:
                pass
            try:
                self.save_all()
            except Exception:
                pass
            try:
                self.index.save()
            except Exception:
                pass
            try:
                self.index.conn.close()
            except Exception:
                pass
            try:
                for p in list(self._temp_share_dirs):
                    shutil.rmtree(p, ignore_errors=True)
                    self._temp_share_dirs.discard(p)
            except Exception:
                pass
        finally:
            super().closeEvent(e)
def custom_info(parent, title, text):
    QMessageBox.information(parent, title, text)
def custom_warning(parent, title, text):
    QMessageBox.warning(parent, title, text)
def custom_error(parent, title, text):
    QMessageBox.critical(parent, title, text)
def custom_question(parent, title, text) -> bool:
    res = QMessageBox.question(
        parent, title, text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No
    )
    return res == QMessageBox.StandardButton.Yes
class SmartFolderDialog(QDialog):
    def __init__(self, sf=None):
        super().__init__()
        self.setWindowTitle("Умная папка")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.setModal(True)
        self.name = QLineEdit()
        self.query = QLineEdit()
        self.scope = QLineEdit()
        self.mode = QComboBox()
        self.mode.addItems(["Поля", "Блоки"])
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Имя:")); lay.addWidget(self.name)
        lay.addWidget(QLabel("Запрос:")); lay.addWidget(self.query)
        lay.addWidget(QLabel("Ограничить разделом (путь, необязательно):")); lay.addWidget(self.scope)
        lay.addWidget(QLabel("Режим поиска:")); lay.addWidget(self.mode)
        row = QHBoxLayout()
        okb = QPushButton("Сохранить"); okb.clicked.connect(self.accept)
        cb = QPushButton("Отмена"); cb.clicked.connect(self.reject)
        row.addWidget(okb); row.addWidget(cb); lay.addLayout(row)
        if sf:
            self.name.setText(sf.get("name", ""))
            self.query.setText(sf.get("query", ""))
            self.scope.setText(sf.get("scope", ""))
            self.mode.setCurrentText("Блоки" if sf.get("mode") == "Блоки" else "Поля")
    def value(self):
        return {
            "name": self.name.text().strip() or "Без имени",
            "query": self.query.text().strip(),
            "scope": self.scope.text().strip(),
            "mode": self.mode.currentText()
        }
class SmartFolderManager(QDialog):
    def __init__(self, smart_folders: list):
        super().__init__()
        self.setWindowTitle("Умные папки")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.setModal(True)
        self.sf = [dict(x) for x in smart_folders]
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Имя", "Запрос", "Scope", "Режим"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        thead = self.table.horizontalHeader()
        try:
            thead.setStretchLastSection(True)
        except Exception:
            pass
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        addb = QPushButton("Добавить"); addb.clicked.connect(self.add)
        edb = QPushButton("Изменить"); edb.clicked.connect(self.edit)
        delb = QPushButton("Удалить"); delb.clicked.connect(self.delete)
        close = QPushButton("Закрыть"); close.clicked.connect(self.accept)
        row.addWidget(addb); row.addWidget(edb); row.addWidget(delb); row.addStretch(1); row.addWidget(close)
        lay.addLayout(row)
        self.populate()
    def populate(self):
        self.table.setRowCount(0)
        for sf in self.sf:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(sf.get("name", "")))
            self.table.setItem(r, 1, QTableWidgetItem(sf.get("query", "")))
            self.table.setItem(r, 2, QTableWidgetItem(sf.get("scope", "ALL")))
            self.table.setItem(r, 3, QTableWidgetItem(sf.get("mode", "Поля")))
    def add(self):
        d = SmartFolderDialog()
        if d.exec() == QDialog.DialogCode.Accepted:
            self.sf.append(d.value()); self.populate()
    def edit(self):
        r = self.table.currentRow()
        if r < 0: return
        d = SmartFolderDialog(self.sf[r])
        if d.exec() == QDialog.DialogCode.Accepted:
            self.sf[r] = d.value(); self.populate()
    def delete(self):
        r = self.table.currentRow()
        if r < 0: return
        del self.sf[r]; self.populate()
    def value(self): return self.sf
class TemplatesManager(QDialog):
    def __init__(self, templates: list):
        super().__init__()
        self.setWindowTitle("Шаблоны блоков")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.setModal(True)
        self.tpl = [dict(x) for x in templates]
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Имя шаблона", "Поля"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        thead = self.table.horizontalHeader()
        try:
            thead.setStretchLastSection(True)
        except Exception:
            pass
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        addb = QPushButton("Добавить"); addb.clicked.connect(self.add)
        edb = QPushButton("Изменить"); edb.clicked.connect(self.edit)
        delb = QPushButton("Удалить"); delb.clicked.connect(self.delete)
        close = QPushButton("Закрыть"); close.clicked.connect(self.accept)
        row.addWidget(addb); row.addWidget(edb); row.addWidget(delb); row.addStretch(1); row.addWidget(close)
        lay.addLayout(row)
        self.populate()
    def populate(self):
        self.table.setRowCount(0)
        for s in self.tpl:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(s.get("name", "")))
            self.table.setItem(r, 1, QTableWidgetItem(", ".join(s.get("fields", []))))
    def add(self):
        name, ok = QInputDialog.getText(self, "Имя шаблона", "Введите имя:")
        if not (ok and name): return
        fields, ok2 = QInputDialog.getText(self, "Поля", "Через запятую:")
        if not ok2: return
        self.tpl.append({"name": name.strip(), "fields": [f.strip() for f in fields.split(",") if f.strip()]})
        self.populate()
    def edit(self):
        r = self.table.currentRow()
        if r < 0: return
        name, ok = QInputDialog.getText(self, "Имя шаблона", "Имя:", text=self.tpl[r].get("name", ""))
        if not (ok and name): return
        fields, ok2 = QInputDialog.getText(self, "Поля", "Через запятую:", text=", ".join(self.tpl[r].get("fields", [])))
        if not ok2: return
        self.tpl[r] = {"name": name.strip(), "fields": [f.strip() for f in fields.split(",") if f.strip()]}
        self.populate()
    def delete(self):
        r = self.table.currentRow()
        if r < 0: return
        del self.tpl[r]; self.populate()
    def value(self): return self.tpl
class ExportPresetsManager(QDialog):
    def __init__(self, presets: list, all_fields: list):
        super().__init__()
        self.setWindowTitle("Пресеты экспорта")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.setModal(True)
        self.presets = [dict(x) for x in presets]
        self.all_fields = all_fields
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Имя пресета", "Столбцы"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        thead = self.table.horizontalHeader()
        try:
            thead.setStretchLastSection(True)
        except Exception:
            pass
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        addb = QPushButton("Добавить"); addb.clicked.connect(self.add)
        edb = QPushButton("Изменить"); edb.clicked.connect(self.edit)
        delb = QPushButton("Удалить"); delb.clicked.connect(self.delete)
        close = QPushButton("Закрыть"); close.clicked.connect(self.accept)
        row.addWidget(addb); row.addWidget(edb); row.addWidget(delb); row.addStretch(1); row.addWidget(close)
        lay.addLayout(row)
        self.populate()
    def populate(self):
        self.table.setRowCount(0)
        for s in self.presets:
            r = self.table.rowCount(); self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(s.get("name", "")))
            self.table.setItem(r, 1, QTableWidgetItem(", ".join(s.get("columns", []))))
    def add(self):
        name, ok = QInputDialog.getText(self, "Имя пресета", "Введите имя:")
        if not (ok and name): return
        cols, ok2 = QInputDialog.getText(self, "Столбцы", "Через запятую (добавьте заранее поля в блоках):")
        if not ok2: return
        self.presets.append({"name": name.strip(), "columns": [c.strip() for c in cols.split(",") if c.strip()]})
        self.populate()
    def edit(self):
        r = self.table.currentRow()
        if r < 0: return
        name, ok = QInputDialog.getText(self, "Имя пресета", "Имя:", text=self.presets[r].get("name", ""))
        if not (ok and name): return
        cols, ok2 = QInputDialog.getText(self, "Столбцы", "Через запятую:", text=", ".join(self.presets[r].get("columns", [])))
        if not ok2: return
        self.presets[r] = {"name": name.strip(), "columns": [c.strip() for c in cols.split(",") if c.strip()]}
        self.populate()
    def delete(self):
        r = self.table.currentRow()
        if r < 0: return
        del self.presets[r]; self.populate()
    def value(self): return self.presets
class RecycleBinDialog(QDialog):
    def __init__(self, win: MainWindow):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Корзина")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(820, 520)
        lay = QVBoxLayout(self)
        trow = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Фильтр по названию/пути/ID…")
        self.filter.textChanged.connect(self.populate)
        trow.addWidget(self.filter)
        trow.addStretch(1)
        lay.addLayout(trow)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Название", "Откуда", "Когда", "ID"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        thead = self.table.horizontalHeader()
        try:
            thead.setStretchLastSection(True)
        except Exception:
            pass
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.table)
        brow = QHBoxLayout()
        self.btn_restore = QPushButton("Восстановить выбранный")
        self.btn_restore.clicked.connect(self.restore_selected)
        self.btn_open = QPushButton("Открыть (только просмотр)")
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_clear_all = QPushButton("Очистить корзину")
        self.btn_clear_all.clicked.connect(self.clear_all)
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        t = self.win.theme
        r = int(t.get("btn_radius", 10))
        fg = t.get("btn_fg", "#FFF")
        self.btn_clear_all.setStyleSheet(
            f"background:{t['btn_delete_bg']};color:{fg};border:none;border-radius:{r}px;"
        )
        brow.addWidget(self.btn_restore)
        brow.addWidget(self.btn_open)
        brow.addStretch(1)
        brow.addWidget(self.btn_clear_all)
        brow.addWidget(btn_close)
        lay.addLayout(brow)
        self.table.itemDoubleClicked.connect(lambda *_: self.open_selected())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self._row_to_trash_index = []
        self.populate()
    def _update_buttons(self):
        has_sel = self.table.currentRow() >= 0
        self.btn_restore.setEnabled(has_sel)
        self.btn_open.setEnabled(has_sel)
        self.btn_clear_all.setEnabled(len(self.win.trash) > 0)
    def selected_index(self):
        r = self.table.currentRow()
        if 0 <= r < len(self._row_to_trash_index):
            return self._row_to_trash_index[r]
        return -1
    def populate(self):
        self.table.setRowCount(0)
        self._row_to_trash_index = []
        filt = (self.filter.text() or "").lower()
        for idx, t in enumerate(self.win.trash):
            b = t.get("block", {}) or {}
            title = b.get("title", "(без названия)")
            frm = t.get("from_key", "")
            ts = t.get("ts", "")
            bid = t.get("id", "")
            if filt:
                hay = " ".join([title, frm, ts, bid]).lower()
                if filt not in hay:
                    continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(title))
            self.table.setItem(r, 1, QTableWidgetItem(frm))
            self.table.setItem(r, 2, QTableWidgetItem(ts))
            self.table.setItem(r, 3, QTableWidgetItem(bid))
            self._row_to_trash_index.append(idx)
        if self.table.rowCount() > 0:
            self.table.scrollToBottom()
        self._update_buttons()
    def restore_selected(self):
        r = self.selected_index()
        if r < 0:
            custom_info(self, "Корзина", "Не выбран элемент.")
            return
        it = self.win.trash[r]
        key = it.get("from_key", "")
        b = it.get("block")
        if not b:
            custom_warning(self, "Корзина", "Нет данных блока.")
            return
        if key:
            self.win._ensure_tree_path(key)
            self.win.save_tree()
            self.win.render_tree()
        self.win.blocks_data.setdefault(key, []).append(b)
        self.win.id_to_ref[b["id"]] = (key, b)
        self.win.update_index_for_block(b)
        del self.win.trash[r]
        self.win.save_blocks()
        self.win.save_trash()
        self.populate()
        self.win.current_path = [p for p in key.split("/") if p]
        self.win.schedule_render()
        audit_write("trash_restore", {"block_id": b["id"], "to": key})
        custom_info(self, "Корзина", "Восстановлено.")
    def open_selected(self):
        r = self.selected_index()
        if r < 0:
            custom_info(self, "Корзина", "Не выбран элемент.")
            return
        it = self.win.trash[r]
        b = it.get("block")
        if not b:
            custom_warning(self, "Корзина", "Нет данных блока.")
            return
        BlockEditorDialog(self.win, b).exec()
    def clear_all(self):
        if not self.win.trash:
            custom_info(self, "Корзина", "Корзина уже пуста.")
            return
        self.win.clear_trash()
        self.populate()
class ExportTasksManager(QDialog):
    def __init__(self, win: MainWindow):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Экспорт по расписанию")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(900, 520)
        self.tasks = [dict(x) for x in (self.win.meta.get("export_tasks") or [])]
        self._migrate_tasks_inplace()
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Имя", "Тип", "Каждые (мин)", "Время (ежедн.)", "Папка", "Следующий запуск"
        ])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        thead = self.table.horizontalHeader()
        try:
            thead.setStretchLastSection(True)
        except Exception:
            pass
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        b_add = QPushButton("Добавить"); b_add.clicked.connect(self.add)
        b_edit = QPushButton("Изменить"); b_edit.clicked.connect(self.edit)
        b_del = QPushButton("Удалить"); b_del.clicked.connect(self.delete)
        b_run = QPushButton("Запустить сейчас"); b_run.clicked.connect(self.run_now)
        b_path = QPushButton("Папка экспорта…"); b_path.clicked.connect(self.pick_folder_for_selected)
        b_close = QPushButton("Закрыть"); b_close.clicked.connect(self.accept)
        row.addWidget(b_add); row.addWidget(b_edit); row.addWidget(b_del); row.addWidget(b_run); row.addWidget(b_path)
        row.addStretch(1); row.addWidget(b_close)
        lay.addLayout(row)
        self.populate()
    def _migrate_tasks_inplace(self):
        changed = False
        for t in self.tasks:
            if not t.get("path_dir"):
                legacy_path = (t.get("path") or "").strip()
                if legacy_path:
                    pd = os.path.dirname(legacy_path)
                    if pd:
                        t["path_dir"] = pd
                        changed = True
            t.setdefault("name", "Экспорт")
            t.setdefault("type", "interval")
            t.setdefault("every_min", 60)
            t.setdefault("at_time", "02:00")
            t.setdefault("seq", 1)
            t.setdefault("enc_pwd_enc", t.get("enc_pwd_enc", ""))
            if not t.get("next_run"):
                t["next_run"] = self.win.calc_next_run(
                    t.get("type", "interval"),
                    int(t.get("every_min", 60)),
                    t.get("at_time", "02:00")
                )
                changed = True
        if changed:
            self.win.meta["export_tasks"] = self.tasks
            self.win.save_meta()
    def populate(self):
        self.table.setRowCount(0)
        for t in self.tasks:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(t.get("name", "")))
            self.table.setItem(r, 1, QTableWidgetItem(t.get("type", "interval")))
            self.table.setItem(r, 2, QTableWidgetItem(str(t.get("every_min", ""))))
            self.table.setItem(r, 3, QTableWidgetItem(t.get("at_time", "")))
            self.table.setItem(r, 4, QTableWidgetItem(t.get("path_dir", "")))
            self.table.setItem(r, 5, QTableWidgetItem(t.get("next_run", "")))
    def selected(self):
        r = self.table.currentRow()
        return (r, self.tasks[r]) if 0 <= r < len(self.tasks) else (-1, None)
    def add(self):
        d = ExportTaskEditDialog(self.win, None)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.tasks.append(d.value())
            self.save()
    def edit(self):
        i, t = self.selected()
        if t is None:
            return
        d = ExportTaskEditDialog(self.win, t)
        if d.exec() == QDialog.DialogCode.Accepted:
            self.tasks[i] = d.value()
            self.save()
    def delete(self):
        i, t = self.selected()
        if t is None:
            return
        del self.tasks[i]
        self.save()
    def run_now(self):
        _, t = self.selected()
        if t is None:
            return
        self.win.run_export_task(t)
        custom_info(self, "Экспорт", "Готово. Проверьте папку назначения.")
    def pick_folder_for_selected(self):
        i, t = self.selected()
        if t is None:
            return
        start = t.get("path_dir", "") or DATA_DIR
        path = QFileDialog.getExistingDirectory(self, "Папка для бэкапов", start)
        if not path:
            return
        t["path_dir"] = path
        self.save()
    def save(self):
        self.win.meta["export_tasks"] = self.tasks
        self.win.save_meta()
        self.populate()        
class QRDialog(QDialog):
    def __init__(self, title: str, payload_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("QR: " + (title or ""))
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(420, 480)
        lay = QVBoxLayout(self)
        self.lbl = QLabel("QR недоступен: установите пакет 'qrcode'.")
        self.lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl)
        row = QHBoxLayout()
        b_copy = QPushButton("Скопировать текст")
        def _copy_now():
            QApplication.clipboard().setText(payload_text)
            try:
                parent = self.parent()
                if hasattr(parent, "clip_timer") and hasattr(parent, "CLIPBOARD_SEC"):
                    parent.clip_timer.start(parent.CLIPBOARD_SEC * 1000)
            except Exception:
                pass
        b_copy.clicked.connect(_copy_now)
        b_save = QPushButton("Сохранить PNG…")
        b_save.clicked.connect(self.save_png)
        row.addWidget(b_copy)
        row.addStretch(1)
        row.addWidget(b_save)
        lay.addLayout(row)
        self._png_bytes = None
        try:
            import qrcode
            qr = qrcode.QRCode(border=2)
            qr.add_data(payload_text)
            img = qr.make_image()
            buf = io.BytesIO()
            img.save(buf, "PNG")
            self._png_bytes = buf.getvalue()
            pm = QPixmap()
            pm.loadFromData(self._png_bytes)
            self.lbl.setPixmap(
                pm.scaled(360, 360,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
            self.lbl.setText("")
        except Exception:
            pass
    def save_png(self):
        if not self._png_bytes:
            custom_warning(self, "QR", "Модуль 'qrcode' не установлен.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить QR", "qr.png", "PNG (*.png)")
        if not path:
            return
        with open(path, "wb") as f:
            f.write(self._png_bytes)
        custom_info(self, "QR", "Сохранено.")
class ExportTaskEditDialog(QDialog):
    def __init__(self, win: MainWindow, task: dict | None):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Задача экспорта")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(560, 360)
        self._task = dict(task) if task else {
            "name": "Экспорт",
            "type": "interval",
            "every_min": 60,
            "at_time": "02:00",
            "path_dir": "",
            "enc_pwd_enc": "",
            "seq": 1,
            "next_run": "",
        }
        lay = QVBoxLayout(self)
        self.e_name = QLineEdit(self._task.get("name", "Экспорт"))
        self.c_type = QComboBox(); self.c_type.addItems(["interval", "daily"])
        self.c_type.setCurrentText(self._task.get("type", "interval"))
        self.e_every = QSpinBox(); self.e_every.setRange(1, 100000)
        try:
            self.e_every.setValue(int(self._task.get("every_min", 60)))
        except Exception:
            self.e_every.setValue(60)
        self.e_time = QLineEdit(self._task.get("at_time", "02:00"))
        self.e_path = QLineEdit(self._task.get("path_dir", ""))
        b_browse = QPushButton("…"); b_browse.clicked.connect(self.pick_folder)
        self.e_pwd = QLineEdit(); self.e_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        try:
            cur_pwd = decrypt_value(self._task.get("enc_pwd_enc",""), self.win.fernet) if self._task.get("enc_pwd_enc") else ""
            if cur_pwd: self.e_pwd.setText(cur_pwd)
        except Exception:
            pass
        form = QGridLayout()
        rowi = 0
        form.addWidget(QLabel("Имя:"), rowi, 0); form.addWidget(self.e_name, rowi, 1, 1, 2); rowi += 1
        form.addWidget(QLabel("Тип:"), rowi, 0); form.addWidget(self.c_type, rowi, 1); rowi += 1
        form.addWidget(QLabel("Каждые (мин):"), rowi, 0); form.addWidget(self.e_every, rowi, 1); rowi += 1
        form.addWidget(QLabel("Время (ежедневно):"), rowi, 0); form.addWidget(self.e_time, rowi, 1); rowi += 1
        form.addWidget(QLabel("Папка для бэкапов:"), rowi, 0); form.addWidget(self.e_path, rowi, 1); form.addWidget(b_browse, rowi, 2); rowi += 1
        form.addWidget(QLabel("Пароль (LPX1):"), rowi, 0); form.addWidget(self.e_pwd, rowi, 1, 1, 2); rowi += 1
        lay.addLayout(form)
        btns = QHBoxLayout()
        ok = QPushButton("Сохранить"); ok.clicked.connect(self.accept)
        cancel = QPushButton("Отмена"); cancel.clicked.connect(self.reject)
        btns.addStretch(1); btns.addWidget(ok); btns.addWidget(cancel)
        lay.addLayout(btns)
        self.c_type.currentTextChanged.connect(self._toggle_rows)
        self._toggle_rows(self.c_type.currentText())
    def _toggle_rows(self, typ: str):
        self.e_every.setEnabled(typ == "interval")
        self.e_time.setEnabled(typ == "daily")
    def pick_folder(self):
        start = self.e_path.text().strip() or DATA_DIR
        path = QFileDialog.getExistingDirectory(self, "Папка для бэкапов", start)
        if path:
            self.e_path.setText(path)
    def value(self):
        ttype = self.c_type.currentText() or "interval"
        every = int(self.e_every.value())
        at    = self.e_time.text().strip() or "02:00"
        pwd   = self.e_pwd.text().strip()
        t = {
            "name": self.e_name.text().strip() or "Экспорт",
            "type": ttype,
            "every_min": every,
            "at_time": at,
            "path_dir": self.e_path.text().strip(),
            "enc_pwd_enc": encrypt_value(pwd, self.win.fernet) if pwd else self._task.get("enc_pwd_enc", ""),
            "seq": int(self._task.get("seq", 1)),
            "next_run": self.win.calc_next_run(ttype, every, at),
        }
        return t
def install_russian_translator(app: QApplication):
    try:
        QLocale.setDefault(QLocale(QLocale.Language.Russian, QLocale.Country.Russia))
    except Exception:
        pass
    try:
        from PySide6.QtCore import QTranslator, QLibraryInfo
        tr = QTranslator(app)
        try:
            path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        except Exception:
            path = os.path.join(sys.prefix, "Lib", "site-packages", "PySide6", "Qt6", "translations")
        if tr.load("qtbase_ru", path):
            app.installTranslator(tr)
    except Exception:
        pass
def ask_master_password() -> str | None:
    first_run = not os.path.exists(MASTER_FILE)
    dlg = MasterPasswordDialog(None, first_run=first_run)
    return dlg.value() if dlg.exec() == QDialog.DialogCode.Accepted else None
def _vault_exists() -> bool:
    try:
        if any(os.path.exists(p) for p in (TREE_FILE, BLOCKS_FILE, META_FILE, TRASH_FILE, INDEX_DB)):
            return True
        if os.path.isdir(ATTACH_DIR):
            with os.scandir(ATTACH_DIR) as it:
                for _ in it:
                    return True
    except Exception:
        pass
    return False
def main():
    print("[LinkPass] starting...")
    app = QApplication(sys.argv)
    install_russian_translator(app)
    app.setQuitOnLastWindowClosed(True)
    first_run = not os.path.exists(MASTER_FILE)
    if first_run:
        dlg = MasterPasswordDialog(None, first_run=True)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return 0
        master = dlg.value()
        try:
            win = MainWindow(master)
        except Exception as e:
            try:
                custom_error(None, "Ошибка запуска", f"Не удалось инициализировать приложение:\n{e}")
            except Exception:
                pass
            return 1
    else:
        win = None
        while True:
            dlg = MasterPasswordDialog(None, first_run=False)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return 0
            master = dlg.value()
            try:
                with open(MASTER_FILE, "r", encoding="utf-8") as f:
                    j = json.load(f)
                auth_salt = base64.b64decode(j.get("auth_salt", "") or b"")
                kdf_name  = j.get("kdf", "argon2id")
                kdf_params = j.get("kdf_params") or KDF_DEFAULTS
                calc = hash_for_auth(master, auth_salt, prefer_argon=(kdf_name == "argon2id"), params=kdf_params)
                if calc != j.get("verifier"):
                    custom_error(None, "Мастер‑пароль", "Неверный мастер‑пароль. Попробуйте ещё раз.")
                    continue
            except Exception:
                pass

            try:
                win = MainWindow(master)
                break
            except WrongMasterPasswordError:
                try:
                    custom_error(None, "Мастер‑пароль", "Неверный мастер‑пароль. Попробуйте ещё раз.")
                except Exception:
                    pass
                continue
            except Exception as e:
                try:
                    custom_error(None, "Ошибка запуска", f"Не удалось открыть хранилище:\n{e}")
                except Exception:
                    pass
                return 1
    win.show()
    try:
        win.raise_()
        win.activateWindow()
    except Exception:
        pass
    print("[LinkPass] UI shown; entering event loop")
    rc = app.exec()
    print("[LinkPass] exited", rc)
    return rc
if __name__ == "__main__":
    sys.exit(main())
