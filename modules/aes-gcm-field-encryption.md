---
name: aes-gcm-field-encryption
title: AES-256-GCM for fields, JSON, blobs, and a JSON store
summary: Encrypt private text with AES-256-GCM, a unique IV, and an authentication tag, and store graph-memory values as a JSON object so a jsonb reader can load them. Use this when row contents must be unreadable in the database and still decryptable by the application.
stack: [django, python]
tags: [encryption, privacy, storage]
requires: []
related_skills: [ai-chat-with-memory, account-privacy-ops]
status: stable
updated: 2026-10-21
---

# AES-256-GCM for fields, JSON, blobs, and a JSON store

One process-wide key encrypts private columns and the bytes a checkpoint library persists. The library is `cryptography.hazmat.primitives.ciphers.aead.AESGCM`. Each call uses a fresh 12-byte IV from `os.urandom`. The tag is 16 bytes. Associated data is `None`.

Vectors used for similarity search stay plaintext. Ciphertext cannot be an operand of `<=>`.

## Key

| Name | Notes |
| --- | --- |
| `FIELD_ENCRYPTION_KEY` | 64 hex characters, 32 bytes. The only secret. |
| `DJANGO_DEBUG` | When false, the process refuses to boot if the key is missing. |

Load the key once. `bytes.fromhex` must yield exactly 32 bytes. A missing key, a non-hex string, or the wrong length disables encryption: log the configuration error, keep the service object, and let reads return stored plaintext. Do not raise from the loader. The boot check is what stops a production process from running with encryption off.

```python
def load_key(hex_key: str | None) -> bytes | None:
    if not hex_key:
        return None
    try:
        key = bytes.fromhex(hex_key)
    except ValueError:
        return None
    if len(key) != 32:
        return None
    return key
```

Generate a key outside the repo and put it in the secret store:

```bash
python -c "import os; print(os.urandom(32).hex())"
```

Never log the key, the plaintext, or a decrypted payload. Log the operation, the row id, and the exception type.

## Four stored shapes

GCM in this library returns `ciphertext || tag`. Split the last 16 bytes when storing a field. Concatenate them again before `decrypt`.

### Field string

`base64(iv):base64(ciphertext):base64(tag)` with exactly two colons. IV decodes to 12 bytes. Tag decodes to 16 bytes. Ciphertext is valid base64. Use ASCII for the base64 text.

```python
IV_SIZE = 12
TAG_SIZE = 16

def encrypt(plaintext: str) -> EncryptedData | None:
    if aesgcm is None or not plaintext:
        return None
    iv = os.urandom(IV_SIZE)
    packed = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    body, tag = packed[:-TAG_SIZE], packed[-TAG_SIZE:]
    return EncryptedData(
        ciphertext=b64(body),
        iv=b64(iv),
        tag=b64(tag),
    )

def encrypt_field(plaintext: str | None) -> str | None:
    if aesgcm is None or not plaintext:
        return plaintext
    data = encrypt(plaintext)
    return f"{data.iv}:{data.ciphertext}:{data.tag}"
```

`encrypt` raises if AES-GCM itself throws. `encrypt_field` of `""` or `None` returns that value unchanged, including when encryption is enabled. Empty strings are not ciphertext.

`is_encrypted_field` is true only when the value is a string, contains exactly two colons, every part is non-empty, and the IV, tag, and ciphertext decode under strict base64 to the lengths above. Text that merely contains two colons, including HTML, is not encrypted.

`decrypt_field`:

- Disabled, empty, or not `is_encrypted_field`: return the input unchanged. Legacy plaintext stays readable. Do not log a warning for that path.
- Otherwise split, decrypt, and return the UTF-8 string.
- If GCM rejects the tag, log a warning with the row id and return the stored string. A single corrupt field must not fail the whole read. `decrypt` on an `EncryptedData` object still raises `ValueError` when the tag or IV is wrong. Callers that already parsed the three parts use that stricter method.

