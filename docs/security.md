# Security

Monitor only targets in the config file or the catalog. The minimum interval is 30 seconds. Redirects are not followed. Hostnames are not resolved while a target is validated, so a public name can still point at a private address later. The public status page never fetches a user-supplied URL. Its response sets `Content-Security-Policy: default-src 'self'`, `Referrer-Policy: no-referrer`, and `X-Content-Type-Options: nosniff`.

`POST /monitors/probe` is the Add monitor "Test this check now" action. It is registered only on the signed-in console, checks the CSRF token, and allows 30 probes an hour with at least two seconds between them. It runs the same check builder as a saved monitor, including the private-target rule. The public status page and `/api/status` do not call it. `POST /monitors/run` runs every enabled monitor once. It requires a login and a CSRF token. The command palette posts to it.

Admin HTML uses a session cookie and a CSRF token. `pulseguard serve` sets that cookie HttpOnly and SameSite=Lax. The password is hashed with PBKDF2-HMAC-SHA256. `serve` stores the hash in the users table. Five failed logins for the same address and username lock that pair for 60 seconds, and the failure message is always `login failed`. `POST /api/monitors` requires `X-API-Key` and still rejects a body over 16384 bytes. Logs name the monitor. They do not include the URL, the heartbeat token, the password, the SMTP password, or the API key. The Werkzeug logger is set to ERROR so the ping path is not access-logged.

The alerts form can store the SMTP password in the SQLite `settings` table. The form never renders that password back; a blank field leaves the stored value in place. `PULSEGUARD_SMTP_*` environment variables override the stored values when a message is sent, which keeps the password out of the database if you set them. The trade-off is convenience against file access: anyone who can read the database file can read a password that was saved from the form. Keep the data directory readable only by the operator. CSV uploads are capped at 1 MB and backups at 20 MB. The saved name is generated, and the file has to stay inside the data directory. Restore asks you to type `RESTORE` and writes a safety copy first.

The web command and the agent collector accept a loopback or private IPv4 literal. `0.0.0.0` and public addresses are rejected. Inside the container, the entrypoint selects the container's own address, which is a private address, and Compose publishes that port only on the host's `127.0.0.1`. The compose environment values are local placeholders. Replace them before anyone else can open the port.

Private and localhost targets are allowed by default because the monitor runs beside them. That is an SSRF concern if a status page ever fetched those URLs. This one does not.

The evidence pack mentions CERT-In and DPDP only to say the text is not legal advice. Retention in that pack is a filter, not a scheduled purge.
