# Структура проекта data_translator

Документ объясняет, из каких файлов состоит проект, зачем нужен каждый слой и
в каком порядке всё это писать. Читать можно сверху вниз — разделы идут от
общей идеи к конкретным файлам.

---

## 1. Главная идея: четыре слоя и одно правило

Проект делает одну вещь: берёт значения из колонки таблицы, переводит их и
создаёт копию таблицы с переведёнными значениями. Но участвуют в этом четыре
совершенно разных вида кода, и если свалить их в один файл, проект станет
неподдерживаемым примерно на третьей неделе.

```
┌──────────────────────────────────────────────┐
│  views + forms + templates + static          │  Flask: HTML, формы, JSON-ручки
│  «что видит пользователь»                    │
└───────────────┬──────────────────────────────┘
                │ вызывает
┌───────────────▼──────────────────────────────┐
│  jobs                                        │  фоновые задания, прогресс, отмена
│  «долгая работа вне HTTP-запроса»            │
└───────────────┬──────────────────────────────┘
                │ вызывает
┌───────────────▼──────────────────────────────┐
│  core                                        │  сценарий перевода, кэш, глоссарий
│  «бизнес-логика, ничего не знает о Flask»    │
└───────────────┬──────────────────────────────┘
                │ вызывает через протокол
┌───────────────▼──────────────────────────────┐
│  db                                          │  два адаптера: ClickHouse, PostgreSQL
│  «как достать данные и создать таблицу»      │
└──────────────────────────────────────────────┘
```

**Правило зависимостей: стрелки идут только вниз.**

- `views` знает про `forms` и `jobs`. Про драйверы БД — не знает.
- `core` знает про `db` — но только через протокол (см. раздел 4). Про Flask
  не знает вообще: в `core/` не должно быть ни одного `from flask import ...`.
- `db` не знает ни про `core`, ни про Flask.

Проверка, что правило соблюдается, простая: **`core/` и `db/` должны
импортироваться и работать из обычного скрипта без Flask.** Если это так —
вы сможете отлаживать самую рискованную часть проекта в консоли за секунды,
вместо того чтобы каждый раз щёлкать по веб-форме. Это тот же приём, что в
проекте forecast_SP: ядро отдельно, обёртка тонкая.

---

## 2. Итоговое дерево файлов

```
data_translator/
├── translator.py                  точка входа: app = create_app()
├── requirements.txt
├── .flaskenv                      FLASK_APP, FLASK_DEBUG
├── .env                           пароли и адреса (в git не кладём)
├── docs/
│   └── STRUCTURE.md               этот файл
├── scripts/
│   └── smoke.py                   прогон пайплайна из консоли, без Flask
└── app/
    ├── __init__.py                фабрика create_app()
    ├── config.py                  чтение настроек из окружения
    ├── forms.py                   формы WTForms
    │
    ├── views/
    │   ├── __init__.py
    │   ├── main.py                GET/POST страницы index
    │   ├── api.py                 JSON-ручки для подсказок
    │   └── jobs.py                JSON-ручки прогресса и отмены
    │
    ├── db/
    │   ├── __init__.py            фабрика get_adapter()
    │   ├── base.py                протокол DbAdapter + общие типы
    │   ├── registry.py            реквизиты подключений в памяти, с TTL
    │   ├── clickhouse.py          реализация для ClickHouse
    │   └── postgres.py            реализация для PostgreSQL
    │
    ├── core/
    │   ├── __init__.py
    │   ├── prefilter.py           надо ли вообще переводить это значение
    │   ├── glossary.py            точные соответствия (ООО → LLC)
    │   ├── cache.py               словарь накопленных переводов
    │   ├── translate_client.py    клиент LibreTranslate
    │   └── pipeline.py            сценарий целиком
    │
    ├── jobs/
    │   ├── __init__.py
    │   └── registry.py            реестр заданий, потоки, прогресс
    │
    ├── templates/
    │   ├── base.html
    │   └── index.html
    └── static/
        ├── css/styles.css
        └── js/index.js            каскад подсказок
```

Ничего из этого не надо создавать сразу — порядок сборки в разделе 10.

---

## 3. Точка входа и фабрика

### `translator.py`

```python
from app import create_app

app = create_app()
```

Три строки. Всё.

### `app/__init__.py`

```python
from flask import Flask

from .config import Config


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    from .views.main import bp as main_bp
    from .views.api import bp as api_bp
    from .views.jobs import bp as jobs_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(jobs_bp, url_prefix="/api/jobs")

    return app
```

**Зачем фабрика, а не глобальный `app = Flask(__name__)`.** Функция, которая
создаёт приложение, позволяет создать его дважды с разными настройками — это
нужно для тестов. И она убирает циркулярный импорт: сейчас у вас
`__init__.py` импортирует `routes`, а `routes` импортирует `app` из
`__init__.py`. Работает, но ломается, как только файлов станет больше трёх.

**Что такое блюпринт.** Это группа роутов, которую можно зарегистрировать в
приложении. Вместо одного файла с двадцатью `@app.route` у вас три файла по
своей теме. `url_prefix="/api"` означает, что роут `@bp.route("/tables")`
внутри `api.py` будет доступен как `/api/tables` — префикс не надо повторять
в каждом декораторе.

### `app/config.py`

