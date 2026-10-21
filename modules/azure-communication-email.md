---
name: azure-communication-email
title: Azure Communication Services transactional email
summary: Queue welcome and support-confirmation mail through Azure Communication Services and return before delivery finishes. Use this when a sign-in or a ticket create must succeed even if the mailbox is down.
stack: [python, azure]
tags: [email, transactional]
requires: []
related_skills: []
status: stable
updated: 2026-10-21
---

# Azure Communication Services transactional email

Two messages exist: a registration welcome, and a confirmation to the person who opened a support ticket. Both are HTML plus a real plaintext body. The sync SDK runs in a worker thread. `begin_send` starts the request. This service does not wait for delivery.

The class docstring also names a payment confirmation. No method sends one.

## Configuration

| Name | Role |
| --- | --- |
| `AZURE_COMMUNICATION_CONNECTION_STRING` | SDK connection string for the Email resource. |
| `AZURE_EMAIL_SENDER_ADDRESS` | MailFrom address on that resource. |
| `SUPPORT_INBOX_EMAIL` | `replyTo` on both messages. Also printed in the body. |
| `FRONTEND_URL` | Absolute origin for every link, no trailing slash. |

`is_configured` in the reference is true when the connection string and the sender are both non-empty. An empty `FRONTEND_URL` or `SUPPORT_INBOX_EMAIL` still sends: the reference substitutes a fixed production origin and a fixed inbox. Require all four. If any is empty, skip the send and return false. Do not substitute a host.

```python
def is_configured(self) -> bool:
    return bool(
        self.connection_string
        and self.sender_address
        and self.support_inbox
        and self.frontend_url
    )
```

Read the values once, in the constructor. A process-wide singleton (`get_email_service`) keeps that instance. Changing env vars needs a process restart.

> NOTE: unverified against a live Azure resource. Domain verification for the sender address is the Azure portal flow. It is not in this repo. The sender string must be an address the Email resource already allows.

## Templates

Files live next to the sender, named `welcome.html` and `support.html`. Read them with `lru_cache(maxsize=4)`. A template edit is invisible until the process restarts.

Tokens are `{{lowercase_words}}`. The pattern is `\{\{([a-z_]+)\}\}`. Substitute once. Escape every value with `html.escape(value, quote=True)` before insertion, so a subject like `Can't login` becomes `Can&#x27;t login` and the inserted text is not scanned for further tokens. An unknown token stays as the original `{{token}}`.

Welcome tokens: `site_url`, `action_url`, `home_url`, `support_email`. Support tokens: `site_url`, `support_email`, `ticket_id`, `subject`, `category`.

```python
_TOKEN = re.compile(r"\{\{([a-z_]+)\}\}")

def fill(name: str, values: dict[str, str]) -> str:
    safe = {key: html.escape(value, quote=True) for key, value in values.items()}

    def repl(match: re.Match[str]) -> str:
        return safe.get(match.group(1), match.group(0))

    return _TOKEN.sub(repl, template(name))
```

Build the plaintext yourself. The HTML fallback only replaces `<br>` variants and collapses whitespace. It does not remove tags. Both builders in the reference pass an explicit plaintext string, and the tests assert the HTML contains no leftover `{{`.

Keep styling inline. A `<style>` block is a progressive enhancement for clients that keep it. Links are absolute and come from `FRONTEND_URL`. Pass the action URL and the home URL into the filler. The reference concatenates fixed app paths onto `FRONTEND_URL`. A new product supplies its own paths.

The support plaintext and the welcome plaintext in the reference include product copy and a postal address. Write those strings for the product. HTML values go through `fill`. The plaintext body is not HTML. Strip CR and LF from `subject` before it is passed as the message subject. The SDK takes `subject` as a field, which is the real protection. The reference does not strip newlines.

Display ticket id: `str(ticket_id).replace("-", "")[:8].upper()`. That is a label in the subject (`Support Request Received - Ticket #ABCD1234`). The stored primary key stays the full UUID. Do not look a ticket up by the short id.

## Send

```python
def _send_sync(self, *, to, subject, html_body, plain_text, reply_to) -> None:
    from azure.communication.email import EmailClient

    message = {
        "senderAddress": self.sender_address,
        "recipients": {"to": [{"address": to}]},
        "content": {
            "subject": subject,
            "html": html_body,
            "plainText": plain_text,
        },
    }
    if reply_to:
        message["replyTo"] = [{"address": reply_to}]
    client = EmailClient.from_connection_string(self.connection_string)
    # Starts the HTTP request. Do not call poller.result().
    client.begin_send(message)


async def send_html(self, *, to, subject, html_body, plain_text, reply_to=None) -> bool:
    if not to or not self.is_configured():
        return False
    try:
        await asyncio.to_thread(
            self._send_sync,
            to=to,
            subject=subject,
            html_body=html_body,
            plain_text=plain_text,
            reply_to=reply_to,
        )
    except Exception:
        logger.exception("Azure email send failed", subject=subject)
        return False
    logger.info("Azure email queued", subject=subject)
    return True
```

