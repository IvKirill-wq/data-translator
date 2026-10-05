from itertools import zip_longest

from flask import Blueprint, current_app, make_response, render_template, request

from .core.glossary import load as load_glossary
from .core.pipeline import Pair, apply, collect, translate
from .core.protect import Protector
from .core.translate_client import LibreTranslateClient
from .db import ConnParams, get_adapter
from .forms import TranslationForm

bp = Blueprint('main', __name__)

SOURCE_FIELDS = ('src_host', 'src_port', 'src_user', 'src_database', 'src_query')
TARGET_FIELDS = ('dst_host', 'dst_port', 'dst_user', 'dst_database', 'dst_table')


def conn_params(form, prefix: str) -> ConnParams:
    port_text = (getattr(form, prefix + '_port').data or '').strip()
    try:
        port = int(port_text)
    except ValueError:
        raise ValueError(f'порт должен быть числом, а не {port_text!r}')
    return ConnParams(
        engine=getattr(form, prefix + '_engine').data,
        host=(getattr(form, prefix + '_host').data or '').strip(),
        port=port,
        user=(getattr(form, prefix + '_user').data or '').strip(),
        password=getattr(form, prefix + '_password').data or '',
        database=(getattr(form, prefix + '_database').data or '').strip(),
    )


def source_query(form) -> str:
    return (form.src_query.data or '').strip().rstrip(';')


def chosen_columns(form) -> list:
    if form.src_auto.data:
        return []
    return [name for name in (form.src_column.data or []) if name]


def build_translator() -> LibreTranslateClient:
    return LibreTranslateClient(
        current_app.config['LIBRE_TRANSLATE_URL'],
        batch=current_app.config['TRANSLATION_BATCH'],
        timeout=current_app.config['TRANSLATION_TIMEOUT'],
        api_key=current_app.config['LIBRE_TRANSLATE_KEY'],
    )


def fetch_result_columns(form):
    adapter = get_adapter(conn_params(form, 'src'))
    return adapter.columns(source_query(form))


def build_translations(form):
    adapter = get_adapter(conn_params(form, 'src'))
    glossary = load_glossary(current_app.config['GLOSSARY_PATH'])
    values, rows = collect(
        adapter,
        source_query(form),
        chosen_columns(form),
        current_app.config['MAX_RU_DISTINCT_QTY'],
    )
    return translate(
        values,
        glossary,
        Protector(keep=glossary.keep),
        build_translator(),
        rows=rows,
    )


def write_to_target(form, pairs):
    return apply(
        get_adapter(conn_params(form, 'src')),
        get_adapter(conn_params(form, 'dst')),
        source_query(form),
        pairs,
        (form.dst_table.data or '').strip(),
        batch=current_app.config['INSERT_BATCH'],
    )


def blank_fields(form, names):
    return [
        getattr(form, name).label.text
        for name in names
        if not (getattr(form, name).data or '').strip()
    ]


def set_columns(form, columns):
    names = [column.name for column in columns]
    form.src_columns.data = '\n'.join(f'{column.name}\t{column.raw_type}' for column in columns)
    form.src_column.choices = [
        (column.name, f'{column.name} · {column.raw_type}') for column in columns
    ]
    form.src_column.data = [name for name in (form.src_column.data or []) if name in names]


def stored_columns(form):
    choices = []
    for line in (form.src_columns.data or '').splitlines():
        parts = line.split('\t')
        name = parts[0].strip()
        if not name:
            continue
        raw_type = parts[1].strip() if len(parts) > 1 else ''
        choices.append((name, f'{name} · {raw_type}' if raw_type else name))
    return choices


def posted_pairs():
    columns = request.form.getlist('column')
    sources = request.form.getlist('source')
    translations = request.form.getlist('translation')
    pairs: dict = {}
    for column, source, translation in zip_longest(
        columns, sources, translations, fillvalue=''
    ):
        if not column or not source:
            continue
        pairs.setdefault(column, []).append(Pair(source=source, translation=translation))
    return pairs


def form_errors(form) -> str:
    problems = []
    for field, messages in form.errors.items():
        text = '; '.join(str(message) for message in messages)
        if not isinstance(field, str) or not field:
            problems.append(text)
            continue
        label = getattr(getattr(form, field, None), 'label', None)
        problems.append(f'{getattr(label, "text", field)}: {text}')
    if not problems:
        return 'Форма не принята.'
    return 'Форма не принята — ' + '. '.join(problems) + '. Обновите страницу и повторите.'


def translation_notice(result) -> str:
    stats = result.stats
    columns = ', '.join(result.pairs) or 'нет'
    return (
        f'Строк просмотрено: {stats.rows}. Колонки: {columns}. '
        f'Значений к переводу: {stats.values}. Движок: {stats.engine}, '
        f'без защиты: {stats.raw}, глоссарий: {stats.glossary}, '
        f'защищено целиком: {stats.protected}, откат на оригинал: {stats.fallback}.'
    )


@bp.route('/', methods=['GET', 'POST'])
@bp.route('/index', methods=['GET', 'POST'])
def index():
    form = TranslationForm()
    form.src_column.choices = stored_columns(form)
    pairs = posted_pairs()
    active = request.form.get('tab', '')
    notice = None
    notice_kind = 'info'

    if form.validate_on_submit():
        try:
            if form.run_query.data:
                gaps = blank_fields(form, SOURCE_FIELDS)
                if gaps:
                    raise ValueError('Заполните поля источника: ' + ', '.join(gaps))
                columns = fetch_result_columns(form)
                set_columns(form, columns)
                notice = f'Колонок в результате: {len(columns)}. Выберите, какие переводить.'

            elif form.run_translate.data:
                gaps = blank_fields(form, SOURCE_FIELDS)
                if gaps:
                    raise ValueError('Заполните поля: ' + ', '.join(gaps))
                if not form.src_auto.data and not chosen_columns(form):
                    raise ValueError(
                        'Выберите колонки для перевода или включите «Определить автоматически».'
                    )
                result = build_translations(form)
                pairs = result.pairs
                active = next(iter(pairs), '')
                notice = translation_notice(result)
                if not pairs:
                    notice += ' Переводить нечего: значений с кириллицей не нашлось.'

            elif form.apply_result.data:
                gaps = blank_fields(form, SOURCE_FIELDS) + blank_fields(form, TARGET_FIELDS)
                if gaps:
                    raise ValueError('Заполните поля: ' + ', '.join(gaps))
                if not pairs:
                    raise ValueError('Список переводов пуст — применять нечего.')
                applied = write_to_target(form, pairs)
                notice = (
                    f'Таблица {applied.table} {applied.action}, '
                    f'колонок переведено: {applied.columns}, '
                    f'записано строк: {applied.written}, '
                    f'заменено значений: {applied.replaced}.'
                )
                if not applied.replaced:
                    notice += ' Ни одно значение не заменено — проверьте выбранные колонки.'
                    notice_kind = 'warn'
        except Exception as error:
            current_app.logger.exception('действие не выполнено')
            notice = str(error) or error.__class__.__name__
            notice_kind = 'error'
            if form.run_query.data:
                set_columns(form, [])

    elif request.method == 'POST':
        notice = form_errors(form)
        notice_kind = 'error'

    if active not in pairs:
        active = next(iter(pairs), '')

    response = make_response(render_template(
        'index.html',
        form=form,
        pairs=pairs,
        active=active,
        notice=notice,
        notice_kind=notice_kind,
    ))
    response.headers['Cache-Control'] = 'no-store'
    return response