```python
import os


class Config:
    SECRET_KEY = os.environ["SECRET_KEY"]          # без него Flask-WTF не работает

    LIBRETRANSLATE_URL = os.environ["LIBRETRANSLATE_URL"]
    LIBRETRANSLATE_BATCH = int(os.environ.get("LIBRETRANSLATE_BATCH", "24"))

    # Реквизитов БД здесь НЕТ: их вводит пользователь, см. раздел 3.1.
    # В конфиге только то, что от пользователя не зависит.
    CH_PORT = int(os.environ.get("CH_PORT", "8123"))
    PG_PORT = int(os.environ.get("PG_PORT", "5432"))

    MAX_DISTINCT = int(os.environ.get("MAX_DISTINCT", "50000"))
    CONN_TTL_SECONDS = int(os.environ.get("CONN_TTL_SECONDS", "3600"))
```

### 3.1. Реквизиты подключения: где они живут

**Решение: пользователь вводит руками сервер, логин, пароль и имя базы.**

Это сильнее, чем служебный аккаунт в конфиге: каждый ходит в БД под собой,
значит права разграничиваются сами собой — человек видит ровно те таблицы,
которые ему разрешены, и создаёт новые от своего имени. Но за это надо
заплатить тремя вещами. Ни одну пропускать нельзя.

**Первое. Ручки подсказок обязаны быть POST, а не GET.**

Это главное следствие, и именно оно ломает наивную схему. Строка запроса
GET попадает в лог nginx, в лог werkzeug, в историю браузера, в заголовок
`Referer` и в логи любого прокси по дороге. Пароль в query string — это
пароль, записанный открытым текстом в пяти местах. Поэтому все ручки,
которым нужны реквизиты, принимают JSON в теле POST-запроса.

**Второе. Пароль нельзя класть в `flask.session`.**

Сессия Flask — это cookie, которая **подписана, но не зашифрована**.
Подпись защищает от подмены, а не от чтения: содержимое раскодируется
base64 в одну строку. Всё, что попало в `session`, пользователь (и любой,
кто добрался до cookie) может прочитать.

**Третье. Пароль всё равно придётся держать на сервере.**

Фоновое задание работает минутами уже после того, как HTTP-запрос
закончился, и ему нужно живое подключение. То есть выбор не между «хранить»
и «не хранить», а между «хранить в одном месте» и «гонять по сети при каждом
чихе». Первое лучше.

Отсюда конструкция — **реестр подключений**, `app/db/registry.py`:

```python
import secrets
import threading
import time
from dataclasses import dataclass


@dataclass
class ConnProfile:
    engine: str           # 'clickhouse' | 'postgres'
    host: str
    user: str
    password: str
    database: str
    created_at: float


_conns: dict[str, ConnProfile] = {}
_lock = threading.Lock()


def put(profile: ConnProfile) -> str:
    """Сохранить реквизиты и вернуть непредсказуемый идентификатор."""
    conn_id = secrets.token_urlsafe(32)
    with _lock:
        _conns[conn_id] = profile
    return conn_id


def get(conn_id: str, ttl: int) -> ConnProfile:
    with _lock:
        profile = _conns.get(conn_id)
    if profile is None or time.time() - profile.created_at > ttl:
        raise KeyError("подключение не найдено или устарело")
    return profile


def drop(conn_id: str) -> None:
    with _lock:
        _conns.pop(conn_id, None)
```

Как это работает целиком:

1. Пользователь заполняет движок, сервер, логин, пароль, базу и жмёт
   **«Подключиться»**.
2. `POST /api/connect` с этими полями в теле. Сервер пробует соединиться.
   Получилось — кладёт профиль в реестр и возвращает `conn_id`. Не
   получилось — возвращает понятную ошибку, и это, кстати, единственный
   момент, когда пользователь узнает, что опечатался в пароле.
3. Браузер запоминает `conn_id` и дальше шлёт **только его**: пароль
   пересекает сеть ровно один раз.
4. Задание при создании забирает `conn_id` себе, чтобы фоновый поток мог
   открыть соединение уже после ухода запроса.

`secrets.token_urlsafe(32)` — не `uuid4()`: идентификатор служит временным
ключом к живым реквизитам, и он должен быть криптографически непредсказуем.

**Ограничения, которые надо знать заранее:**

- Реестр в памяти процесса, значит `gunicorn -w 1` — то же ограничение, что
  у реестра заданий. При двух воркерах `conn_id`, выданный одним, будет
  неизвестен другому.
- Перезапуск приложения обнуляет реестр: все откроют форму заново. Для
  внутреннего инструмента это приемлемо.
- TTL обязателен (`CONN_TTL_SECONDS`), иначе пароли копятся в памяти до
  перезапуска. Плюс `drop()` по кнопке «Отключиться».

**И ещё: поднимите HTTPS**, если приложение доступно не только с localhost.
POST прячет пароль от логов, но не от того, кто слушает сеть.

---

## 4. Слой `db`: протокол и два адаптера

Это самая важная часть документа, потому что именно здесь чаще всего делают
ошибку — пишут «универсальный класс для работы с БД».

### 4.1. Почему не один класс на обе СУБД

ClickHouse и PostgreSQL устроены принципиально по-разному:

