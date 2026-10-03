import os


class Config:
    SECRET_KEY = os.environ['SECRET_KEY']

    LIBRE_TRANSLATE_URL = os.environ['LIBRE_TRANSLATE_URL']
    TRANSLATION_BATCH = int(os.environ.get('TRANSLATION_BATCH', '24'))

    MAX_RU_DISTINCT_QTY = int(os.environ.get('MAX_RU_DISTINCT_QTY', '50000')) 