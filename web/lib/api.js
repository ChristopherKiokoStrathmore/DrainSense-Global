// Unset: the local API. Empty string: the API on the desk's own origin (production, behind Caddy).
export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

// FastAPI returns `detail` as a string (our own 422s) or a list (schema errors). Make it one readable line.
export function errorDetail(err) {
  try {
    const detail = JSON.parse(err.message).detail;
    if (typeof detail === "string") return detail;
    return detail.map((d) => `${d.loc.slice(1).join(".")}: ${d.msg}`).join("; ");
  } catch {
    return err.message;
  }
}