| | ClickHouse | PostgreSQL |
|---|---|---|
| протокол | HTTP, блоками колонок | своё соединение, построчно |
| библиотека | `clickhouse-connect` | `psycopg` (версия 3) |
| экранирование имён | обратные кавычки `` ` `` | `psycopg.sql.Identifier` |
| метаданные | `system.databases`, `system.tables`, `system.columns` | `information_schema`, `pg_catalog` |
| клонирование схемы | разбор `SHOW CREATE TABLE` | `CREATE TABLE (LIKE ... INCLUDING ALL)` |
| подстановка значений | `JOIN` или `dictGet` | `LEFT JOIN` + `coalesce` |

Единый класс, который делает вид, что это одно и то же, будет состоять из
`if self.kind == "clickhouse"` и превратится в кашу. Правильный подход: узкий
общий **протокол** и две независимые реализации.

### 4.2. `app/db/base.py` — протокол

```python
from dataclasses import dataclass
from typing import Iterator, Protocol


@dataclass(frozen=True)
class TableRef:
    """Ссылка на таблицу ВНУТРИ уже выбранной базы.

    Базы здесь нет намеренно: она часть реквизитов подключения, её вводит
    пользователь, и адаптер знает её с момента создания.

    `schema` актуальна только для PostgreSQL (по умолчанию 'public'). Это не
    придирка: в PostgreSQL подключаются К базе, а внутри адресуются
    `схема.таблица`. Если сюда подставить имя базы, получится обращение к
    схеме с таким именем — запрос не упадёт, просто ничего не найдёт.
    В ClickHouse пространство имён — это и есть база, поэтому `schema = None`.
    """
    table: str
    schema: str | None = None


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    is_virtual: bool      # MATERIALIZED/ALIAS в CH, генерируемые в PG — в них нельзя вставлять
    translatable: bool    # строковый тип, который вообще имеет смысл переводить


class DbAdapter(Protocol):
    """Контракт, который обязаны выполнять оба адаптера.

    Метода list_databases() здесь нет: чтобы перечислить базы, надо уже быть
    авторизованным, а базу пользователь называет сам вместе с реквизитами.
    """

    # --- чтение метаданных: питает подсказки в форме ---
    def probe(self) -> None: ...
    def list_tables(self) -> list[str]: ...
    def list_columns(self, ref: TableRef) -> list[Column]: ...

    # --- чтение значений ---
    def count_distinct(self, ref: TableRef, column: str) -> int: ...
    def iter_distinct(self, ref: TableRef, column: str) -> Iterator[str]: ...

    # --- запись результата ---
    def create_table_like(self, src: TableRef, dst: TableRef) -> None: ...
    def upload_mapping(self, mapping_ref: TableRef,
                       rows: list[tuple[str, str, str]]) -> None: ...
    def apply_mapping(self, src: TableRef, dst: TableRef,
                      mapping_ref: TableRef, columns: list[str]) -> None: ...
```

**Почему протокол именно такой гранулярности.** Это ключевой момент, его
стоит понять.

Слишком низкий уровень — `execute(sql: str)` — плохо: тогда SQL начнёт
писаться в `core/`, и диалектные различия (обратные кавычки, `SHOW CREATE`,
`dictGet`) протекут в бизнес-логику. Через месяц вы не сможете ответить на
вопрос «где у нас формируется DDL».

Слишком высокий уровень — `translate_table(...)` — тоже плохо: тогда вся
логика (кэш, глоссарий, батчинг) уедет внутрь адаптера и продублируется
дважды, для CH и для PG.

Правильный уровень — «операции над данными без знания, зачем они нужны».
Адаптер умеет «дай уникальные значения» и «примени маппинг». Он не знает,
что это перевод.

`Protocol` из `typing` — это способ описать контракт без наследования: класс
считается соответствующим, если у него есть нужные методы. Наследоваться от
`DbAdapter` не обязательно, он нужен для проверки типов и как документация.

### 4.3. `app/db/__init__.py` — фабрика

```python
from .base import DbAdapter, TableRef, Column
from .clickhouse import ClickHouseAdapter
from .postgres import PostgresAdapter


def get_adapter(profile: ConnProfile, config) -> DbAdapter:
    if profile.engine == "clickhouse":
        return ClickHouseAdapter(host=profile.host, port=config.CH_PORT,
                                 user=profile.user, password=profile.password,
                                 database=profile.database)
    if profile.engine == "postgres":
        return PostgresAdapter(host=profile.host, port=config.PG_PORT,
                               user=profile.user, password=profile.password,
                               database=profile.database)
    raise ValueError(f"неизвестный движок: {profile.engine}")
```

Одно место, где решается, какой адаптер создать. Весь остальной код работает
с результатом и не знает, что именно ему досталось.

### 4.4. `app/db/clickhouse.py` — на что обратить внимание

```python
import clickhouse_connect

from .base import Column, TableRef


def qi(name: str) -> str:
    """Экранирование идентификатора для ClickHouse."""
    return "`" + name.replace("`", "``") + "`"


class ClickHouseAdapter:
    def __init__(self, host, port, user, password, database):
        self._database = database
        self._client = clickhouse_connect.get_client(
            host=host, port=port, username=user, password=password,
            database=database,
        )

    def list_columns(self, ref: TableRef) -> list[Column]:
        rows = self._client.query(
            "SELECT name, type, default_kind FROM system.columns "
            "WHERE database = {db:String} AND table = {tbl:String} ORDER BY position",
            parameters={"db": self._database, "tbl": ref.table},
        ).result_rows
        return [
            Column(
                name=name,
                type=type_,
                is_virtual=default_kind in ("MATERIALIZED", "ALIAS"),
                translatable=("String" in type_ or "FixedString" in type_),
            )
            for name, type_, default_kind in rows
        ]
