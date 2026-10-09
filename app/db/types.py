import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Sequence

from .base import (
    BIGINT,
    BOOL,
    DATE,
    DATETIME,
    DATETIMETZ,
    DECIMAL,
    FLOAT,
    INT,
    TEXT,
    UBIGINT,
    UUID,
    Column,
)

@dataclass(frozen=True)
class Limits:
    min_time: datetime
    max_time: datetime
    min_date: date
    max_date: date


PG_DDL = {
    TEXT: 'text',
    INT: 'integer',
    BIGINT: 'bigint',
    UBIGINT: 'numeric(20, 0)',
    FLOAT: 'double precision',
    DECIMAL: 'numeric',
    BOOL: 'boolean',
    DATE: 'date',
    DATETIME: 'timestamp',
    DATETIMETZ: 'timestamptz',
    UUID: 'uuid',
}

CH_DDL = {
    TEXT: 'String',
    INT: 'Int32',
    BIGINT: 'Int64',
    UBIGINT: 'UInt64',
    FLOAT: 'Float64',
    DECIMAL: 'Decimal(38, 9)',
    BOOL: 'Bool',
    DATE: 'Date32',
    DATETIME: "DateTime64(3, 'UTC')",
    DATETIMETZ: "DateTime64(3, 'UTC')",
    UUID: 'UUID',
}

PG_KINDS = {
    'int2': INT,
    'int4': INT,
    'oid': INT,
    'int8': BIGINT,
    'float4': FLOAT,
    'float8': FLOAT,
    'numeric': DECIMAL,
    'bool': BOOL,
    'date': DATE,
    'timestamp': DATETIME,
    'timestamptz': DATETIMETZ,
    'uuid': UUID,
}

CH_KINDS = {
    'Int8': INT,
    'Int16': INT,
    'Int32': INT,
    'UInt8': INT,
    'UInt16': INT,
    'UInt32': BIGINT,
    'Int64': BIGINT,
    'UInt64': UBIGINT,
    'Float32': FLOAT,
    'Float64': FLOAT,
    'Bool': BOOL,
    'Boolean': BOOL,
    'Date': DATE,
    'Date32': DATE,
    'UUID': UUID,
}

CH_WRAPPERS = ('Nullable', 'LowCardinality', 'SimpleAggregateFunction')
CH_DECIMAL = re.compile(r'^Decimal(32|64|128|256)?\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)$')
CH_DECIMAL_WIDTH = {'32': 9, '64': 18, '128': 38, '256': 76}

PG_NAME_LIMIT = 63
CH_PRECISION_LIMIT = 76

