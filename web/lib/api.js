export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
