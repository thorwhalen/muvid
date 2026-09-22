# muvid.mcp.identity

Caller identity for the muvid MCP tools — resolved from the OAuth token, fail-closed.

A hosted connector is multi-user: every tool must place work under the *verified*
caller, never a shared/ambient identity. muvid’s tools resolve the caller via
[`current_email()`](#muvid.mcp.identity.current_email), which reads the fastmcp request’s access token — so they work
under ANY host middleware, including the shared `enlace_metering.MeteringMiddleware`
the unified reelee connector installs (thorwhalen/reelee#232), which keys its own
context var. There is **no fallback** beyond the token: an unauthenticated call is
failed closed. Mirrors `braidio.mcp.metering.token_email`/`current_email`.

### Functions

| [`current_email`](#muvid.mcp.identity.current_email)()   | The caller's identity for the in-flight tool call (raises if unauthenticated).   |
|--------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`token_email`](#muvid.mcp.identity.token_email)()     | The verified caller's email from the OAuth token (`email` claim, else `sub`).    |
| [`use_email`](#muvid.mcp.identity.use_email)(email)  | Bind the caller identity for the duration of the block (local/stdio/testing).    |

### muvid.mcp.identity.current_email()

The caller’s identity for the in-flight tool call (raises if unauthenticated).

Resolves from the explicit [`use_email()`](#muvid.mcp.identity.use_email) override when present (local/stdio/
tests), else the verified OAuth token — so tools work under any host middleware, and
an unauthenticated call is failed closed.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### muvid.mcp.identity.token_email()

The verified caller’s email from the OAuth token (`email` claim, else `sub`).

Lowercased, or `None` when there is no request/token context — deliberately no
fallback, so a caller is failed closed rather than handed a shared identity.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### muvid.mcp.identity.use_email(email)

Bind the caller identity for the duration of the block (local/stdio/testing).