```

Обратите внимание: значения подставляются через `parameters`, а не через
f-строку. А вот **имена таблиц и колонок через параметры подставить нельзя** —
они не значения, они часть синтаксиса. Для них `qi()`.

Четыре подводных камня ClickHouse, которые проявятся именно на этом проекте:

1. **`CREATE TABLE new AS orig` копирует движок дословно.** Для
   `ReplicatedMergeTree` это означает тот же путь в ZooKeeper — то есть две
   таблицы будут писать в одну реплику. Берите `SHOW CREATE TABLE`, меняйте
   имя **и** ZK-путь.
2. **`MATERIALIZED` и `ALIAS` колонки нельзя вставлять.** Их надо исключить из
   списка `INSERT`. Для этого в `Column` и заведён флаг `is_virtual`.
3. **`Enum8`/`Enum16`: переведённое значение не влезет в тип** — значения
   перечисления являются частью определения типа. Такие колонки в v1 просто
   не предлагайте к переводу.
4. **Промах `LEFT JOIN` в ClickHouse даёт значение по умолчанию для типа, а не
   NULL.** Для `String` это пустая строка. То есть непереведённые значения
   молча превратятся в `''`. Лечится либо типом `Nullable(String)` у колонки
   `dst` в маппинг-таблице, либо настройкой `join_use_nulls = 1`. Это ровно
   тот баг, который съедает день, если о нём не знать заранее.

### 4.5. `app/db/postgres.py` — на что обратить внимание

```python
import psycopg
from psycopg import sql

from .base import Column, TableRef


class PostgresAdapter:
    def __init__(self, host, port, user, password, database):
        # Параметры словарём, а не строкой DSN: строка с password=... норовит
        # попасть в текст исключения, а оттуда — в отчёт задания на экране.
        self._params = dict(host=host, port=port, user=user,
                            password=password, dbname=database)

    def iter_distinct(self, ref: TableRef, column: str):
        query = sql.SQL("SELECT DISTINCT {col} FROM {tbl} WHERE {col} IS NOT NULL").format(
            col=sql.Identifier(column),
            tbl=sql.Identifier(ref.schema or "public", ref.table),
        )
        with psycopg.connect(**self._params) as conn:
            with conn.cursor(name="distinct_cur") as cur:   # name → курсор на сервере
                cur.itersize = 10_000
                cur.execute(query)
                for (value,) in cur:
                    yield value
```

Здесь два приёма, ради которых мы и выбрали psycopg 3:

- **`sql.Identifier`** экранирует имена правильно и с учётом кавычек.
  Это и есть настоящая защита от инъекции — не регулярка на входе формы.
  Регулярка полезна как дополнительная проверка, но не как граница
  безопасности.
- **`cursor(name=...)`** создаёт курсор на стороне сервера. Без имени psycopg
  втянет весь результат в память; на таблице с миллионом distinct это убьёт
  процесс.

Клонирование схемы в PostgreSQL проще, чем в ClickHouse:

```sql
CREATE TABLE new_t (LIKE orig_t INCLUDING ALL);
```

`INCLUDING ALL` переносит типы, значения по умолчанию, индексы, ограничения
CHECK, identity-колонки и комментарии. **Не переносит внешние ключи и
триггеры** — если они нужны, это отдельный шаг.

### 4.6. Как применяется маппинг

Главное правило: **строки не должны проходить через Python.** Вы заливаете
маленькую таблицу соответствий и делаете перенос одним запросом внутри СУБД.

Маппинг-таблица одна на все колонки, с колонкой-дискриминатором:

```
mapping(col_name String, src String, dst Nullable(String))
```

PostgreSQL, перевод двух колонок `a` и `c`:

```sql
INSERT INTO new_t (a, b, c)
SELECT
    coalesce(m_a.dst, t.a),
    t.b,
    coalesce(m_c.dst, t.c)
FROM orig_t t
LEFT JOIN mapping m_a ON m_a.col_name = 'a' AND m_a.src = t.a
LEFT JOIN mapping m_c ON m_c.col_name = 'c' AND m_c.src = t.c;
```

`coalesce(m.dst, t.a)` означает «если перевода нет — оставь оригинал». Это
важно не только для пропусков: между моментом, когда вы собрали distinct, и
моментом вставки в таблицу могли появиться новые значения. Без `coalesce` они
станут NULL.

ClickHouse — то же самое, но помните про пункт 4 выше: либо `Nullable(String)`,
либо `SETTINGS join_use_nulls = 1`.

---

## 5. Слой `core`: сценарий перевода

Здесь живёт вся логика, и ни одной строчки про Flask или про SQL.

### `core/prefilter.py`

Решает, надо ли вообще отправлять значение в модель. Это самая
высокоокупаемая часть проекта: она и ускоряет прогон в разы, и убирает
галлюцинации на нетекстовом входе.

```python
import re

CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")


def needs_translation(value: str | None) -> bool:
    if value is None:
        return False
    if not value.strip():
        return False
    if not CYRILLIC.search(value):     # уже латиница, код, число — не трогаем
        return False
    return True


def normalize_key(value: str) -> str:
    """Ключ для кэша и глоссария: схлопывает варианты записи одного и того же."""
    v = value.replace(" ", " ")     # неразрывный пробел
    v = v.replace("ё", "е").replace("Ё", "Е")
    v = re.sub(r"\s+", " ", v).strip()
    return v.casefold()
