# Security

Monitor only targets in the config file or the catalog. The minimum interval is 30 seconds. Redirects are not followed. Hostnames are not resolved while a target is validated, so a public name can still point at a private address later. The public status page never fetches a user-supplied URL. Its response sets `Content-Security-Policy: default-src 'self'`, `Referrer-Policy: no-referrer`, and `X-Content-Type-Options: nosniff`.

Admin HTML uses a session cookie and a CSRF token. The password is hashed with PBKDF2-HMAC-SHA256 before it is stored on the app object. `POST /api/monitors` requires `X-API-Key`. Login failures use one message. Logs name the monitor. They do not include the URL, the heartbeat token, the password, or the API key. The Werkzeug logger is set to ERROR so the ping path is not access-logged.

The web command and the agent collector accept a loopback or private IPv4 literal. `0.0.0.0` and public addresses are rejected. Inside the container, the entrypoint selects the container's own address, which is a private address, and Compose publishes that port only on the host's `127.0.0.1`. The compose environment values are local placeholders. Replace them before anyone else can open the port.

Private and localhost targets are allowed by default because the monitor runs beside them. That is an SSRF concern if a status page ever fetched those URLs. This one does not.

The evidence pack mentions CERT-In and DPDP only to say the text is not legal advice. Retention in that pack is a filter, not a scheduled purge.
