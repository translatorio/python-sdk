![Translatorio Python SDK](https://raw.githubusercontent.com/translatorio/python-sdk/main/art/banner.png)

# Translatorio Python SDK

<p align="center">
    <a href="https://github.com/translatorio/python-sdk/actions/workflows/tests.yml"><img src="https://github.com/translatorio/python-sdk/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
    <a href="https://pypi.org/project/translatorio-sdk/"><img src="https://img.shields.io/pypi/v/translatorio-sdk.svg" alt="PyPI version"></a>
    <a href="https://pypi.org/project/translatorio-sdk/"><img src="https://img.shields.io/pypi/dm/translatorio-sdk.svg" alt="PyPI downloads"></a>
    <a href="https://github.com/translatorio/python-sdk/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/translatorio-sdk.svg" alt="license"></a>
    <a href="https://pypi.org/project/translatorio-sdk/"><img src="https://img.shields.io/pypi/pyversions/translatorio-sdk.svg" alt="Python versions"></a>
</p>

The official Python client for [Translatorio](https://translatorio.com), the translation API.

```bash
pip install translatorio-sdk
```

```python
import os

from translatorio import Client

tr = Client(os.environ["TRANSLATORIO_KEY"])

result = tr.translate("Add to cart", "es", glossary="shop", reference="release-42")

if result.intact:
    publish(result.translation)   # "Añadir a la cesta"
else:
    hold_for_review(result.id)    # a placeholder, tag or masked value did not come back
```

`intact` is the one field worth checking before publishing: a translation that lost
`{name}` or a `<b>` tag is not ready, however good the rest of it reads.

## Translating text

One text or many, into one language or many. `results` keeps the order you sent.

```python
result = tr.translate(
    ["Add to cart", "<b>Free</b> returns within {days} days"],
    ["es", "de", "ja"],
    source="en",              # left out, or "auto", it is detected
    format="html",            # text, html or markdown: decides what is protected
    formality="informal",     # default, formal or informal
    context="buttons of a shop",
    glossary="shop",          # the slug of one of your glossaries
    terms={"es": {"cart": "cesta"}},   # terms for this call only, per language or for all
    anonymize=True,           # the default: personal data is masked before the model sees it
    reference="release-42",   # your own id, handed back and kept in the history
)

result.translation          # only for one text into one language, otherwise None
result.text("de", index=1)  # one text in one language
result.translations         # [{"es": "...", "de": "...", "ja": "..."}, {...}]
result.results              # [{"index", "translations", "intact"}], as sent
result.intact               # False when anything protected did not come back
result.source               # "en", given or detected
result.detected             # True when it was detected

result.charged              # credits this call cost
result.remaining            # credits left
result.segments             # text by language pairs
result.cached               # answered from your translation memory
result.corrected            # answered with one of your corrections
result.anonymized           # pieces of personal data masked
result.model_read           # whether a model read it
result.model_why            # "memory" or "test_key" when none did
result.effort               # "low", "medium" or "high": the level it was served at
result.tokens               # {"input": ..., "output": ...}
result.raw                  # the whole answer
```

An option the endpoint does not take is a `TypeError` here, before anything is sent: the
API refuses unknown fields with `unknown_field`, and an option left out is never sent.

### Choosing how hard the model works

`effort` (`default`, `low`, `medium` or `high`) is accepted on `translate()`, `translate_file()`,
`translate_image()` and `batch()`, and sent only when you pass it. Leaving it out is the same as `default`: the level Translatorio recommends at the time (medium today), which moves as the models do. Name a level to keep it fixed.

```python
tr.translate(text, "es", effort="high")
```

The answer says which level served it, in `result.effort`.

## Translating a file

Send a whole translation file and get one file back per language, in the same shape:
keys, comments and structure untouched. JSON, PO, XLIFF, CSV, Android XML, iOS `.strings`,
Markdown, HTML and plain text.

```python
from pathlib import Path

result = tr.translate_file(Path("lang/en.json"), ["es", "de"])   # read, and named after itself

for file in result.files:
    Path("lang", file["name"]).write_text(file["content"])        # es.json, de.json

result.content("es")    # one language's file
result.strings          # how many strings the file held
result.format           # "json"
result.corrected        # strings answered with one of your corrections
result.effort           # the level it was served at

# Bytes, with a name that says the format (or `format=`):
tr.translate_file(Path("Localizable.strings").read_bytes(), "fr", filename="Localizable.strings")

# Or the content as text:
tr.translate_file(catalogue_text, "fr", filename="messages.po")
```

A path or bytes are uploaded as `multipart/form-data`, exactly as they are, so a UTF-16
`.strings` file arrives byte for byte (it comes back translated as UTF-8). Text goes up as JSON. A plain `str` is always the
content, never a path: pass a `pathlib.Path` to read a file.

## Translating an image

Every block of text in the picture, its translation into each language, and its box as
fractions of the image, so you can lay the translation over the original.

```python
result = tr.translate_image("https://cdn.example.com/sign.jpg", ["en", "es"])
result = tr.translate_image(Path("sign.jpg"), "en")            # a file
result = tr.translate_image(open("sign.jpg", "rb").read(), "en")  # bytes
result = tr.translate_image(base64_or_data_uri, "en")           # already encoded

for block in result.blocks:
    block["text"], block["box"], block["translations"]["en"]
```

An http or https address is fetched by the model provider; anything else is the picture
itself and goes out as base64. PNG, JPEG, WebP or GIF.

## Detecting a language

```python
detection = tr.detect("Obrigado pela ajuda")

detection.code         # "pt"
detection.language     # {"code", "name", "native", "rtl"}, or None
detection.confidence   # 0.97
detection.by           # "script", "model" or "test_key"
```

Rarely needed: leaving `source` out of `translate()` detects it in the same call.

## Batches

Many texts translated in the background. The call answers at once; poll the batch or let
the `batch.completed` webhook tell you.

```python
batch = tr.batch(texts, ["es", "fr", "de"], glossary="shop", reference="catalogue-2026-10")
batch.id                  # "bat_..."
batch.credits_estimated   # set aside now, so a burst of batches cannot spend the same credits

status = tr.batch_status(batch.id)
status.status, status.done, status.failed

translated = dict(status.translations)    # index -> {language: text}
while status.next:
    status = tr.batch_status(batch.id, cursor=status.next)
    translated.update(status.translations)

status.failures           # index -> {"code", "message"}: a bad item fails in its place
status.page               # this page of results as a Page (items, next)

tr.batches(limit=10)      # the recent ones, summarised
```

## Glossaries

```python
tr.create_glossary("Shop", {"cart": "cesta", "checkout": "caja"}, source="en", target="es")
tr.glossaries()                       # every glossary, with entry counts
glossary = tr.glossary("shop")        # one, with its entries
glossary.terms                        # {"cart": "cesta", "checkout": "caja"}

# Only what you pass is sent. `entries` REPLACES the list; None unties a language.
tr.update_glossary("shop", entries=glossary.entries + [{"term": "basket", "translation": "cesta"}])
tr.update_glossary("shop", target=None)

tr.delete_glossary("shop")
```

A glossary with no target applies to every language, which is what brand names want.

## Corrections

Your own translation of one exact text into one language. From then on that text is
answered with yours, and the model is never asked again.

```python
correction = tr.correct("Add to cart", "es", "Añadir a la cesta")

page = tr.corrections(target="es")
while page.next:
    page = tr.corrections(target="es", cursor=page.next)

tr.delete_correction(correction.id)
```

## The account

```python
tr.languages()        # every language: code, name, native name, right to left
tr.usage()            # plan, credits, the last 30 days and prices. Works at zero credits.
tr.ping()             # is the key good, is the service up

page = tr.translations(kind="text", reference="release-42")   # the history, never the text
tr.translation_record(page.items[0].id)

tr.keys()             # name, mode, prefix, last use. Never a secret.
tr.revoke_key(12)     # including the one you are calling with. There is no create.
```

### Paged lists

`corrections()` and `translations()` answer a `Page`: `page.items` (the rows, which you can
also iterate over) and `page.next`. Pass `next` back as `cursor=` for the following page;
`None` means the last one. `next` is opaque, so keep it as it came. A batch's results page
the same way: `batch_status(id, cursor=...)` and its `next`. The other lists
(`languages()`, `keys()`, `glossaries()`, `batches()`) are plain lists.

All of these are free.

## Errors

Every refusal is a typed exception carrying the API's `code`, its `message`, the `status`
and the decoded `payload`.

```python
from translatorio import (
    AuthenticationError, InvalidRequest, NotFound, QuotaExhausted, RateLimited, ServerError,
)

try:
    result = tr.translate(text, "es")
except QuotaExhausted as e:
    # 402. Stop calling: no amount of retrying produces credits.
    alert(f"needed {e.required}, {e.remaining} left, renews {e.renews_at}")
except InvalidRequest as e:
    # 422: e.code is invalid_request, unknown_field, unsupported_language, too_long...
    log(e.code, e.fields)    # {field: [messages]}, empty when no one field is at fault
except RateLimited as e:
    # 429, already retried. e.retry_after is what the service asked for, in seconds.
    ...
except ServerError as e:
    # 5xx, or the service could not be reached (status 0). Already retried.
    ...
```

`AuthenticationError` is a 401, `NotFound` a 404, and anything else the API refuses is a
`TranslatorioError`, the base of them all.

## Retries and idempotency

**Retries the right failures and never the wrong one.** A 429, a 5xx and an unreachable
service are asked again with a growing wait, twice by default (`retries=`). A 402 is never
retried, nor is any other 4xx: those answers do not change by asking again.

**Waits as long as the service asked, and no longer.** The wait a 429 or a call still in
flight carries (`retry_after` in the body, or the `Retry-After` header) beats guessing, and it is bounded by `max_wait` (30 seconds by default) all the same.

**Makes those retries safe.** Every call that changes something carries an
`Idempotency-Key`, generated once per call and reused by its retries (a file upload is
resent byte for byte), so a request that
timed out and is asked again is translated once and billed once. Pass your own with
`idempotency_key=` on the translation calls to make a retry from your side the same call.

**Never reads a non-answer as an answer.** Only a 2xx whose body is a JSON object is one.
A redirect (never followed, so your key goes nowhere it was not sent), an empty body or a
proxy's HTML page is a retryable `ServerError`.

`timeout=` (120 seconds by default) applies to connecting and to every read, so a stalled
body fails like a stalled connection.

## Webhooks

Your endpoint URL is public. Verify before you act:

```python
from translatorio import webhooks

event = webhooks.event(
    request.get_data(),                       # the RAW body
    request.headers[webhooks.HEADER],         # X-Translatorio-Signature
    os.environ["TRANSLATORIO_WEBHOOK_SECRET"],
)

if event is None:
    return "", 400

if event["event"] == webhooks.BATCH_COMPLETED:
    fetch_results(event["data"]["id"])
```

Events: `batch.completed`, `credits.low` and `quota.exhausted`. A delivery older than five
minutes is refused (`tolerance=` to change it), so a captured one cannot be replayed.

## Testing with a transport of your own

Python 3.9+. **No dependencies**: the standard library does the HTTP. Pass anything with a
`send()` as `transport=` to put requests through your own stack, or to test without a
network:

```python
class Stub:
    def send(self, method, url, headers, body):
        return 200, '{"translation": "Hola", "intact": true}'

tr = Client("tr_test_...", transport=Stub())
```

`send()` returns `(status, body)` or `(status, body, headers)`, and reports a request that
got no answer by raising `ServerError`, which is what gets retried.

The client's own suite runs over a stub transport: `python3 -m unittest discover -s tests`.

## Author

Created by [Edu Lazaro](https://edulazaro.com) for [Translatorio](https://translatorio.com),
the translation API this client speaks to.

## License

The Translatorio Python SDK is open-sourced software licensed under the [MIT license](https://github.com/translatorio/python-sdk/blob/main/LICENSE).
