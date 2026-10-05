import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.base import INT, TEXT, Column
from tests.fakes import FakeAdapter, FakeTranslator

try:
    import flask
except ImportError:
    flask = None

TOKEN = re.compile(r'name="csrf_token"[^>]*value="([^"]*)"')
COLUMN_SELECT = re.compile(r'<select [^>]*id="src_column"[^>]*>(.*?)</select>', re.S)
OPTION = re.compile(r'<option[^>]*value="([^"]*)"')
SELECTED = re.compile(r'<option[^>]*selected[^>]*value="([^"]*)"')
TAB_LABEL = re.compile(r'<label class="tab-label" for="tab-\d+">\s*([^<\s]+)')
TAB_CHECKED = re.compile(r'<input type="radio" name="tab"[^>]*value="([^"]*)" checked>')
PANE = re.compile(r'<div class="tab-pane">(.*?)</div>', re.S)
ROW = re.compile(
    r'name="column" value="([^"]*)">\s*'
    r'<input type="hidden" name="source" value="([^"]*)">.*?'
    r'name="translation" value="([^"]*)">\s*'
    r'<input type="hidden" name="machine" value="([^"]*)">',
    re.S,
)

BROWSER_FORM = {
    'src_engine': 'postgres',
    'src_host': '',
    'src_port': '',
    'src_user': '',
    'src_password': '',
    'src_database': '',
    'src_query': '',
    'src_columns': '',
    'dst_engine': 'clickhouse',
    'dst_host': '',
    'dst_port': '',
    'dst_user': '',
    'dst_password': '',
    'dst_database': '',
    'dst_table': '',
}

SOURCE_FORM = {
    'src_engine': 'postgres',
    'src_host': 'src-host',
    'src_port': '5432',
    'src_user': 'reader',
    'src_password': 'секрет',
    'src_database': 'dwh',
    'src_query': 'select * from contracts',
    'src_columns': 'id\tint4\nkind\ttext\nnote\ttext',
}

TARGET_FORM = {
    'dst_engine': 'clickhouse',
    'dst_host': 'dst-host',
    'dst_port': '8123',
    'dst_user': 'writer',
    'dst_password': 'тоже-секрет',
    'dst_database': 'bi',
    'dst_table': 'contracts_en',
}

COLUMNS = [
    Column(name='id', kind=INT, raw_type='int4'),
    Column(name='kind', kind=TEXT, raw_type='text'),
    Column(name='note', kind=TEXT, raw_type='text'),
]

ROWS = [
    (1, 'Договор', 'Отгрузка'),
    (2, 'договор ', None),
    (3, 'Акт', 'оплата'),
    (4, None, 'ATOL-15'),
]