```

Про `normalize_key`: `«Ключ»`, `«ключ »` и `«ключ»` с неразрывным пробелом —
это три разных distinct-значения, которые должны перевестись одинаково.
Нормализуем **ключ**, но в БД пишем и возвращаем **оригинал**.

### `core/glossary.py`

Точные соответствия, которые применяются **до** модели. NMT-движок не умеет
принимать глоссарий, поэтому это единственный способ зафиксировать
терминологию.

```python
GLOSSARY = {
    "ооо": "LLC",
    "ао": "JSC",
    "инн": "TIN",
    "оквэд": "OKVED",
}


def lookup(normalized_key: str) -> str | None:
    return GLOSSARY.get(normalized_key)
```

В v1 — словарь в коде. Позже — таблица в БД с веб-редактором.

### `core/cache.py`

Накопленный словарь переводов. **Это не оптимизация, а то, что обеспечивает
консистентность:** без него одно и то же значение в двух таблицах
переведётся по-разному, и никто этого не заметит.

Одна таблица:

```sql
CREATE TABLE translation_cache (
    key_hash    text PRIMARY KEY,   -- хэш от (normalized_key, engine, model_version)
    src         text NOT NULL,      -- оригинал как есть
    dst         text NOT NULL,
    engine      text NOT NULL,      -- 'libretranslate'
    model_ver   text NOT NULL,
    created_at  timestamptz DEFAULT now()
);
```

Версия модели в ключе обязательна: после обновления Argos переводы поедут, и
вы должны уметь отличить старые от новых, а не получить смесь.

### `core/translate_client.py`

```python
import requests


