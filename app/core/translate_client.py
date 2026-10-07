import logging
import time
from typing import Sequence

import requests

LOG = logging.getLogger(__name__)

DEFAULT_BATCH = 24
DEFAULT_CHARS = 1200
DEFAULT_TIMEOUT = 120
PROBE_TIMEOUT = 15
PROBE_PAUSE = 1


class TranslationError(RuntimeError):
    pass


class TranslationTimeout(TranslationError):
    pass


class LibreTranslateClient:
    def __init__(
        self,
        url: str,
        batch: int = DEFAULT_BATCH,
        timeout: int = DEFAULT_TIMEOUT,
        api_key: str = '',
        chars: int = DEFAULT_CHARS,
    ):
        address = (url or '').strip().rstrip('/')
        if not address:
            raise TranslationError('не задан адрес движка перевода')
        if address.endswith('/translate'):
            address = address[:-len('/translate')]
        self._url = address + '/translate'
        self._probe = address + '/languages'
        self._batch = max(1, int(batch))
        self._chars = max(1, int(chars))
        self._timeout = max(1, int(timeout))
        self._api_key = api_key or ''
        self._session = requests.Session()

    def translate(self, texts: Sequence[str]) -> list:
        answers: list = []
        for chunk in self._chunks(list(texts)):
            answers.extend(self._translate_chunk(chunk))
        return answers

    def _chunks(self, texts: Sequence[str]):
        chunk: list = []
        size = 0
        for text in texts:
            length = len(text) + 1
            if chunk and (len(chunk) >= self._batch or size + length > self._chars):
                yield chunk
                chunk, size = [], 0
            chunk.append(text)
            size += length
        if chunk:
            yield chunk

    def _translate_chunk(self, chunk: Sequence[str]) -> list:
        started = time.monotonic()
        try:
            answers = self._send(chunk)
        except TranslationTimeout:
            if len(chunk) == 1:
                LOG.warning(
                    'движок не уложился в %s с на одном значении длиной %s символов, '
                    'оставляю без перевода: %.80s',
                    self._timeout, len(chunk[0]), chunk[0],
                )
                return [None]
            middle = len(chunk) // 2
            LOG.warning(
                'батч из %s значений (%s символов) не уложился в %s с, делю на %s и %s',
                len(chunk), sum(len(text) for text in chunk), self._timeout,
                middle, len(chunk) - middle,
            )
            self._wait_free()
            return self._translate_chunk(chunk[:middle]) + self._translate_chunk(chunk[middle:])
        LOG.info(
            'переведено значений: %s (%s символов) за %.1f с',
            len(chunk), sum(len(text) for text in chunk), time.monotonic() - started,
        )
        return answers

    def _wait_free(self) -> None:
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                if self._session.get(self._probe, timeout=PROBE_TIMEOUT).status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(PROBE_PAUSE)
        LOG.warning('движок перевода не освободился за %s с, пробую дальше', self._timeout)

    def _send(self, chunk: Sequence[str]) -> list:
        payload = {'q': list(chunk), 'source': 'ru', 'target': 'en', 'format': 'text'}
        if self._api_key:
            payload['api_key'] = self._api_key
        try:
            response = self._session.post(self._url, json=payload, timeout=self._timeout)
        except requests.Timeout as error:
            raise TranslationTimeout(str(error)) from error
        except requests.RequestException as error:
            raise TranslationError(f'движок перевода недоступен ({self._url}): {error}') from error
        if response.status_code != 200:
            raise TranslationError(
                f'движок перевода ответил {response.status_code}: {response.text[:200]}'
            )
        try:
            body = response.json()
        except ValueError as error:
            raise TranslationError(f'движок перевода вернул не JSON: {response.text[:200]}') from error
        translated = body.get('translatedText') if isinstance(body, dict) else None
        if isinstance(translated, str):
            translated = [translated]
        if not isinstance(translated, list) or len(translated) != len(chunk):
            got = len(translated) if isinstance(translated, list) else type(translated).__name__
            raise TranslationError(f'движок перевода вернул {got} значений вместо {len(chunk)}')
        return [str(item) for item in translated]
