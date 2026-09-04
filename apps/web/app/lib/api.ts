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

// ---------------------------------------------------------------------------
// Daily heart summary contract mirror (M5, somatriq_contracts/daily.py)
// ---------------------------------------------------------------------------

export type DataQuality = "good" | "fair" | "poor" | "insufficient";

export type DailyHeartSummary = {
  /** "YYYY-MM-DD" — already the local day in the day's effective timezone. */
  date: string;
  /** The day's effective timezone (ADR 0017). */
  timezone: string;
  restingHr: number | null;
  hrMin: number | null;
  hrMean: number | null;
  hrMax: number | null;
  sampleCount: number;
  /** 0..1 (wire coverage_ratio; QUALITY_GOOD = 0.50, QUALITY_FAIR = 0.25). */
  coverageRatio: number;
  dataQuality: DataQuality;
  algorithmVersion: string | null;
};

export type DailySummaryResponse = {
  featureSetVersion: string;
  days: DailyHeartSummary[];
};

const ISO_DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function parseFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseDataQuality(value: unknown): DataQuality {
  if (
    value === "good" ||
    value === "fair" ||
    value === "poor" ||
    value === "insufficient"
  ) {
    return value;
  }
  unexpected();
}

function parseDailyHeartSummary(raw: unknown): DailyHeartSummary {
  if (!isRecord(raw)) unexpected();
  const date = parseString(raw.date);
  if (!ISO_DAY_PATTERN.test(date)) unexpected();
  const coverageRatio = parseFiniteNumber(raw.coverage_ratio);
  if (coverageRatio === null || coverageRatio < 0 || coverageRatio > 1) unexpected();
  const sampleCount = parseFiniteNumber(raw.sample_count);
  return {
    date,
    timezone: parseString(raw.timezone),
    restingHr: parseFiniteNumber(raw.resting_hr),
    hrMin: parseFiniteNumber(raw.hr_min),
    hrMean: parseFiniteNumber(raw.hr_mean),
    hrMax: parseFiniteNumber(raw.hr_max),
    sampleCount: sampleCount === null ? 0 : Math.round(sampleCount),
    coverageRatio,
    dataQuality: parseDataQuality(raw.data_quality),
    algorithmVersion: parseNullableString(raw.algorithm_version ?? null),
  };
}

function parseDailySummary(raw: unknown): DailySummaryResponse {
  if (!isRecord(raw) || !Array.isArray(raw.days)) unexpected();
  return {
    featureSetVersion: parseString(raw.feature_set_version),
    days: raw.days.map(parseDailyHeartSummary),
  };
}

// ---------------------------------------------------------------------------
// Today contract mirror (M6, somatriq_contracts/recovery.py — frozen shapes).
// Nulls are preserved exactly, list fields default to empty, and the
// contribution/input literals are validated: anything else throws so the
// Today card enters its error state instead of trusting the wire (ADR 0014).
// ---------------------------------------------------------------------------

/** Frozen algorithm ids (recovery.py); also the defaults for absent fields. */
const RECOVERY_ALGORITHM = "somatriq_recovery_v1";
const HRV_ALGORITHM = "somatriq_hrv_rmssd_v1";
const HRV_FILTER_VERSION = "simple-delta400";

export type RecoveryInput =
  | "hrv"
  | "rhr"
  | "sleep"
  | "temperature"
  | "training_load";

export type ContributionTone = "positive" | "negative" | "neutral";

export type RecoveryContribution = {
  input: RecoveryInput;
  value: number | null;
  baselineMedian: number | null;
  baselineIqr: number | null;
  robustZ: number | null;
  contribution: ContributionTone;
  note: string | null;
};

export type RecoveryResult = {
  /** "YYYY-MM-DD" — the wake-date in the day's effective timezone. */
  day: string;
  /** 0..100, or null when an input is missing (never imputed, spec §76). */
  score: number | null;
  algorithmVersion: string;
  contributions: RecoveryContribution[];
  missingInputs: string[];
  caveats: string[];
};

export type HrvSummary = {
  day: string;
  rmssdMs: number | null;
  sdnnMs: number | null;
  /** 0..1 */
  pnn50: number | null;
  meanRrMs: number | null;
  samples: number;
  validSamples: number;
  artifactCount: number;
  /** valid / samples, 0..1 */
  coverage: number;
  filterVersion: string;
  algorithmVersion: string;
  sessionCount: number;
};

export type SleepSummary = {
  day: string;
  durationMinutes: number | null;
  /** 0..1 */
  efficiency: number | null;
  restingHr: number | null;
  avgHrv: number | null;
  sourceRecordIds: string[];
};

