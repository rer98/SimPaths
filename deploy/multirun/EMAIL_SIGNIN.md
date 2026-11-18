<!-- (C) Copyright 2026, by Ross Richardson
     Ordinary MultiRun email-link behaviour and deployment boundary.
     @author ross richardson -->

# MultiRun email sign-in

Ordinary users receive a **Sign in to MultiRun** link and the existing six-digit
code in the same email. The link signs in automatically in the browser that
requested the message. A different browser explains how to request its own email
or use the code in the original browser. Approval is still required.

Both credentials expire with the original ten-minute challenge. Successful use
of either consumes their shared nonce in PostgreSQL, including across concurrent
requests and service restarts. Existing send limits, code-attempt limits, account
revocation, session lifetime, ownership checks and CSRF checks remain in force.
Administrator sign-in retains its separate password and code.

The public frontend creates a random, short-lived HttpOnly, Secure, SameSite=Strict
host-only browser cookie. Production uses the `__Host-` prefix. Resending in the
same browser reuses the binding, so an earlier pending email still works. A
successful sign-in or explicit sign-out clears that cookie. Loopback-only local
tests use a non-Secure cookie under a separate name.

The private executor constructs the URL from its configured canonical origin,
never from request headers. The signed email credential uses a separate purpose
from the code challenge, includes no six-digit code, and is bound to the browser
secret. It is sent only through the mail sender, not in the request API response.

The credential is in a URL fragment, which is not sent in page requests. The
landing page removes it from browser history before making a same-origin POST.
The page and API are uncached, have no third-party resources, disallow embedding,
and send no referrer. The script waits for a visible, non-prerendered page before
redeeming. GET and HEAD do not authenticate or consume anything. Scanners without
the original browser cookie cannot spend the request's code attempts or consume
the nonce, even if they execute JavaScript. A scanner with access to the original
browser's cookies is outside this boundary.

The ordinary code remains usable without this cookie, using the original code
challenge as before. Browser-cookie loss therefore does not make the code itself
unusable. A connection failure on the landing page offers Retry; the link is
kept only in that page's memory, not browser storage.

## Deployment

Both native VM entry points explicitly enable the link sender through
`create_registry(..., send_link_mail=verification_mail)`. The canonical URL is
`options.origin`; SMTP configuration is unchanged. Code-only local tools keep
their existing two-argument sender. New and already-issued code challenges remain
compatible; existing signed-in sessions and approval records are preserved.

Install the matching HTTP frontend, private RPC facade/dispatcher, Access and
email-verification modules, landing assets, runtime assembly and mail sender
together. No database schema migration or new secret file is required. Coordinate
the frontend/executor reload with the normal deployment process so an old private
dispatcher is not left serving the new frontend.

Qualification includes captured plain/HTML mail, original code and administrator
regressions, PostgreSQL single-use races, and desktop/mobile browser workflows
through the private RPC transport. It sends no real mail and runs no simulations.
