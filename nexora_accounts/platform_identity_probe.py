"""Read-only authenticated user ID proof from a selected 5gbet Chromium page.

The site's shipped commonChunk calls POST /api/member/v2/user/info with
`data: {action:[0]}`; its returned data.basic is used for trusted-device
settings. A real backend user identity schema is not yet established. Therefore
only an explicit *pair* of uid + username in the same successful response can
prove the observed ID; all other response formats remain UNVERIFIED.

No provider response bodies, access credentials, IDs or usernames are logged
or transmitted to the dashboard. No financial request or account mutation here.
"""
from __future__ import annotations

from urllib.parse import urlsplit


OFFICIAL_HOSTS = frozenset({"5gbet.com", "www.5gbet.com"})
VERIFICATION_SOURCE = "5gbet:member-v2-user-info:authenticated"

# The original frontend calls this exact endpoint for user profile data.
# The optional uid/username pair is intentionally NOT assumed to exist:
# a response without those two fields will never pass verification.
_NATIVE_AUTH_IDENTITY_PROBE = r"""
async ({expectedUid, expectedUsername}) => {
  if (location.protocol !== "https:" ||
      !["www.5gbet.com", "5gbet.com"].includes(location.hostname.toLowerCase())) {
    return {state:"wrong-origin"};
  }
  const paths = [
    ...performance.getEntriesByType("resource").map(x => x.name),
    ...Array.from(document.querySelectorAll("script[src]"), x => x.src)
  ];
  const chunkUrl = [...new Set(paths)].find(x => {
    try {
      const u = new URL(x, location.href);
      return u.origin === location.origin &&
             /\/commonChunk\.[^/?]+\.js$/.test(u.pathname);
    } catch { return false; }
  });
  if (!chunkUrl) return {state:"missing-sdk"};
  try {
    const chunk = await import(chunkUrl);
    if (typeof chunk?.n?.request !== "function") return {state:"missing-sdk"};
    const query = chunk.n.request(
      {url:"/api/member/v2/user/info", method:"POST", data:{action:[0]}},
      {merge:false, maxRetryCount:0, cache:{useCache:false}}
    );
    const outcome = await Promise.race([
      query.then(response => ({response})).catch(() => ({error:true})),
      new Promise(resolve => setTimeout(() => resolve({timeout:true}), 7000))
    ]);
    if (outcome.timeout) return {state:"timeout"};
    if (outcome.error) return {state:"provider-unavailable"};
    const body = outcome.response?.data;
    if (Number(body?.code) !== 1) return {state:"provider-rejected"};
    const info = body?.data;
    if (!info || typeof info !== "object") return {state:"schema-unconfirmed"};
    // Only accept two fields bound together in the CURRENT authenticated
    // account's provider response. Never infer UID from localStorage,
    // outgoing x-object-id, success:true or HTTP 200 alone.
    const candidates = [info?.basic, info].filter(x => x && typeof x === "object");
    for (const identity of candidates) {
      if (!Object.prototype.hasOwnProperty.call(identity,"uid") ||
          !Object.prototype.hasOwnProperty.call(identity,"username")) continue;
      const uid = identity.uid;
      const username = identity.username;
      if ((typeof uid !== "string" && typeof uid !== "number") ||
          typeof username !== "string") return {state:"invalid-identity-shape"};
      const normalizedUid = String(uid);
      if (!/^[1-9][0-9]{0,15}$/.test(normalizedUid)) {
        return {state:"invalid-identity-shape"};
      }
      if (normalizedUid !== expectedUid) return {state:"uid-mismatch"};
      if (username.trim().toLowerCase() !== expectedUsername.toLowerCase()) {
        return {state:"username-mismatch"};
      }
      return {state:"matched"};
    }
    // Report no field names, PII or original JSON to the outer application.
    return {state:"schema-unconfirmed"};
  } catch {
    return {state:"provider-unavailable"};
  }
}
"""


def check_auth_identity_page(page, *, observed_uid: str, username: str) -> dict:
    """Return only a bounded diagnostic; 'matched' is an authenticated claim."""
    if (not isinstance(observed_uid, str) or not observed_uid.isdecimal()
        or not 0 < int(observed_uid) <= 2**53 - 1):
        return {"state": "invalid-observed-id"}
    if not isinstance(username, str) or not 1 <= len(username) <= 128:
        return {"state": "invalid-username"}
    try:
        current = urlsplit(str(page.url or ""))
        if current.scheme != "https" or current.hostname not in OFFICIAL_HOSTS:
            return {"state": "wrong-origin"}
        result = page.evaluate(_NATIVE_AUTH_IDENTITY_PROBE, {
            "expectedUid": observed_uid,
            "expectedUsername": username,
        })
    except Exception:
        return {"state": "browser-error"}
    accepted = {
        "matched", "wrong-origin", "missing-sdk", "timeout",
        "provider-unavailable", "provider-rejected", "schema-unconfirmed",
        "invalid-identity-shape", "uid-mismatch", "username-mismatch",
    }
    state = result.get("state") if isinstance(result, dict) else None
    return {"state": state if state in accepted else "invalid-result"}