class LibreTranslateClient:
    def __init__(self, url, batch_size=24, timeout=60):
        self._url = url.rstrip("/") + "/translate"
        self._batch = batch_size
        self._timeout = timeout

    def translate(self, texts: list[str]) -> list[str]:
        out = []
        for i in range(0, len(texts), self._batch):
            chunk = texts[i:i + self._batch]
            resp = requests.post(
                self._url,
                json={"q": chunk, "source": "ru", "target": "en", "format": "text"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            result = resp.json()["translatedText"]
            if len(result) != len(chunk):
                raise RuntimeError(f"движок вернул {len(result)} вместо {len(chunk)}")
            out.extend(result)
        return out
```

Три обязательных детали:

- **`q` — массив.** LibreTranslate поддерживает батчи, и это главное, что
  спасает от «запрос на каждую строку». Размер батча подберите под
  `--batch-limit` вашего инстанса.
- **`source: "ru"` строго, никогда `auto`.** Автоопределение языка на коротких
  метках ошибается и переводит с неверного языка молча, без ошибки.
- **Проверка длины ответа.** Если движок вернул меньше элементов, чем приняли,
  дальше всё поедет со сдвигом. Падать надо здесь, а не через три таблицы.

Интерфейс из одного метода `translate(list) -> list` — это то, что делает
движок сменным. Захотите уйти на opus-mt-tc-big или на локальную LLM — новый
класс с тем же методом, остальной код не меняется.

### `core/pipeline.py`

Сценарий целиком, одной функцией, вызываемой откуда угодно — из Flask, из
консоли, из теста.

```python
def run(adapter, translator, cache, src: TableRef, dst: TableRef,
        columns: list[str], progress=None, cancelled=None) -> Report:
    ...
```

`progress` — функция обратного вызова, которую пайплайн дёргает по мере
работы. `cancelled` — функция, возвращающая `True`, если задание попросили
отменить. Так пайплайн ничего не знает ни о реестре заданий, ни о HTTP, но
умеет сообщать о прогрессе и останавливаться.

Порядок шагов:

```
1. Проверить, что таблица и колонки реально существуют
2. Для каждой колонки: count_distinct → если больше MAX_DISTINCT, отказ
3. iter_distinct → набрать значения
4. Разложить каждое значение по четырём корзинам:
      не требует перевода (prefilter)  → оригинал
      попало в глоссарий               → из глоссария
      есть в кэше                      → из кэша
      остальное                        → в очередь на перевод
5. Очередь → батчами в translator
6. Новые переводы записать в кэш
7. Собрать маппинг → upload_mapping
8. create_table_like → apply_mapping
9. Вернуть Report: сколько переведено, взято из кэша, пропущено, ошибок
```

Шаг 4 — то, ради чего стоит весь этот слой: на реальных данных в модель уйдёт
меньшая часть значений, остальное закроется фильтром, глоссарием и кэшем.

---

## 6. Слой `jobs`: почему нельзя просто в обработчике запроса

Перевод десяти тысяч значений — это минуты. HTTP-запрос столько не живёт:
gunicorn по умолчанию рвёт соединение через 30 секунд, браузер — раньше.
Поэтому обработчик формы должен **создать задание и сразу вернуть ответ**, а
работа идёт в отдельном потоке.

### `app/jobs/registry.py`

```python
import threading
import uuid
from dataclasses import dataclass, field


@dataclass
class Job:
    id: str
    status: str = "pending"        # pending | running | done | error | cancelled
    total: int = 0
    done: int = 0
    message: str = ""
    report: dict | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def submit(fn, *args, **kwargs) -> str:
    job = Job(id=uuid.uuid4().hex)
    with _lock:
        _jobs[job.id] = job

    def runner():
        job.status = "running"
        try:
            job.report = fn(*args, progress=_make_progress(job),
                            cancelled=job._cancel.is_set, **kwargs)
            job.status = "cancelled" if job._cancel.is_set() else "done"
        except Exception as exc:
            job.status = "error"
            job.message = str(exc)

    threading.Thread(target=runner, daemon=True).start()
    return job.id
```

**Осознанное упрощение v1: реестр живёт в памяти процесса.** Это значит, что
приложение обязано запускаться в **один воркер** (`gunicorn -w 1`). С двумя
воркерами запрос о прогрессе попадёт в процесс, который про это задание не
знает, и вы получите 404 на ровном месте.

Путь наверх, когда упрётесь: таблица `jobs` в PostgreSQL вместо словаря.
Менять придётся только этот файл — потому что пайплайн ничего про реестр не
знает.

---

## 7. Слой `views` и `forms`: ручной ввод плюс выпадающий список

Вот здесь ваше требование. Разберу подробно, потому что тут есть ровно одна
правильная конструкция и одна распространённая ошибка.

### 7.1. Почему не `SelectField`

Рефлекс «выпадающий список → `SelectField`» здесь не работает. `SelectField`:

- требует, чтобы `choices` были известны в момент отрисовки страницы — а у вас
  список баз становится известен только после того, как пользователь ввёл
  сервер;
- **проверяет значение по списку и отвергает всё остальное** — то есть
  запрещает ручной ввод, который вы хотите.

### 7.2. Правильная конструкция: `StringField` + `<datalist>`

`<datalist>` — это штатный HTML-элемент: поле остаётся обычным текстовым
вводом, но браузер показывает подсказки из списка. Ровно «печатаю руками, но
могу и выбрать».

```html
<input list="dl-table" name="table" id="table" autocomplete="off">
<datalist id="dl-table"></datalist>
```

Подсказки нужны там, где список можно получить, уже зная реквизиты:
**таблицы и колонки**. Сервер, логин, пароль и база — обычные поля без
`<datalist>`, их узнать заранее неоткуда.

Со стороны WTForms это просто `StringField`. Атрибут `list` передаётся при
отрисовке: **любые незнакомые именованные аргументы WTForms превращает в
HTML-атрибуты.** Это тот механизм, который здесь нужен:

```jinja
{{ form.table(list="dl-table", autocomplete="off", class="input") }}
```

`autocomplete="off"` нужен, чтобы браузер не подмешивал к вашим подсказкам
свою историю ввода.

### 7.3. `app/forms.py`

```python
from flask_wtf import FlaskForm
from wtforms import (FieldList, PasswordField, SelectField, StringField,
                     SubmitField)
from wtforms.validators import DataRequired, Length, Regexp

IDENT = Regexp(r"^[^\x00\n\r`\"]+$", message="недопустимые символы в имени")


class TranslateForm(FlaskForm):
    # Здесь SelectField уместен: движков ровно два, они известны заранее
    engine = SelectField("СУБД", choices=[
        ("clickhouse", "ClickHouse"),
        ("postgres", "PostgreSQL"),
    ])

    # --- реквизиты: только ручной ввод, подсказок взять неоткуда ---
    server = StringField("Сервер", validators=[DataRequired(), Length(max=255)])
    user = StringField("Пользователь", validators=[DataRequired(), Length(max=128)])
    password = PasswordField("Пароль",
                             render_kw={"autocomplete": "new-password"})
    database = StringField("База данных", validators=[DataRequired(), IDENT])

    # --- а вот здесь список уже можно получить: ручной ввод + <datalist> ---
    table = StringField("Таблица", validators=[DataRequired(), IDENT])

    # Несколько колонок: FieldList даёт columns-0, columns-1, ...
    columns = FieldList(StringField("Колонка", validators=[IDENT]), min_entries=1)

    target_table = StringField("Новая таблица", validators=[DataRequired(), IDENT])

    submit = SubmitField("Перевести")
```

Два момента про `PasswordField`. Он рисует `<input type="password">`, то есть
браузер прячет ввод. И у него по умолчанию `hide_value=True`: если форма не
прошла валидацию и страница перерисовывается, пароль **не** подставляется
обратно в HTML. Это поведение нужное, не отключайте его ради удобства.

`autocomplete="new-password"` — практичнее, чем `"off"`: современные браузеры
`off` на полях пароля часто игнорируют, а `new-password` уважают.

`FieldList` — это список однотипных полей. WTForms сам разберёт пришедшие
`columns-0`, `columns-1` и так далее; JS добавляет новые строки по кнопке.

**Про валидацию.** Форма проверяет только форму: заполнено, не слишком длинно,
нет управляющих символов. Проверить, что колонка реально существует, форма не
может — для этого нужно соединение с БД. Поэтому проверка в два этапа:

```python
if form.validate_on_submit():               # 1. форма корректна по виду
    errors = validate_against_db(form, adapter)   # 2. объекты реально есть
    if errors:
        ...
```

Регулярка `IDENT` — **не граница безопасности**. Настоящая защита от инъекции
— `sql.Identifier` и `qi()` в адаптерах. Регулярка просто отсекает
бессмысленный ввод пораньше.

### 7.4. `app/views/main.py`

```python
from flask import Blueprint, current_app, render_template

from ..forms import TranslateForm

bp = Blueprint("main", __name__)


@bp.route("/", methods=["GET", "POST"])
@bp.route("/index", methods=["GET", "POST"])
def index():
    form = TranslateForm()
    if form.validate_on_submit():
        ...     # проверить по БД, создать задание, отдать job_id
    return render_template("index.html", form=form)
```

### 7.5. `app/views/api.py` — ручки для подсказок

Списки зависят от того, что уже введено, поэтому их нельзя отдать вместе со
страницей. Нужны JSON-ручки — и все они **POST**, потому что в теле ходят
реквизиты либо ключ к ним (см. 3.1):

```
POST /api/connect    {engine, server, user, password, database}  -> {conn_id}
POST /api/tables     {conn_id}                                   -> [имена]
POST /api/columns    {conn_id, table}                            -> [{name,type,...}]
POST /api/disconnect {conn_id}
```

`GET` здесь был бы ошибкой: параметры строки запроса попадают в логи и в
историю браузера.

```python
@bp.post("/connect")
def connect():
    data = request.get_json()
    profile = ConnProfile(
        engine=data["engine"], host=data["server"], user=data["user"],
        password=data["password"], database=data["database"],
        created_at=time.time(),
    )
    adapter = get_adapter(profile, current_app.config)
    try:
        adapter.probe()
    except Exception as exc:
        return jsonify({"error": safe_message(exc)}), 400
    return jsonify({"conn_id": registry.put(profile)})


@bp.post("/columns")
def columns():
    data = request.get_json()
    profile = registry.get(data["conn_id"], current_app.config.CONN_TTL_SECONDS)
    adapter = get_adapter(profile, current_app.config)
    cols = adapter.list_columns(TableRef(table=data["table"]))
    return jsonify([
        {"name": c.name, "type": c.type, "translatable": c.translatable}
        for c in cols if not c.is_virtual
    ])
```

`safe_message(exc)` — маленькая функция, которая вырезает из текста
исключения возможные реквизиты. Драйверы любят включать в сообщение об
ошибке параметры подключения, и это сообщение вы собираетесь показать на
экране.

`/api/columns` отдаёт и тип, и признак `translatable` — чтобы в интерфейсе
можно было показать, что переводить эту колонку бессмысленно (число, дата), и
отфильтровать виртуальные колонки, в которые всё равно нельзя вставить.

---

## 8. `static/js/index.js` — каскад подсказок

Логика простая: как пользователь закончил вводить поле, подтягиваем список
для следующего.

```javascript
let connId = null;

async function post(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || resp.statusText);
  return data;
}

function fillDatalist(listId, items) {
  const dl = document.getElementById(listId);
  dl.innerHTML = "";
  for (const item of items) {
    const opt = document.createElement("option");
    opt.value = (typeof item === "string") ? item : item.name;
    dl.appendChild(opt);
  }
}

// Шаг 1: пароль уходит по сети ровно здесь и больше нигде
document.getElementById("btn-connect").addEventListener("click", async () => {
  try {
    const data = await post("/api/connect", {
      engine:   document.getElementById("engine").value,
      server:   document.getElementById("server").value,
      user:     document.getElementById("user").value,
      password: document.getElementById("password").value,
      database: document.getElementById("database").value,
    });
    connId = data.conn_id;
    document.getElementById("password").value = "";   // из DOM он больше не нужен
    fillDatalist("dl-table", (await post("/api/tables", {conn_id: connId})));
    showOk("Подключено");
  } catch (e) {
    showError(e.message);
  }
});

// Шаг 2: колонки — когда названа таблица
document.getElementById("table").addEventListener("change", async (ev) => {
  if (!connId) { showError("Сначала подключитесь"); return; }
  try {
    const cols = await post("/api/columns", {conn_id: connId, table: ev.target.value});
    fillDatalist("dl-columns", cols.filter(c => c.translatable));
  } catch (e) {
    showError(e.message);
  }
});
```

Три вещи, которые здесь сделаны намеренно:

- **Кнопка «Подключиться» — обязательный элемент, а не удобство.** Она
  единственное место, где человек узнает, что ошибся в пароле или адресе.
- **Поле пароля очищается сразу после успеха.** Дальше нужен только
  `conn_id`, а пароль в DOM — это пароль, который увидит любой, кто откроет
  инструменты разработчика или сделает скриншот.
- **Список колонок фильтруется по `translatable`.** Предлагать к переводу
  числа и даты бессмысленно.

Событие `change` у текстового поля срабатывает при потере фокуса — то есть
когда пользователь дописал и ушёл дальше. Это то поведение, которое нужно;
`input` дёргал бы сервер на каждую букву.

**Обязательно показывайте ошибку.** Пустой список подсказок выглядит как
«здесь ничего нет», и человек будет искать проблему у себя, а не в
реквизитах.

Всё это лежит на одной странице `index.html`, как вы и хотели: форма, блок
ошибок, блок прогресса задания.

---

## 9. Полный сценарий одного прогона

```
пользователь                       браузер            Flask                фон
────────────┬──────────────────────────┬─────────────────┬──────────────────┬───
выбрал СУБД │                          │                 │                  │
ввёл сервер,│                          │                 │                  │
логин,пароль│                          │                 │                  │
и базу      │                          │                 │                  │
«Подключить-├─ POST /api/connect ─────►│ probe() + registry.put()           │
ся»         │◄── {"conn_id": "..."} ───┤                 │                  │
            ├─ POST /api/tables ──────►│                 │                  │
            │◄── список таблиц ────────┤                 │                  │
ввёл таблицу├─ POST /api/columns ─────►│                 │                  │
            │◄── список колонок ───────┤                 │                  │
выбрал      │                          │                 │                  │
колонки,    │                          │                 │                  │
нажал       ├─ POST / ────────────────►│ validate_on_submit                 │
«Перевести» │                          │ validate_against_db                │
            │                          │ jobs.submit() ──┼─────────────────►│
            │◄── {"job_id": "..."} ────┤                 │   count_distinct │
            │                          │                 │   iter_distinct  │
            ├─ GET /api/jobs/<id> ────►│                 │   prefilter      │
            │◄── {"done": 120, ...} ───┤                 │   glossary/cache │
            ├─ GET /api/jobs/<id> ────►│                 │   translate      │
            │◄── {"done": 4300, ...} ──┤                 │   upload_mapping │
            │                          │                 │   create_table   │
            ├─ GET /api/jobs/<id> ────►│                 │   apply_mapping  │
            │◄── {"status": "done"} ───┤◄────────────────┼──────── Report ──┤
показали    │                          │                 │                  │
отчёт       │                          │                 │                  │
```

Опрос прогресса — раз в 1–2 секунды, обычным `setInterval` с `fetch`.

---

## 10. Порядок сборки

Это, пожалуй, важнее самой структуры. Тот же принцип, что сработал в
forecast_SP: **сначала то, где риск, а не то, что видно.**

**Шаг 1. `db/base.py` + `db/clickhouse.py` + `scripts/smoke.py`.**
Скрипт без Flask: подключиться, перечислить базы, таблицы, колонки,
посчитать distinct. Здесь вы узнаете все реальные особенности своих
серверов, и узнаете их за минуты, а не через веб-форму.

**Шаг 2. `core/prefilter.py` и `core/translate_client.py`.**
Тот же `smoke.py` дополняете: взять 200 настоящих distinct-значений, прогнать
через фильтр и через LibreTranslate, распечатать парами. **Это и есть тот
замер качества, о котором мы говорили** — глазами по 200 строкам вы поймёте,
годится движок или нет. До того, как написана хоть одна строка веб-интерфейса.

**Шаг 3. `core/cache.py` и `core/glossary.py`.**
Таблица кэша, запись и чтение. Повторный прогон `smoke.py` должен почти не
обращаться к движку.

**Шаг 4. `core/pipeline.py` целиком, вызываемый из `smoke.py`.**
Здесь же `create_table_like` и `apply_mapping` — и здесь вы наступите на
ZooKeeper-путь, на виртуальные колонки и на пустые строки вместо NULL.
Лучше наступить в консоли.

**Шаг 5. Только теперь Flask:** фабрика, конфиг, форма, `index.html`,
`api.py`, `jobs/registry.py`, JS.

**Шаг 6. `postgres.py`** — второй адаптер по уже проверенному протоколу.
К этому моменту вы точно знаете, какой контракт вам на самом деле нужен, и
второй адаптер напишется за вечер.

Соблазн начать с интерфейса, потому что он виден, велик. Но интерфейс — это
самая простая и самая переделываемая часть, а протокол адаптера и пайплайн —
самая дорогая.

---

## 11. Чего в v1 сознательно не делаем

Список нужен, чтобы не расползтись:

- **Celery, Redis, RQ** — `threading.Thread` и словарь в памяти достаточно
  при `-w 1`. Очередь понадобится, когда появятся параллельные задания.
- **SQLAlchemy и любой ORM** — вы пишете DDL и bulk-SQL, ORM тут только
  мешает. Рефлекс «Flask → Flask-SQLAlchemy» подавить.
- **Авторизация** — внутренний инструмент. Но помните: форма принимает адрес
  сервера, то есть приложение может подключиться куда угодно в вашей сети.
  Если это выйдет за пределы вашей команды, понадобится whitelist хостов.
- **Переименование колонок** — вы сами решили оставить оригинальные имена.
  Правильно: одна задача за раз.
- **Enum-колонки в ClickHouse** — просто не предлагайте их к переводу.
- **Годная обработка длинных текстов** — если в колонке абзацы, а не метки,
  понадобится разбивка по предложениям. Отдельная задача, не сейчас.

---

## 12. Короткая шпаргалка «что где лежит»

| Вопрос | Файл |
|---|---|
| Как подключиться к ClickHouse | `app/db/clickhouse.py` |
| Как подключиться к PostgreSQL | `app/db/postgres.py` |
| Какой SQL создаёт новую таблицу | `app/db/*.py`, метод `create_table_like` |
| Надо ли переводить это значение | `app/core/prefilter.py` |
| Как зафиксировать перевод термина | `app/core/glossary.py` |
| Где хранятся прошлые переводы | `app/core/cache.py` |
| Как устроен запрос к LibreTranslate | `app/core/translate_client.py` |
| В каком порядке всё происходит | `app/core/pipeline.py` |
| Почему задание не блокирует страницу | `app/jobs/registry.py` |
| Какие поля в форме | `app/forms.py` |
| Откуда берутся подсказки в полях | `app/views/api.py` + `static/js/index.js` |
| Где живут реквизиты пользователя | `app/db/registry.py`, в памяти, с TTL |
| Почему ручки подсказок POST, а не GET | раздел 3.1 |
| Что лежит в `.env` | только `SECRET_KEY`, URL движка и порты |