### JSON

```python
def encrypt_json(value):
    if value is None or is_encrypted_field(value):
        return value
    if aesgcm is None:
        return value
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return encrypt_field(payload)

def decrypt_json(value, default=None):
    if value is None:
        return default
    decrypted = decrypt_field(value) if isinstance(value, str) else value
    if isinstance(decrypted, str):
        try:
            return json.loads(decrypted)
        except json.JSONDecodeError:
            return decrypted if default is None else default
    return decrypted
```

`sort_keys=True` keeps the ciphertext stable for a given object. Already-encrypted strings are left alone so a backfill can run twice.

### Binary blob

Checkpoint bytes use a prefix so old plaintext blobs still load.

```python
MAGIC = b"AESGCM1\x00"  # 8 bytes

def encrypt_bytes(data: bytes) -> bytes:
    if aesgcm is None or data is None:
        return data
    iv = os.urandom(IV_SIZE)
    return MAGIC + iv + aesgcm.encrypt(iv, data, None)

def decrypt_bytes(data: bytes) -> bytes:
    if data is None or not isinstance(data, (bytes, bytearray)):
        return data
    data = bytes(data)
    if not data.startswith(MAGIC):
        return data
    if aesgcm is None:
        raise ValueError("Encrypted blob requires FIELD_ENCRYPTION_KEY")
    body = data[len(MAGIC):]
    iv, packed = body[:IV_SIZE], body[IV_SIZE:]
    return aesgcm.decrypt(iv, packed, None)
```

A serializer that wraps another serializer encrypts only the payload and keeps the type tag:

```python
def dumps_typed(obj):
    type_, payload = inner.dumps_typed(obj)
    return type_, encrypt_bytes(payload)

def loads_typed(data):
    type_, payload = data
    return inner.loads_typed((type_, decrypt_bytes(payload)))
```

Plaintext blobs written before encryption have no magic prefix and pass through. A magic blob with encryption disabled raises. That is the one read that must fail closed: the bytes are not recoverable without the key.

### JSON store envelope

Some stores persist `value` as `jsonb` and only skip a second `json.loads` when the decoded row is already an object. A bare string, including `iv:ciphertext:tag`, is parsed as JSON. `json.loads` then throws during search, before application code can decrypt. One bad row fails a batched read for every key fetched with it.

Store a dict. Decrypt only after the read succeeds.

```python
def encrypt_store_value(value) -> dict:
    return {"enc": encrypt_json(value)}

def decrypt_store_value(value, default=None):
    if value is None:
        return default
    if isinstance(value, dict) and "enc" in value:
        return decrypt_json(value["enc"], default=default)
    return decrypt_json(value, default=default)
```

`decrypt_store_value` still accepts a legacy encrypted string, or a plaintext dict or list, so rows written before the envelope keep loading. New writes always use the envelope. Repair old rows. Leave no mixed shapes in the namespaces you search.

```sql
UPDATE store
SET value = jsonb_build_object('enc', value #>> '{}'),
    updated_at = CURRENT_TIMESTAMP
WHERE jsonb_typeof(value) = 'string'
  AND (prefix LIKE '%.L1_memory' OR prefix LIKE '%.L2_memory');
```

The `store` table and `prefix` column are the graph library's. Limit the `UPDATE` to the prefixes this app encrypted. Dry-run is the same `WHERE` with `SELECT COUNT(*)`. Run the repair with the persistence pool, then close it. After the repair, `decrypt_store_value` sees `{"enc": "<ciphertext>"}`.

## Columns

Encrypt on write, decrypt in the repository when building the domain object. API serializers see plaintext and still drop secrets and vectors (`pgvector-similarity-search`).

| Rows | Field strings | JSON strings |
| --- | --- | --- |
| Items | title, body | topics, keywords, people, analysis |
| Comments | body | |
| Goals | title, description | analysis |
| Summaries | | analysis |
| Threads | latest user message | |
| Messages | content | metadata |
| Chat turns | question, answer | raw payload |

