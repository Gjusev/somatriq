/**
 * Typed same-origin API client for the M2 account/pairing surface (ADR 0014
 * single-origin topology, ADR 0015 local accounts). Same boundary rule as the
 * heart-rate card: the wire shape is never trusted — every response passes a
 * small parser that mirrors packages/contracts/somatriq_contracts/pairing.py
 * (snake_case on the wire, camelCase in the app) and throws on anything
 * unexpected so queries enter their error state.
 */

export const TOKEN_STORAGE_KEY = "sqt_web_token";

/** Error raised for any non-2xx response; carries the contract error code. */
export class ApiError extends Error {
  readonly status: number;
  readonly errorCode: string | null;

  constructor(status: number, errorCode: string | null, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.errorCode = errorCode;
  }
}

// ---------------------------------------------------------------------------
// Contract mirror (field names follow the TS side of the 1:1 mapping)
// ---------------------------------------------------------------------------

export type AuthStatus = {
  hasAccount: boolean;
};

export type TokenResponse = {
  accessToken: string;
  tokenType: string;
  expiresAt: string;
};

export type PairingSessionResponse = {
  sessionId: string;
  pairingCode: string;
  expiresAt: string;
};

export type PairedDeviceInfo = {
  deviceId: string;
  name: string;
  model: string | null;
};

export type PairingStatus = {
  sessionId: string;
  pairingCodeHint: string;
  status: "pending" | "consumed" | "expired";
  createdAt: string;
  expiresAt: string;
  device: PairedDeviceInfo | null;
};

export type DeviceInfo = {
  deviceId: string;
  name: string;
  model: string | null;
  createdAt: string;
  lastUsedAt: string | null;
  tokenExpiresAt: string | null;
  revokedAt: string | null;
};

/** Unambiguous alphabet, no 0/O/1/I — mirrors PAIRING_CODE_ALPHABET. */
const PAIRING_CODE_PATTERN = /^[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{8}$/;

// ---------------------------------------------------------------------------
// Token storage (localStorage; every access is SSR-prerender safe)
// ---------------------------------------------------------------------------

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } catch {
    // Private mode / storage disabled: the session just won't persist.
  }
}

export function clearStoredToken(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // Ignore — nothing to clear.
  }
}

// ---------------------------------------------------------------------------
// Fetch core
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isIsoDate(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

async function toApiError(res: Response): Promise<ApiError> {
  let errorCode: string | null = null;
  let message = `API responded with ${res.status}`;
  try {
    const body: unknown = await res.json();
    if (isRecord(body)) {
      // Two shapes live on the wire: ingest returns the contract error flat
      // ({error_code, message}); the auth/pairing/devices routers raise
      // HTTPException(detail={...}), which FastAPI nests under "detail".
      const detail: unknown = isRecord(body.detail) ? body.detail : body;
      if (isRecord(detail)) {
        if (typeof detail.error_code === "string") errorCode = detail.error_code;
        if (typeof detail.message === "string") message = detail.message;
      } else if (typeof body.detail === "string") {
        message = body.detail;
      }
    }
  } catch {
    // Non-JSON body — keep the status-line message.
  }
  return new ApiError(res.status, errorCode, message);
}

/**
 * Authenticated fetch: attaches the account JWT from localStorage. On 401 the
 * session is dead — drop the token and return to /login (full navigation so
 * every cached query and component state resets with it).
 */
export async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getStoredToken();
  if (token !== null) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const res = await fetch(path, { credentials: "same-origin", ...init, headers });
  if (res.status === 401) {
    clearStoredToken();
    window.location.assign("/login/");
  }
  return res;
}

async function request(path: string, init: RequestInit): Promise<Response> {
  const res = await fetch(path, { credentials: "same-origin", ...init });
  if (!res.ok) {
    throw await toApiError(res);
  }
  return res;
}

async function authRequest(path: string, init: RequestInit): Promise<Response> {
  const res = await authFetch(path, init);
  if (!res.ok && res.status !== 401) {
    // 401 already triggered the sign-in redirect; anything else is an error.
    throw await toApiError(res);
  }
  return res;
}

async function getJson<T>(path: string, authed: boolean, parse: (raw: unknown) => T): Promise<T> {
  const res = authed ? await authRequest(path, { method: "GET" }) : await request(path, { method: "GET" });
  return parse(await res.json());
}

async function postJson<T>(
  path: string,
  body: unknown,
  authed: boolean,
  parse: (raw: unknown) => T,
): Promise<T> {
  const init: RequestInit = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const res = authed ? await authRequest(path, init) : await request(path, init);
  return parse(await res.json());
}

/** POST expecting 204 No Content (device revoke). */
async function postVoid(path: string): Promise<void> {
  const res = await authRequest(path, { method: "POST" });
  // 204 is the contract; tolerate any 2xx with a body we ignore.
  await res.text().catch(() => undefined);
}

// ---------------------------------------------------------------------------
// Parsers (one per endpoint, mirroring the pydantic contracts)
// ---------------------------------------------------------------------------