CH_WIDE_LIMITS = Limits(
    datetime(1900, 1, 1, tzinfo=timezone.utc),
    datetime(2299, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
    date(1900, 1, 1),
    date(2299, 12, 31),
)
CH_SAFE_LIMITS = Limits(
    datetime(1970, 1, 1, tzinfo=timezone.utc),
    datetime(2106, 2, 7, 6, 28, 15, tzinfo=timezone.utc),
    date(1970, 1, 1),
    date(2149, 6, 6),
)

CH_SAFE_DIGITS = 28
CH_SAFE_SCALE_LIMIT = CH_SAFE_DIGITS // 2
CH_SAFE_DEFAULT_DECIMAL = (24, 4)

CH_SAFE_DDL = {
    DATE: 'Date',
    DATETIME: 'DateTime',
    DATETIMETZ: "DateTime('UTC')",
}

CH_RESTRICTED = ('Date32', 'DateTime64', 'Decimal')
CH_TIMEZONE = re.compile(r"'([^']+)'")

CH_CASTABLE = (
    'String', 'FixedString', 'Enum8', 'Enum16', 'UUID', 'IPv4', 'IPv6',
    'Date', 'Date32', 'DateTime', 'DateTime64',
    'Int8', 'Int16', 'Int32', 'Int64', 'UInt8', 'UInt16', 'UInt32', 'UInt64',
    'Float32', 'Float64', 'Decimal', 'Bool', 'Boolean', 'Nothing',
)


def clip_name(name: str, limit: int = PG_NAME_LIMIT) -> str:
    data = (name or '').encode('utf-8')
    if len(data) <= limit:
        return name
    return data[:limit].decode('utf-8', 'ignore')


def pg_name(oid: int, name: str, array_oid: int) -> str:
    if array_oid and oid == array_oid:
        return (name or '') + '[]'
    return name or str(oid)


def pg_kind(type_name: str) -> str:
    return PG_KINDS.get((type_name or '').lower(), TEXT)


def unwrap_ch(type_name: str) -> str:
    text = (type_name or '').strip()
    changed = True
    while changed:
        changed = False
        for wrapper in CH_WRAPPERS:
            head = wrapper + '('
            if text.startswith(head) and text.endswith(')'):
                text = text[len(head):-1].strip()
                if wrapper == 'SimpleAggregateFunction' and ',' in text:
                    text = text.split(',', 1)[1].strip()
                changed = True
    return text


def ch_kind(type_name: str) -> str:
    text = unwrap_ch(type_name)
    if text in CH_KINDS:
        return CH_KINDS[text]
    if text.startswith('Decimal'):
        return DECIMAL
    if text.startswith('DateTime'):
        return DATETIMETZ if "'" in text else DATETIME
    return TEXT


def ch_decimal_spec(type_name: str) -> tuple[int, int]:
    match = CH_DECIMAL.match(unwrap_ch(type_name))
    if not match:
        return 0, 0
    width, first, second = match.groups()
    if second is None:
        return CH_DECIMAL_WIDTH.get(width or '', 38), int(first)
    return int(first), int(second)


def strip_nullable(type_name: str) -> tuple[str, bool]:
    text = (type_name or '').strip()
    head = 'Nullable('
    if text.startswith(head) and text.endswith(')'):
        return text[len(head):-1].strip(), True
    return text, 'Nullable(' in text


def safe_decimal(precision: int, scale: int) -> tuple[int, int]:
    if not precision:
        return CH_SAFE_DEFAULT_DECIMAL
    scale = max(0, min(scale, CH_SAFE_SCALE_LIMIT))
    precision = max(min(precision, CH_SAFE_DIGITS - scale), scale)
    return precision, scale


def ch_safe_column(column: Column) -> Column:
    if column.kind != DECIMAL:
        return column
    precision, scale = safe_decimal(int(column.precision or 0), int(column.scale or 0))
    return replace(column, precision=precision, scale=scale)


def restricted_core(type_name: str) -> str:
    core = unwrap_ch(strip_nullable(type_name)[0])
    for name in CH_RESTRICTED:
        if core.startswith(name):
            return name
    return ''


def ch_type_is_safe(type_name: str) -> bool:
    core = restricted_core(type_name)
    if not core:
        return True
    if core != 'Decimal':
        return False
    precision, scale = ch_decimal_spec(type_name)
    return bool(precision) and precision + scale <= CH_SAFE_DIGITS


def ch_safe_type(type_name: str) -> str:
    inner, nullable = strip_nullable(type_name)
    core = unwrap_ch(inner)
    restricted = restricted_core(type_name)
    if restricted == 'Decimal':
        precision, scale = safe_decimal(*ch_decimal_spec(type_name))
        body = f'Decimal({precision}, {scale})'
    elif restricted == 'Date32':
        body = 'Date'
    elif restricted == 'DateTime64':
        zone = CH_TIMEZONE.search(core)
        body = f"DateTime('{zone.group(1)}')" if zone else 'DateTime'
    else:
        body = core
    return f'Nullable({body})' if nullable else body


def ch_type_is_castable(type_name: str) -> bool:
    core = unwrap_ch(strip_nullable(type_name)[0])
    return any(
        core == name or core.startswith(name + '(')
        for name in CH_CASTABLE
    )


def decimal_spec(column: Column) -> tuple[int, int]:
    precision = max(0, int(column.precision or 0))
    scale = max(0, int(column.scale or 0))
    if not precision:
        return 0, 0
    precision = min(precision, CH_PRECISION_LIMIT)
    return precision, min(scale, precision)


def fit_temporal(
    row: tuple,
    moments: Sequence[int],
    days: Sequence[int],
    dropped: list,
    limits: Limits = CH_WIDE_LIMITS,
) -> tuple:
    values = list(row)
    for index in moments:
        value = values[index]
        if isinstance(value, date) and not isinstance(value, datetime):
            value = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        if not isinstance(value, datetime):
            continue
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if limits.min_time <= value <= limits.max_time:
            values[index] = value
        else:
            values[index] = None
            dropped[0] += 1
    for index in days:
        value = values[index]
        if isinstance(value, datetime) or not isinstance(value, date):
            continue
        if not limits.min_date <= value <= limits.max_date:
            values[index] = None
            dropped[0] += 1
    return tuple(values)


def pg_ddl(column: Column) -> str:
    if column.kind == DECIMAL:
        precision, scale = decimal_spec(column)
        if precision:
            return f'numeric({precision}, {scale})'
    return PG_DDL.get(column.kind, PG_DDL[TEXT])


def ch_ddl(column: Column, safe: bool = True) -> str:
    if column.kind == DECIMAL:
        if safe:
            precision, scale = safe_decimal(int(column.precision or 0), int(column.scale or 0))
        else:
            precision, scale = decimal_spec(column)
        if precision:
            return f'Nullable(Decimal({precision}, {scale}))'
    body = CH_SAFE_DDL.get(column.kind) if safe else None
    return 'Nullable({})'.format(body or CH_DDL.get(column.kind, CH_DDL[TEXT]))