`asyncio.to_thread` keeps the sync client off the event loop. An empty recipient returns false and does not call the SDK. A missing configuration returns false. Any exception from `begin_send` returns false. Success means the request was accepted, not that the mailbox delivered it.

The service comment says `begin_send` returns after HTTP 202 and any other status raises. The unit test stubs a 400 as `RuntimeError` and asserts `poller.result` is not called. Microsoft’s quickstart then blocks on `poller.result()` until the operation reaches a terminal state (`SUCCEEDED` or a failure). That wait does not belong on the sign-in or ticket request. Log the subject. Do not log the HTML, the connection string, or the recipient if the log drain is shared. The reference logs the recipient address.

`replyTo` is the support inbox on both messages. The support confirmation goes to the requester only. Nobody on the team is copied. Open tickets are read in the app.

## Callers

Welcome route, `POST /api/welcome-email/`, body `{ "email": "<address>" }`. Reject when the address is empty, contains no `@`, or is longer than 320 characters (400). There is no fuller RFC check.

When `request.user_id` is set, insert the activity row (`loops-crm`) before the send. A failure to insert is logged and does not change the response. Then send. Always return 200 `{ "sent": true | false }`, including when Azure throws. Sign-in must not depend on delivery.

The reference sends whatever address is in the body. It does not compare that address to the auth user. Send only to the email on the verified user, or require the body address to match it. The signup caller does pass the session email. The route should still enforce it.

Support ticket create stores the row first, then sends in a `try` that logs and swallows exceptions. The response stays 201 with the ticket id when mail returns false. The confirmation uses the stored ticket UUID, the requester address, the subject, and the category. A missing requester address skips the send.

Both routes sit behind the JWT middleware. A missing subject never reaches the handler on a normal request. The welcome handler still checks `if user_id` before writing activity.

## Setup

1. Create an Email Communication Services resource and verify the sender domain. Set `AZURE_EMAIL_SENDER_ADDRESS` to a MailFrom address on that resource.
2. Set the connection string, `SUPPORT_INBOX_EMAIL`, and `FRONTEND_URL`. Treat any empty value as "not configured".
3. Add `welcome.html` and `support.html` beside the sender. Use the tokens listed above. Ship a plaintext body next to each HTML body.
4. Mount the welcome route and call it from signup provisioning. Mount ticket create so it stores the row, then sends, and still returns 201 when send returns false.
5. Confirm `poller.result` is not called and a downed mailbox does not fail sign-in or ticket create.

## Failure prevention

- Treat the connection string, sender, inbox, and `FRONTEND_URL` as one configuration. Skip the send when any is empty. Do not fall back to a baked-in origin or inbox.
- Call `begin_send` and do not call `poller.result()`. Delivery is asynchronous.
- Run the sync client in a thread. Return false on exception so the caller can keep the primary action.
- Escape template values in one pass. Always pass a real plaintext body.
- Build every link from `FRONTEND_URL`.
- Welcome returns 200 with `sent`. Ticket create returns 201 even when mail fails.
- Bind the welcome recipient to the authenticated user.
- Record signup activity before the send, with `get_or_create`, so a retry does not overwrite a later item-save timestamp.
- Leave the connection string and the HTML body out of logs.

## Tests

Configuration: an empty connection string or sender makes `is_configured` false. Extend that to an empty inbox and an empty site URL.

`send_html("")` does not call the SDK and returns false. A stubbed `begin_send` that returns a poller returns true, and `poller.result` is not called. `begin_send` raising returns false.

Support: one send, `to` is the requester, the subject contains the 8-character label, the HTML contains the escaped subject and no `{{`, and `reply_to` is the inbox.

Welcome: `to` is the recipient, `reply_to` is the inbox, and the HTML uses the configured origin for the action link and the home link. A different origin string does not appear.

Route: a missing welcome email is 400. A successful send is 200 `{ "sent": true }`.

## Checklist

- [ ] All four env vars are set. No default origin or inbox in code.
- [ ] The sender address is verified on the Email resource.
- [ ] Templates substitute `{{token}}` once, with HTML-escaped values, and ship a separate plaintext body.
- [ ] `begin_send` is not polled. Failures return false.
- [ ] Welcome is HTTP 200 with `sent`. Ticket create is HTTP 201 even when mail fails.
- [ ] Welcome mail goes to the authenticated user's address.
- [ ] Signup activity is inserted with `get_or_create` before the send.
- [ ] The connection string and message bodies are not logged.
