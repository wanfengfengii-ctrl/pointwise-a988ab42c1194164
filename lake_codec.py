import hashlib
import json


def require_text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip() or '\x00' in value:
        raise ValueError(f'{field} must be nonblank text without NUL')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ValueError(f'{field} must be valid Unicode text') from exc
    return value


def require_fields(document: object, fields: tuple[str, ...]) -> dict:
    if type(document) is not dict or set(document) != set(fields):
        raise ValueError(f'expected object fields {fields!r}')
    return document


def require_list(value: object, field: str) -> list:
    if type(value) is not list:
        raise ValueError(f'{field} must be an array')
    return value


def canonical_json(document: object) -> str:
    return json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON field {key!r}')
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f'invalid JSON constant {value!r}')


def load_json(text: str) -> object:
    if type(text) is not str:
        raise ValueError('JSON document must be text')
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def content_digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def encode_integer(value: int) -> str:
    if type(value) is not int:
        raise ValueError('observation value must be an integer')
    return hex(value)


def decode_integer(wire: object) -> int:
    if type(wire) is not str or not wire.startswith(('0x', '-0x')):
        raise ValueError('observation value must be a canonical hexadecimal integer')
    value = int(wire, 16)
    if hex(value) != wire:
        raise ValueError('observation value must be a canonical hexadecimal integer')
    return value
