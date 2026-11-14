const API_BASE = import.meta.env.VITE_API_URL || "/api";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function apiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, options);
  } catch {
    throw new ApiError("Mirrorling could not reach the backend.", 0);
  }

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.blob();

  if (!response.ok) {
    const detail = payload && typeof payload === "object" ? payload.detail : null;
    throw new ApiError(
      typeof detail === "string" ? detail : `Request failed (${response.status}).`,
      response.status,
    );
  }

  return payload;
}

export function apiJson(path, method = "GET", body) {
  return apiRequest(path, {
    method,
    ...(body === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
}

export function apiUrl(path) {
  return `${API_BASE}${path}`;
}

export function localStorageJson(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : fallback;
  } catch {
    return fallback;
  }
}

export function saveLocalJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

export function sessionStorageKey(userId, companionId) {
  return `mirrorling:session:${userId}:${companionId}`;
}

export function getSessionId(userId, companionId) {
  const key = sessionStorageKey(userId, companionId);
  let sessionId = localStorage.getItem(key);
  if (!sessionId) {
    sessionId = crypto.randomUUID();
    localStorage.setItem(key, sessionId);
  }
  return sessionId;
}

export { API_BASE };
