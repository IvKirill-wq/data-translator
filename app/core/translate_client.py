from typing import Sequence

import requests


class TranslationError(RuntimeError):
    pass


class LibreTranslateClient:
    def __init__(self, url: str, batch: int = 24, timeout: int = 120, api_key: str = ''):
        address = (url or '').strip().rstrip('/')
        if not address:
            raise TranslationError('не задан адрес движка перевода')
        self._url = address if address.endswith('/translate') else address + '/translate'
        self._batch = max(1, int(batch))
        self._timeout = int(timeout)
        self._api_key = api_key or ''

    def translate(self, texts: Sequence[str]) -> list[str]:
        result: list[str] = []
        for start in range(0, len(texts), self._batch):
            chunk = list(texts[start:start + self._batch])
            result.extend(self._send(chunk))
        return result

    def _send(self, chunk: Sequence[str]) -> list[str]:
        payload = {'q': list(chunk), 'source': 'ru', 'target': 'en', 'format': 'text'}
        if self._api_key:
            payload['api_key'] = self._api_key
        try:
            response = requests.post(self._url, json=payload, timeout=self._timeout)
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
