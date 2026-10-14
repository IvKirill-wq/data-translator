# Кроме приложения необходим запуск модели из виртуального окружения.
# Пример запуска с оптимальными аргументами на скорость перевода:
# ARGOS_BEAM_SIZE=1 ARGOS_COMPUTE_TYPE=int8 ARGOS_BATCH_SIZE=1024 libretranslate --host 127.0.0.1 --port 5000 --load-only ru,en
# + flask app
# flask run -p 5001

from app import create_app

app = create_app()