export type TodayResponse = {
  date: string;
  timezone: string;
  recovery: RecoveryResult | null;
  hrv: HrvSummary | null;
  sleep: SleepSummary | null;
  restingHr: number | null;
  restingHrQuality: string | null;
  /** Minutes since the newest observation. */
  dataFreshnessMinutes: number | null;
  caveats: string[];
};

/** Optional finite float: null/undefined preserved as null, wrong type throws. */
function parseOptionalFinite(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const parsed = parseFiniteNumber(value);
  return parsed === null ? unexpected() : parsed;
}

/** Integer counter with a contract default (samples etc. default to 0). */
function parseCounter(value: unknown): number {
  const parsed = parseOptionalFinite(value);
  return parsed === null ? 0 : Math.round(parsed);
}

/** Required ratio 0..1 with a contract default (coverage defaults to 0.0). */
function parseRatio(value: unknown, fallback: number): number {
  const parsed = value === null || value === undefined ? fallback : parseFiniteNumber(value);
  if (parsed === null || parsed < 0 || parsed > 1) unexpected();
  return parsed;
}

/** Optional ratio 0..1 (pnn50, efficiency): null preserved, out-of-range throws. */
function parseOptionalRatio(value: unknown): number | null {
  const parsed = parseOptionalFinite(value);
  if (parsed !== null && (parsed < 0 || parsed > 1)) unexpected();
  return parsed;
}

/** Wire dates are calendar days already local to the payload timezone. */
function parseDay(value: unknown): string {
  const day = parseString(value);
  if (!ISO_DAY_PATTERN.test(day)) unexpected();
  return day;
}

/** Caveat-style lists default to empty; non-string entries are dropped. */
function parseStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((entry): entry is string => typeof entry === "string")
    : [];
}

function parseRecoveryInput(value: unknown): RecoveryInput {
  switch (value) {
    case "hrv":
    case "rhr":
    case "sleep":
    case "temperature":
    case "training_load":
      return value;
    default:
      unexpected();
  }
}

function parseContributionTone(value: unknown): ContributionTone {
  switch (value) {
    case "positive":
    case "negative":
    case "neutral":
      return value;
    default:
      unexpected();
  }
}

function parseRecoveryContribution(raw: unknown): RecoveryContribution {
  if (!isRecord(raw)) unexpected();
  return {
    input: parseRecoveryInput(raw.input),
    value: parseOptionalFinite(raw.value),
    baselineMedian: parseOptionalFinite(raw.baseline_median),
    baselineIqr: parseOptionalFinite(raw.baseline_iqr),
    robustZ: parseOptionalFinite(raw.robust_z),
    contribution: parseContributionTone(raw.contribution),
    note: parseNullableString(raw.note ?? null),
  };
}

function parseRecoveryResult(raw: unknown): RecoveryResult {
  if (!isRecord(raw)) unexpected();
  const score = parseOptionalFinite(raw.score);
  if (score !== null && (score < 0 || score > 100)) unexpected();
  return {
    day: parseDay(raw.day),
    score,
    algorithmVersion: parseString(raw.algorithm_version ?? RECOVERY_ALGORITHM),
    contributions: Array.isArray(raw.contributions)
      ? raw.contributions.map(parseRecoveryContribution)
      : [],
    missingInputs: parseStringArray(raw.missing_inputs),
    caveats: parseStringArray(raw.caveats),
  };
}

function parseHrvSummary(raw: unknown): HrvSummary {
  if (!isRecord(raw)) unexpected();
  return {
    day: parseDay(raw.day),
    rmssdMs: parseOptionalFinite(raw.rmssd_ms),
    sdnnMs: parseOptionalFinite(raw.sdnn_ms),
    pnn50: parseOptionalRatio(raw.pnn50),
    meanRrMs: parseOptionalFinite(raw.mean_rr_ms),
    samples: parseCounter(raw.samples),
    validSamples: parseCounter(raw.valid_samples),
    artifactCount: parseCounter(raw.artifact_count),
    coverage: parseRatio(raw.coverage, 0),
    filterVersion: parseString(raw.filter_version ?? HRV_FILTER_VERSION),
    algorithmVersion: parseString(raw.algorithm_version ?? HRV_ALGORITHM),
    sessionCount: parseCounter(raw.session_count),
  };
}

