from flask import Blueprint, make_response, render_template, request

from .forms import TranslationForm

bp = Blueprint('main', __name__)

SOURCE_FIELDS = ('src_host', 'src_port', 'src_user', 'src_database', 'src_query')
TARGET_FIELDS = ('dst_host', 'dst_port', 'dst_user', 'dst_database', 'dst_table')

DB_LAYER_PENDING = 'Слой доступа к БД ещё не написан: app/db пуст.'
CORE_LAYER_PENDING = 'Слой перевода ещё не написан: app/core пуст.'


def fetch_result_columns(form):
    raise NotImplementedError(DB_LAYER_PENDING)


def build_translations(form, column):
    raise NotImplementedError(CORE_LAYER_PENDING)


def write_to_target(form, pairs):
    raise NotImplementedError(DB_LAYER_PENDING)


def blank_fields(form, names):
    return [
        getattr(form, name).label.text
        for name in names
        if not getattr(form, name).data
    ]


def set_columns(form, columns):
    form.src_columns.data = '\n'.join(columns)
    form.src_column.choices = [(name, name) for name in columns]


@bp.route('/', methods=['GET', 'POST'])
@bp.route('/index', methods=['GET', 'POST'])
def index():
    form = TranslationForm()
    stored = [line for line in (form.src_columns.data or '').splitlines() if line]
    form.src_column.choices = [(name, name) for name in stored]
    pairs = list(zip(request.form.getlist('source'), request.form.getlist('translation')))
    notice = None

    if form.validate_on_submit():
        if form.run_query.data:
            gaps = blank_fields(form, SOURCE_FIELDS)
            if gaps:
                notice = 'Заполните поля источника: ' + ', '.join(gaps)
            else:
                try:
                    set_columns(form, fetch_result_columns(form))
                except (NotImplementedError, RuntimeError) as error:
                    notice = str(error)
                    set_columns(form, [])

        elif form.run_translate.data:
            gaps = blank_fields(form, SOURCE_FIELDS) + blank_fields(form, TARGET_FIELDS)
            if not form.src_column.data:
                gaps.append(form.src_column.label.text)
            if gaps:
                notice = 'Заполните поля: ' + ', '.join(gaps)
            else:
                try:
                    pairs = build_translations(form, form.src_column.data)
                except (NotImplementedError, RuntimeError) as error:
                    notice = str(error)

        elif form.apply_result.data:
            gaps = blank_fields(form, TARGET_FIELDS)
            if gaps:
                notice = 'Заполните поля приёмника: ' + ', '.join(gaps)
            elif not pairs:
                notice = 'Список переводов пуст — применять нечего.'
            else:
                try:
                    written = write_to_target(form, pairs)
                    notice = f'Записано значений: {written}.'
                except (NotImplementedError, RuntimeError) as error:
                    notice = str(error)

    response = make_response(render_template('index.html', form=form, pairs=pairs, notice=notice))
    response.headers['Cache-Control'] = 'no-store'
    return response