Chat-turn vectors, item vectors, and catalog vectors are not encrypted.

A management command walks each table with `iterator(chunk_size=500)`, default batch 500. It requires the key (`CommandError` when encryption is disabled). `--dry-run` reports counts and does not save. For each text column, skip `None` and skip values that already pass `is_encrypted_field`. Assign `encrypt_field` or `encrypt_json` only when the new value differs, then `save(update_fields=...)`. The command is safe to repeat.

## Failure prevention

- **P08.** Write store values as `{"enc": "<ciphertext>"}`. A bare encrypted string fails `asearch` / `aget` inside the jsonb reader, and one such row fails the whole batch. Decrypt after a successful read. Repair string-typed rows with the `UPDATE` above. Do not leave mixed shapes in a namespace you search.
- Production boot requires `FIELD_ENCRYPTION_KEY` when `DJANGO_DEBUG` is false. A dev process may boot with encryption disabled and will store plaintext. Turn that off before any shared environment.
- A new IV for every `encrypt` and every `encrypt_bytes`. Reusing an IV under the same key breaks GCM.
- Treat two colons as plaintext unless IV and tag lengths match. Otherwise a normal sentence is sent to AES-GCM and the read logs a false tamper warning.
- `decrypt_field` returns the original string when the tag check fails, so one bad column does not 500 the request. Alert on that warning. `decrypt` and `decrypt_bytes` raise, because the caller already asked for authenticated bytes.
- Backfill is idempotent: `is_encrypted_field` and `encrypt_json`'s already-encrypted check skip finished values.
- Keep similarity vectors outside the encrypted columns.
- Log row ids and exception types. Leave plaintext and the key out of logs.

## Tests

- Round-trip ASCII, a string with non-Latin characters and an emoji, a 100_000 character string, and a string that contains colons, quotes, and newlines.
- `encrypt("")` and `encrypt(None)` return `None`. `encrypt_field("")` returns `""`.
- A field string has exactly two colons. Decrypt returns the original.
- Tampered ciphertext and a replaced IV make `decrypt` raise `ValueError`.
- `decrypt_field` of ordinary text, of `""`, of `None`, and of `<p>a</p>:<p>b</p>:<p>c</p>` returns the input and does not log a decrypt warning.
- `encrypt_json` of a dict and of a list round-trips. `sort_keys` is on. A legacy list passed to `decrypt_json` is returned as that list.
- `encrypt_store_value` returns a dict with key `enc`. `decrypt_store_value` restores the object. A legacy encrypted string still decrypts.
- `encrypt_bytes` starts with `AESGCM1\x00`. A blob without the prefix is returned unchanged. A prefixed blob with the key missing raises.
- A wrong-length hex key leaves `is_enabled` false, and `encrypt_field` returns the plaintext.
- Backfill dry-run saves nothing. A second real run updates zero rows.
- The repair query counts string `value` rows and, after the update, those rows are objects with `enc`.

## Checklist

- [ ] `FIELD_ENCRYPTION_KEY` is 64 hex characters in the secret store. Production boot fails closed without it.
- [ ] Fields use `iv:ciphertext:tag`. JSON is `json.dumps` with `sort_keys` then that format. Blobs use the `AESGCM1` prefix. Store writes use `{"enc": ...}` (P08).
- [ ] IV is 12 random bytes per operation. Tag is 16 bytes. AAD is empty.
- [ ] `is_encrypted_field` checks lengths, so two-colon plaintext is left alone.
- [ ] Repositories decrypt on read. Serializers still strip vectors and secrets.
- [ ] Vector columns stay plaintext.
- [ ] Backfill supports `--dry-run` and skips values that are already encrypted.
- [ ] Legacy string rows in the JSON store are wrapped, then searched again.
