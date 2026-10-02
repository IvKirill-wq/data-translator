import os

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY')

    LIBRE_TRANSLTE_URL = os.getenv('LIBRE_TRANSLTE_URL')
    TRANSLATION_BATCH = int(os.getenv('TRANSLATION_BATCH'))

    MAX_RU_DISTINCT_QTY = int(os.getenv('MAX_RU_DISTINCT_QTY'))
