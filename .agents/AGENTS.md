# Project Rules & Security Constraints (Backend)

Follow these constraints strictly for all backend edits in `Vtt_backend`:

1. **No Debug Mode in Production**: Never set `debug=True` inside `main.py` or other execution contexts. Rely strictly on the `QUART_DEBUG` environment variable.
2. **Database-backed JWT Blacklist**: Do not use in-memory sets for JTI blacklist verification. Always use the `BlacklistedToken` Tortoise ORM model, and await `is_blacklisted` / `blacklist_token` asynchronous calls.
3. **Authentication Rate Limiting**: Keep `/login` and `/register` endpoints decorated with the `@rate_limit` decorator to prevent credentials brute-forcing and registration spam.
4. **Safe Exceptions**: Do not return raw exception messages (`str(e)`) to the client. Log them server-side using `logger.error(..., exc_info=True)` and return generic user-friendly errors.
5. **No Database Default Fallbacks**: Raise a `RuntimeError` if `DATABASE_URL` is not set. Never hardcode credentials fallback to `postgres:admin`.
6. **Docker Containers as Non-Root**: Ensure the `Dockerfile` runs applications using `USER appuser` and directory permissions are appropriately assigned to `appuser:appgroup`. Do not bake secrets into docker builds (maintain the `.dockerignore` file blocking `.env` and `venv`).
7. **Gated Schema Creation**: Keep `generate_schemas()` gated behind the `GENERATE_SCHEMAS` environment check to avoid db startup races.
8. **Secure Uploads**: Prevent memory exhaustion DoS attacks by verifying `Content-Length` headers first, and reading streams with limits: `file.read(MAX_UPLOAD_SIZE + 1)`.
9. **URL Validation**: Verify all user-supplied URLs (avatars, map images, etc.) using `is_safe_url` to restrict schemes to `http`/`https`/relative local paths, blocking `javascript:` and phishing targets.
10. **Strict Character Ownership**: Validate client-supplied owner IDs (`proprietario_id`) during character creation. Players can only own their own characters, while masters can assign them only to verified campaign participants.
11. **JWT Revocation on Profile Change**: Issue a new JWT cookie upon updating authentication fields (such as username) to avoid stale claims.
12. **Dataclass Request Validation**: Split route methods (PUT/DELETE) where validation is needed and decorate PUT/POST with `@validate_request` schemas to avoid raw parsing of JSON bodies.