function parseSleepSummary(raw: unknown): SleepSummary {
  if (!isRecord(raw)) unexpected();
  return {
    day: parseDay(raw.day),
    durationMinutes: parseOptionalFinite(raw.duration_minutes),
    efficiency: parseOptionalRatio(raw.efficiency),
    restingHr: parseOptionalFinite(raw.resting_hr),
    avgHrv: parseOptionalFinite(raw.avg_hrv),
    sourceRecordIds: Array.isArray(raw.source_record_ids)
      ? raw.source_record_ids.filter(
          (entry): entry is string => typeof entry === "string",
        )
      : [],
  };
}

function parseToday(raw: unknown): TodayResponse {
  if (!isRecord(raw)) unexpected();
  return {
    date: parseDay(raw.date),
    timezone: parseString(raw.timezone),
    recovery:
      raw.recovery === null || raw.recovery === undefined
        ? null
        : parseRecoveryResult(raw.recovery),
    hrv: raw.hrv === null || raw.hrv === undefined ? null : parseHrvSummary(raw.hrv),
    sleep:
      raw.sleep === null || raw.sleep === undefined
        ? null
        : parseSleepSummary(raw.sleep),
    restingHr: parseOptionalFinite(raw.resting_hr),
    restingHrQuality: parseNullableString(raw.resting_hr_quality ?? null),
    dataFreshnessMinutes: parseOptionalFinite(raw.data_freshness_minutes),
    caveats: parseStringArray(raw.caveats),
  };
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

/**
 * GET /api/v1/metrics/daily?days=N — unauthenticated today, like the other
 * metric endpoints. Days are returned oldest-first, one row per local day.
 */
export function fetchDailySummary(days: number): Promise<DailySummaryResponse> {
  return getJson(`/api/v1/metrics/daily?days=${days}`, false, parseDailySummary);
}

/**
 * GET /api/v1/metrics/today — unauthenticated, like the other metric
 * endpoints. The day's recovery result and HRV/sleep summaries for the
 * current wake-date, in the day's effective timezone (ADR 0017).
 */
export function fetchToday(): Promise<TodayResponse> {
  return getJson("/api/v1/metrics/today", false, parseToday);
}

// ---------------------------------------------------------------------------
// Correlation matrix contract mirror (M10, somatriq_api/correlations.py —
// frozen shapes). The matrix is unauthenticated like the other metric
// reads; every field is validated, and the causal-language note travels on
// the wire and is rendered verbatim (spec §82: no causal claims, ever).
// ---------------------------------------------------------------------------

export type CorrelationBand = "none" | "weak" | "moderate" | "strong";

export type CorrelationMethod = "pearson" | "spearman";

export type CorrelationPairRow = {
  /** Canonical catalog order within the pair. */
  pair: [string, string];
  /** Shared days the coefficient was computed over. */
  n: number;
  /** Spearman ρ (default) or Pearson r, in [-1, 1]. */
  r: number;
  band: CorrelationBand;
  /** p < Bonferroni-adjusted α for the n_tests coefficients computed. */
  significant: boolean;
};

export type CorrelationSkippedRow = {
  pair: [string, string];
  /** Why no coefficient exists — reported, never hidden. */
  reason: string;
};

export type CorrelationMatrixResponse = {
  days: number;
  method: CorrelationMethod;
  nTests: number;
  bonferroniAlpha: number;
  pairs: CorrelationPairRow[];
  skipped: CorrelationSkippedRow[];
  note: string;
};

function parseCorrelationBand(value: unknown): CorrelationBand {
  switch (value) {
    case "none":
    case "weak":
    case "moderate":
    case "strong":
      return value;
    default:
      unexpected();
  }
}

function parseCorrelationMethod(value: unknown): CorrelationMethod {
  switch (value) {
    case "pearson":
    case "spearman":
      return value;
    default:
      unexpected();
  }
}

/** Required finite float (the optional variant returns null instead). */
function parseRequiredFinite(value: unknown): number {
  const parsed = parseFiniteNumber(value);
  return parsed === null ? unexpected() : parsed;
}

function parseRequiredCounter(value: unknown): number {
  return Math.round(parseRequiredFinite(value));
}

function parseMetricPair(value: unknown): [string, string] {
  if (!Array.isArray(value) || value.length !== 2) unexpected();
  return [parseString(value[0]), parseString(value[1])];
}

function parseCorrelationPairRow(raw: unknown): CorrelationPairRow {
  if (!isRecord(raw)) unexpected();
  const r = parseRequiredFinite(raw.r);
  if (r < -1 || r > 1) unexpected();
  return {
    pair: parseMetricPair(raw.pair),
    n: parseRequiredCounter(raw.n),
    r,
    band: parseCorrelationBand(raw.band),
    significant: raw.significant === true,
  };
}

function parseCorrelationSkippedRow(raw: unknown): CorrelationSkippedRow {
  if (!isRecord(raw)) unexpected();
  return { pair: parseMetricPair(raw.pair), reason: parseString(raw.reason) };
}

function parseCorrelationMatrix(raw: unknown): CorrelationMatrixResponse {
  if (!isRecord(raw)) unexpected();
  if (!Array.isArray(raw.pairs) || !Array.isArray(raw.skipped)) unexpected();
  const alpha = parseRequiredFinite(raw.bonferroni_alpha);
  if (alpha <= 0 || alpha > 1) unexpected();
  return {
    days: parseRequiredCounter(raw.days),
    method: parseCorrelationMethod(raw.method),
    nTests: parseRequiredCounter(raw.n_tests),
    bonferroniAlpha: alpha,
    pairs: raw.pairs.map(parseCorrelationPairRow),
    skipped: raw.skipped.map(parseCorrelationSkippedRow),
    note: parseString(raw.note),
  };
}

/**
 * GET /api/v1/correlations/matrix?days=N — every catalog pair over the
 * window, sorted by |r| descending. Insufficient pairs arrive in `skipped`
 * with their reasons; the note is the persistent causal-language footer.
 */
export function fetchCorrelationMatrix(days: number): Promise<CorrelationMatrixResponse> {
  return getJson(`/api/v1/correlations/matrix?days=${days}`, false, parseCorrelationMatrix);
}

// ---------------------------------------------------------------------------
// Experiments contract mirror (M11, somatriq_api/experiments.py — frozen
// shapes). Reads are unauthenticated like the other metric surfaces; the
// create write carries the account JWT. The evaluation is computed on read
// once the experiment is completed (evaluation_version distinguishes it);
// its verdict wording is validated on the wire — "consistent with effect",
// never "proves" (spec §82).
// ---------------------------------------------------------------------------

export type ExperimentStatus = "running" | "completed" | "abandoned";

export type ExperimentDirection = "increase" | "decrease" | "any";

export type ExperimentVerdict =
  | "inconclusive"
  | "consistent with effect"
  | "opposite of hypothesis";

export type ExperimentPhase = "baseline" | "intervention";

export type PhaseProgress = {
  /** Days materialized through today. */
  elapsed: number;
  /** Configured phase length. */
  total: number;
};

export type PhaseCompliance = {
  complied: number;
  /** Materialized days (elapsed), not configured days. */
  total: number;
};

export type ExperimentEvaluation = {
  evaluationVersion: string;
  verdict: ExperimentVerdict;
  nBaseline: number;
  nIntervention: number;
  meanBaseline: number | null;
  meanIntervention: number | null;
  meanDifference: number | null;
  cohensD: number | null;
  welchT: number | null;
  welch_df: number | null;
  pValue: number | null;
  pMethod: string;
  /** Non-complied days excluded from the comparison (spec §85 first-class). */
  excludedNoncomplied: number;
  caveat: string;
};

export type Experiment = {
  id: string;
  name: string;
  hypothesis: string;
  intervention: string;
  metric: string;
  direction: ExperimentDirection;
  baselineDays: number;
  interventionDays: number;
  status: ExperimentStatus;
  startedAt: string;
  createdAt: string;
  completedAt: string | null;
  window: { firstDay: string; lastDay: string };
  currentPhase: ExperimentPhase | null;
  progress: { baseline: PhaseProgress; intervention: PhaseProgress };
  compliance: { baseline: PhaseCompliance; intervention: PhaseCompliance };
  /** Present once completed — computed live on each read. */
  evaluation: ExperimentEvaluation | null;
};

export type ExperimentDraft = {
  name: string;
  hypothesis: string;
  intervention: string;
  metric: string;
  direction: ExperimentDirection;
};

/** Curated outcome-metric options (mirrors MATRIX_METRICS; the server
 * validates against the full correlation catalog). */
export const EXPERIMENT_METRIC_OPTIONS: readonly string[] = [
  "resting_hr",
  "hr_mean",
  "hr_min",
  "hr_max",
  "avg_hrv",
  "recovery",
  "strain",
  "total_sleep_min",
  "spo2_pct",
  "skin_temp_dev_c",
  "resp_rate_bpm",
  "caffeine_count",
];

function parseExperimentStatus(value: unknown): ExperimentStatus {
  switch (value) {
    case "running":
    case "completed":
    case "abandoned":
      return value;
    default:
      unexpected();
  }
}

function parseExperimentDirection(value: unknown): ExperimentDirection {
  switch (value) {
    case "increase":
    case "decrease":
    case "any":
      return value;
    default:
      unexpected();
  }
}

function parseExperimentVerdict(value: unknown): ExperimentVerdict {
  switch (value) {
    case "inconclusive":
    case "consistent with effect":
    case "opposite of hypothesis":
      return value;
    default:
      unexpected();
  }
}

function parseExperimentPhase(value: unknown): ExperimentPhase {
  switch (value) {
    case "baseline":
    case "intervention":
      return value;
    default:
      unexpected();
  }
}

function parseOptionalCounter(value: unknown): number | null {
  const parsed = parseOptionalFinite(value);
  return parsed === null ? null : Math.round(parsed);
}

function parsePhaseProgress(raw: unknown): PhaseProgress {
  if (!isRecord(raw)) unexpected();
  return { elapsed: parseRequiredCounter(raw.elapsed), total: parseRequiredCounter(raw.total) };
}

function parsePhaseCompliance(raw: unknown): PhaseCompliance {
  if (!isRecord(raw)) unexpected();
  return {
    complied: parseRequiredCounter(raw.complied),
    total: parseRequiredCounter(raw.total),
  };
}

function parseExperimentEvaluation(raw: unknown): ExperimentEvaluation {
  if (!isRecord(raw)) unexpected();
  return {
    evaluationVersion: parseString(raw.evaluation_version),
    verdict: parseExperimentVerdict(raw.verdict),
    nBaseline: parseRequiredCounter(raw.n_baseline),
    nIntervention: parseRequiredCounter(raw.n_intervention),
    meanBaseline: parseOptionalFinite(raw.mean_baseline),
    meanIntervention: parseOptionalFinite(raw.mean_intervention),
    meanDifference: parseOptionalFinite(raw.mean_difference),
    cohensD: parseOptionalFinite(raw.cohens_d),
    welchT: parseOptionalFinite(raw.welch_t),
    welch_df: parseOptionalCounter(raw.welch_df),
    pValue: parseOptionalFinite(raw.p_value),
    pMethod: parseString(raw.p_method),
    excludedNoncomplied: parseRequiredCounter(raw.excluded_noncomplied),
    caveat: parseString(raw.caveat),
  };
}

function parseExperiment(raw: unknown): Experiment {
  if (!isRecord(raw)) unexpected();
  if (!isRecord(raw.progress) || !isRecord(raw.compliance) || !isRecord(raw.window)) {
    unexpected();
  }
  const window = raw.window;
  const currentPhase =
    raw.current_phase === null || raw.current_phase === undefined
      ? null
      : parseExperimentPhase(raw.current_phase);
  return {
    id: parseString(raw.id),
    name: parseString(raw.name),
    hypothesis: parseString(raw.hypothesis),
    intervention: parseString(raw.intervention),
    metric: parseString(raw.metric),
    direction: parseExperimentDirection(raw.direction),
    baselineDays: parseRequiredCounter(raw.baseline_days),
    interventionDays: parseRequiredCounter(raw.intervention_days),
    status: parseExperimentStatus(raw.status),
    startedAt: parseString(raw.started_at),
    createdAt: parseString(raw.created_at),
    completedAt: parseNullableString(raw.completed_at ?? null),
    window: {
      firstDay: parseDay(window.first_day),
      lastDay: parseDay(window.last_day),
    },
    currentPhase,
    progress: {
      baseline: parsePhaseProgress(raw.progress.baseline),
      intervention: parsePhaseProgress(raw.progress.intervention),
    },
    compliance: {
      baseline: parsePhaseCompliance(raw.compliance.baseline),
      intervention: parsePhaseCompliance(raw.compliance.intervention),
    },
    evaluation:
      raw.evaluation === null || raw.evaluation === undefined
        ? null
        : parseExperimentEvaluation(raw.evaluation),
  };
}

function parseExperimentList(raw: unknown): Experiment[] {
  if (!Array.isArray(raw)) unexpected();
  return raw.map(parseExperiment);
}

/** GET /api/v1/experiments — every experiment, live progress + evaluation. */
export function fetchExperiments(): Promise<Experiment[]> {
  return getJson("/api/v1/experiments", false, parseExperimentList);
}

/** POST /api/v1/experiments — account JWT; baseline window backfilled today. */
export function createExperiment(draft: ExperimentDraft): Promise<Experiment> {
  return postJson("/api/v1/experiments", draft, true, parseExperiment);
}
