import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.environ['SECRET_KEY']
    WTF_CSRF_TIME_LIMIT = None

    LIBRE_TRANSLATE_URL = os.environ['LIBRE_TRANSLATE_URL']
    LIBRE_TRANSLATE_KEY = os.environ.get('LIBRE_TRANSLATE_KEY', '')
    TRANSLATION_BATCH = int(os.environ.get('TRANSLATION_BATCH', '24'))
    TRANSLATION_TIMEOUT = int(os.environ.get('TRANSLATION_TIMEOUT', '120'))

    MAX_RU_DISTINCT_QTY = int(os.environ.get('MAX_RU_DISTINCT_QTY', '5000'))
    INSERT_BATCH = int(os.environ.get('INSERT_BATCH', '10000'))

    GLOSSARY_PATH = os.environ.get('GLOSSARY_PATH') or str(BASE_DIR / 'glossary.tsv')