function unexpected(): never {
  throw new Error("unexpected response shape");
}

function parseString(value: unknown): string {
  return typeof value === "string" && value.length > 0 ? value : unexpected();
}

function parseNullableString(value: unknown): string | null {
  return value === null ? null : parseString(value);
}

function parseAuthStatus(raw: unknown): AuthStatus {
  if (!isRecord(raw) || typeof raw.has_account !== "boolean") unexpected();
  return { hasAccount: raw.has_account };
}

function parseTokenResponse(raw: unknown): TokenResponse {
  if (!isRecord(raw)) unexpected();
  const accessToken = parseString(raw.access_token);
  if (!isIsoDate(raw.expires_at)) unexpected();
  return {
    accessToken,
    tokenType: typeof raw.token_type === "string" ? raw.token_type : "bearer",
    expiresAt: raw.expires_at,
  };
}

function parsePairingSession(raw: unknown): PairingSessionResponse {
  if (!isRecord(raw)) unexpected();
  const code = parseString(raw.pairing_code);
  if (!PAIRING_CODE_PATTERN.test(code) || !isIsoDate(raw.expires_at)) unexpected();
  return {
    sessionId: parseString(raw.session_id),
    pairingCode: code,
    expiresAt: raw.expires_at,
  };
}

function parsePairedDevice(raw: unknown): PairedDeviceInfo {
  if (!isRecord(raw)) unexpected();
  return {
    deviceId: parseString(raw.device_id),
    name: parseString(raw.name),
    model: parseNullableString(raw.model),
  };
}

function parsePairingStatus(raw: unknown): PairingStatus {
  if (!isRecord(raw)) unexpected();
  const hint = parseString(raw.pairing_code_hint);
  const status = raw.status;
  if (
    hint.length !== 4 ||
    (status !== "pending" && status !== "consumed" && status !== "expired") ||
    !isIsoDate(raw.created_at) ||
    !isIsoDate(raw.expires_at)
  ) {
    unexpected();
  }
  return {
    sessionId: parseString(raw.session_id),
    pairingCodeHint: hint,
    status,
    createdAt: raw.created_at,
    expiresAt: raw.expires_at,
    device: raw.device === null || raw.device === undefined ? null : parsePairedDevice(raw.device),
  };
}

function parseDeviceInfo(raw: unknown): DeviceInfo {
  if (!isRecord(raw)) unexpected();
  if (!isIsoDate(raw.created_at)) unexpected();
  const lastUsedAt = raw.last_used_at ?? null;
  const tokenExpiresAt = raw.token_expires_at ?? null;
  const revokedAt = raw.revoked_at ?? null;
  if (
    (lastUsedAt !== null && !isIsoDate(lastUsedAt)) ||
    (tokenExpiresAt !== null && !isIsoDate(tokenExpiresAt)) ||
    (revokedAt !== null && !isIsoDate(revokedAt))
  ) {
    unexpected();
  }
  return {
    deviceId: parseString(raw.device_id),
    name: parseString(raw.name),
    model: parseNullableString(raw.model),
    createdAt: raw.created_at,
    lastUsedAt,
    tokenExpiresAt,
    revokedAt,
  };
}

function parseDeviceList(raw: unknown): DeviceInfo[] {
  if (!Array.isArray(raw)) unexpected();
  return raw.map(parseDeviceInfo);
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

/** GET /api/v1/auth/status — public; drives the first-run wizard branch. */
export function fetchAuthStatus(): Promise<AuthStatus> {
  return getJson("/api/v1/auth/status", false, parseAuthStatus);
}

/** POST /api/v1/auth/register — only valid while zero accounts exist. */
export function registerAccount(username: string, password: string): Promise<TokenResponse> {
  return postJson("/api/v1/auth/register", { username, password }, false, parseTokenResponse);
}

/** POST /api/v1/auth/login. */
export function loginAccount(username: string, password: string): Promise<TokenResponse> {
  return postJson("/api/v1/auth/login", { username, password }, false, parseTokenResponse);
}

/** POST /api/v1/pairing/sessions — the full code is returned exactly once. */
export function createPairingSession(): Promise<PairingSessionResponse> {
  return postJson("/api/v1/pairing/sessions", {}, true, parsePairingSession);
}

/** GET /api/v1/pairing/sessions/{id} — wizard polling; code visible as hint only. */
export function fetchPairingStatus(sessionId: string): Promise<PairingStatus> {
  return getJson(`/api/v1/pairing/sessions/${encodeURIComponent(sessionId)}`, true, parsePairingStatus);
}

/** GET /api/v1/devices. */
export function fetchDevices(): Promise<DeviceInfo[]> {
  return getJson("/api/v1/devices", true, parseDeviceList);
}

/** POST /api/v1/devices/{id}/revoke — 204 on success. */
export function revokeDevice(deviceId: string): Promise<void> {
  return postVoid(`/api/v1/devices/${encodeURIComponent(deviceId)}/revoke`);
}
