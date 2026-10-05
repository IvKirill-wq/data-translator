from flask_wtf import FlaskForm
from wtforms import HiddenField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import Optional
from wtforms.widgets import PasswordInput

ENGINES = [('postgres', 'PostgreSQL'), ('clickhouse', 'ClickHouse')]


class TranslationForm(FlaskForm):
    src_engine = SelectField('СУБД', choices=ENGINES)
    src_host = StringField('Хост', validators=[Optional()])
    src_port = StringField('Порт', validators=[Optional()])
    src_user = StringField('Пользователь', validators=[Optional()])
    src_password = PasswordField('Пароль', validators=[Optional()], widget=PasswordInput(hide_value=False))
    src_database = StringField('База данных', validators=[Optional()])
    src_query = TextAreaField('Запрос', validators=[Optional()])
    src_columns = HiddenField()
    src_column = SelectField('Колонка', choices=[], validate_choice=False)
    run_query = SubmitField('Выполнить')

    dst_engine = SelectField('СУБД', choices=ENGINES)
    dst_host = StringField('Хост', validators=[Optional()])
    dst_port = StringField('Порт', validators=[Optional()])
    dst_user = StringField('Пользователь', validators=[Optional()])
    dst_password = PasswordField('Пароль', validators=[Optional()], widget=PasswordInput(hide_value=False))
    dst_database = StringField('База данных', validators=[Optional()])
    dst_table = StringField('Целевая таблица', validators=[Optional()])
    run_translate = SubmitField('Выполнить перевод')

    apply_result = SubmitField('Применить')
