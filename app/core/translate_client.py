import requests


class LibreTranslateClient:
    def __init__(self, url, batch_size=24, timeout=60):
        self._url = url.rstrip('/') + '/translate'
        self._batch = batch_size
        self._timeout = timeout

    def translate(self, texts, ctx=None):
        out = []
        for i in range(0, len(texts), self._batch):
            chunk = texts[i:i + self._batch]
            resp = requests.post(
                self._url,
                json={'q': chunk, 'source': 'ru', 'target': 'en', 'format': 'text'},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            result = resp.json()['translatedText']
            if len(result) != len(chunk):
                raise RuntimeError(f'движок вернул {len(result)} вместо {len(chunk)}')
            out.extend(result)
        return out