@unittest.skipIf(flask is None, 'Flask не установлен')
class WebFlowTest(unittest.TestCase):
    def setUp(self):
        from app import create_app

        self.directory = tempfile.TemporaryDirectory()
        self.source = FakeAdapter(columns=COLUMNS, rows=ROWS)
        self.target = FakeAdapter()
        self.translator = FakeTranslator({
            'Договор': 'Contract',
            'Акт': 'Act',
            'Отгрузка': 'Shipment',
            'оплата': 'payment',
        })

        class TestConfig:
            SECRET_KEY = 'test'
            WTF_CSRF_TIME_LIMIT = None
            LIBRE_TRANSLATE_URL = 'http://127.0.0.1:5000'
            LIBRE_TRANSLATE_KEY = ''
            TRANSLATION_BATCH = 24
            TRANSLATION_TIMEOUT = 5
            MAX_RU_DISTINCT_QTY = 100
            INSERT_BATCH = 1000
            CACHE_PATH = str(Path(self.directory.name) / 'cache.sqlite3')
            GLOSSARY_PATH = str(Path(self.directory.name) / 'glossary.tsv')

        self.app = create_app(TestConfig)
        self.app.logger.disabled = True
        self.client = self.app.test_client()

        from app import routes

        self.routes = routes
        self.real_adapter = routes.get_adapter
        self.real_translator = routes.build_translator
        routes.get_adapter = self.pick_adapter
        routes.build_translator = lambda: self.translator

    def tearDown(self):
        self.routes.get_adapter = self.real_adapter
        self.routes.build_translator = self.real_translator
        self.directory.cleanup()

    def pick_adapter(self, params):
        return self.source if params.host == 'src-host' else self.target

    def token(self, page: str) -> str:
        found = TOKEN.search(page)
        self.assertIsNotNone(found, 'в форме нет csrf_token')
        return found.group(1)

    def start(self) -> str:
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def post(self, page: str, data: dict) -> str:
        body = dict(BROWSER_FORM)
        body.update(data)
        body['csrf_token'] = self.token(page)
        response = self.client.post('/', data=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response.get_data(as_text=True)

    def columns_page(self) -> str:
        page = self.post(self.start(), dict(SOURCE_FORM, run_query='Выполнить'))
        self.assertIn('Колонок в результате: 3', page)
        return page

    def translated_page(self, **extra) -> str:
        data = dict(SOURCE_FORM, run_translate='Выполнить перевод')
        data.update(extra)
        return self.post(self.columns_page(), data)

    def rows_of(self, page: str) -> list:
        return ROW.findall(page)

    def test_empty_page(self):
        page = self.start()
        self.assertIn('1. База с переводимыми значениями', page)
        self.assertIn('3. Значение и перевод', page)
        self.assertIn('Пусто.', page)
        self.assertIn('Определить автоматически', page)

    def test_column_list_is_multiple(self):
        page = self.columns_page()
        block = COLUMN_SELECT.search(page)
        self.assertIsNotNone(block)
        self.assertIn('multiple', block.group(0))
        self.assertEqual(OPTION.findall(block.group(1)), ['id', 'kind', 'note'])

    def test_missing_source_fields(self):
        page = self.post(self.start(), {'run_query': 'Выполнить'})
        self.assertIn('Заполните поля источника', page)
        self.assertEqual(self.source.reads, 0)

    def test_broken_csrf_is_reported(self):
        self.start()
        response = self.client.post('/', data=dict(BROWSER_FORM, run_query='Выполнить'))
        self.assertIn('Форма не принята', response.get_data(as_text=True))

    def test_bad_port_is_reported(self):
        page = self.post(self.start(), dict(SOURCE_FORM, src_port='пять', run_query='Выполнить'))
        self.assertIn('порт должен быть числом', page)

    def test_translation_without_a_choice_is_refused(self):
        page = self.translated_page()
        self.assertIn('Выберите колонки для перевода', page)
        self.assertEqual(self.translator.seen, [])

    def test_one_column(self):
        page = self.translated_page(src_column=['kind'])
        self.assertEqual(TAB_LABEL.findall(page), ['kind'])
        self.assertEqual(
            self.rows_of(page),
            [('kind', 'Акт', 'Act', 'Act'), ('kind', 'Договор', 'Contract', 'Contract')],
        )
        self.assertIn('Колонки: kind', page)

    def test_several_columns_get_their_own_tabs(self):
        page = self.translated_page(src_column=['kind', 'note'])
        self.assertEqual(TAB_LABEL.findall(page), ['kind', 'note'])
        self.assertEqual(TAB_CHECKED.findall(page), ['kind'])
        self.assertEqual(len(PANE.findall(page)), 2)
        self.assertEqual(
            self.rows_of(page),
            [
                ('kind', 'Акт', 'Act', 'Act'),
                ('kind', 'Договор', 'Contract', 'Contract'),
                ('note', 'оплата', 'payment', 'payment'),
                ('note', 'Отгрузка', 'Shipment', 'Shipment'),
            ],
        )
        self.assertIn('Колонки: kind, note', page)

    def test_automatic_choice_skips_columns_without_cyrillic(self):
        page = self.translated_page(src_auto='y')
        self.assertEqual(TAB_LABEL.findall(page), ['kind', 'note'])
        self.assertNotIn('ATOL-15', page)

    def test_automatic_choice_wins_over_the_selection(self):
        page = self.translated_page(src_auto='y', src_column=['id'])
        self.assertEqual(TAB_LABEL.findall(page), ['kind', 'note'])

    def test_selected_columns_survive_a_new_query(self):
        page = self.translated_page(src_column=['kind', 'note'])
        again = self.post(page, dict(SOURCE_FORM, src_column=['kind', 'note'],
                                     run_query='Выполнить'))
        block = COLUMN_SELECT.search(again)
        self.assertEqual(SELECTED.findall(block.group(1)), ['kind', 'note'])

    def test_apply_writes_the_hand_edited_translation(self):
        page = self.translated_page(src_column=['kind', 'note'])
        result = self.post(page, dict(
            SOURCE_FORM,
            **TARGET_FORM,
            src_column=['kind', 'note'],
            column=['kind', 'kind', 'note', 'note'],
            source=['Акт', 'Договор', 'оплата', 'Отгрузка'],
            machine=['Act', 'Contract', 'payment', 'Shipment'],
            translation=['Statement of work', 'Supply contract', 'payment', 'Shipment'],
            apply_result='Применить',
        ))
        self.assertIn('колонок переведено: 2', result)
        self.assertIn('записано строк: 4', result)
        self.assertIn('заменено значений: 5', result)
        table, columns, rows, clear = self.target.inserted[0]
        self.assertEqual(table, 'contracts_en')
        self.assertEqual([column.name for column in columns], ['id', 'kind', 'note'])
        self.assertEqual([column.kind for column in columns], [INT, TEXT, TEXT])
        self.assertEqual(rows, [
            (1, 'Supply contract', 'Shipment'),
            (2, 'Supply contract', None),
            (3, 'Statement of work', 'payment'),
            (4, None, 'ATOL-15'),
        ])
        self.assertFalse(clear)
        self.assertEqual(self.source.reads, 2)

    def test_only_the_edited_row_becomes_reviewed(self):
        page = self.translated_page(src_column=['kind'])
        self.post(page, dict(
            SOURCE_FORM,
            **TARGET_FORM,
            src_column=['kind'],
            column=['kind', 'kind'],
            source=['Акт', 'Договор'],
            machine=['Act', 'Contract'],
            translation=['Act', 'Supply contract'],
            apply_result='Применить',
        ))
        self.translator.seen.clear()
        again = self.translated_page(src_column=['kind'])
        self.assertEqual(self.translator.seen, [])
        self.assertEqual(
            self.rows_of(again),
            [('kind', 'Акт', 'Act', 'Act'), ('kind', 'Договор', 'Supply contract',
                                             'Supply contract')],
        )

    def test_apply_without_target_fields(self):
        page = self.translated_page(src_column=['kind'])
        result = self.post(page, dict(
            SOURCE_FORM,
            src_column=['kind'],
            column=['kind'],
            source=['Акт'],
            machine=['Act'],
            translation=['Act'],
            apply_result='Применить',
        ))
        self.assertIn('Заполните поля:', result)
        self.assertEqual(self.target.inserted, [])

    def test_apply_with_empty_list(self):
        page = self.columns_page()
        result = self.post(page, dict(
            SOURCE_FORM,
            **TARGET_FORM,
            src_column=['kind'],
            apply_result='Применить',
        ))
        self.assertIn('Список переводов пуст', result)

    def test_existing_target_table_is_replaced(self):
        self.target.existing = list(COLUMNS)
        page = self.translated_page(src_column=['kind'])
        result = self.post(page, dict(
            SOURCE_FORM,
            **TARGET_FORM,
            src_column=['kind'],
            column=['kind', 'kind'],
            source=['Акт', 'Договор'],
            machine=['Act', 'Contract'],
            translation=['Act', 'Contract'],
            apply_result='Применить',
        ))
        self.assertIn('перезаписана', result)
        self.assertIsNone(self.target.created)
        self.assertTrue(self.target.inserted[0][3])

    def test_pairs_for_an_unknown_column_are_reported(self):
        page = self.translated_page(src_column=['kind'])
        result = self.post(page, dict(
            SOURCE_FORM,
            **TARGET_FORM,
            src_column=['kind'],
            column=['нет-такой'],
            source=['Акт'],
            machine=['Act'],
            translation=['Act'],
            apply_result='Применить',
        ))
        self.assertIn('нет колонки', result)
        self.assertEqual(self.target.inserted, [])

    def test_too_many_distinct_values(self):
        self.app.config['MAX_RU_DISTINCT_QTY'] = 1
        page = self.translated_page(src_column=['kind'])
        self.assertIn('уникальных значений для перевода больше 1', page)
        self.assertIn('Пусто.', page)

    def test_values_with_markup_survive(self):
        self.source = FakeAdapter(
            columns=COLUMNS, rows=[(1, 'ООО "Ромашка" & <b>Весна</b>', None)]
        )
        page = self.translated_page(src_column=['kind'])
        self.assertNotIn('<b>Весна</b>', page)
        self.assertIn('&lt;b&gt;Весна&lt;/b&gt;', page)

    def test_database_error_is_shown(self):
        from app.db.base import DbError

        class Broken:
            def columns(self, sql_text):
                raise DbError('PostgreSQL: запрос не выполнен: syntax error at or near "selec"')

        self.source = Broken()
        page = self.post(self.start(), dict(SOURCE_FORM, run_query='Выполнить'))
        self.assertIn('syntax error', page)
        self.assertIn('notice-error', page)

    def test_unknown_engine(self):
        from app.db import get_adapter
        from app.db.base import ConnParams, DbError

        with self.assertRaises(DbError):
            get_adapter(ConnParams('oracle', 'h', 1521, 'u', 'p', 'd'))


if __name__ == '__main__':
    unittest.main()
