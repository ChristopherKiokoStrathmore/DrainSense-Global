"use client";

import { useEffect, useState } from "react";
import { API, api, errorDetail } from "../../lib/api";

const METHODS = ["get", "post", "put", "patch", "delete"];

// Not /api: Caddy sends /api/* to the FastAPI service in production.
export default function EndpointsPage() {
  const [schema, setSchema] = useState(null);
  const [origin, setOrigin] = useState(API);
  const [error, setError] = useState("");

  useEffect(() => {
    setOrigin(API || window.location.origin);
    api("/openapi.json").then(setSchema).catch((err) => setError(errorDetail(err)));
  }, []);

  const groups = [];
  if (schema) {
    const byTag = new Map((schema.tags || []).map((tag) => [tag.name, { ...tag, operations: [] }]));
    for (const [path, ops] of Object.entries(schema.paths)) {
      for (const method of METHODS) {
        const op = ops[method];
        if (!op) continue;
        const tag = op.tags?.[0] || "other";
        if (!byTag.has(tag)) byTag.set(tag, { name: tag, description: "", operations: [] });
        byTag.get(tag).operations.push({ path, method, ...op });
      }
    }
    groups.push(...[...byTag.values()].filter((group) => group.operations.length));
  }
  const count = groups.reduce((n, group) => n + group.operations.length, 0);

  return (
    <main className="sheet wide">
      <p className="kicker">Everything on the desk runs through these endpoints</p>
      <h1>API</h1>
      <p className="reading">
        The map, ledger, dispatch plan, sector flags and impact model all read the same JSON API, so any number on the
        desk can be checked, or reused by another system, without the desk. No API key is needed.
        {schema && <> This list is read live from the schema: {count} endpoints, version {schema.info.version}.</>}
      </p>
      <p className="actions">
        <a href={`${API}/docs`} target="_blank" rel="noreferrer">Swagger UI (try requests)</a>
        <a href={`${API}/redoc`} target="_blank" rel="noreferrer">ReDoc</a>
        <a href={`${API}/openapi.json`} target="_blank" rel="noreferrer">OpenAPI JSON</a>
      </p>
      <pre className="snippet">{`curl "${origin}/api/v1/risk/hotspots?limit=3"
curl "${origin}/api/v1/response/plan?limit=5"`}</pre>
      <p className="reading">
        Postman: import <code>api/postman/DrainSense.postman_collection.json</code> and its local environment from the
        repository, or run it with{" "}
        <code>npx newman run api/postman/DrainSense.postman_collection.json -e api/postman/DrainSense.local.postman_environment.json</code>.
      </p>
      {error && <p className="error">Could not load the schema from {API || "this site"}: {error}</p>}

      {groups.map((group) => (
        <section className="panel" key={group.name}>
          <h2>{group.name}</h2>
          {group.description && <p className="reading">{group.description}</p>}
          <div className="scroll">
            <table className="endpoints">
              <tbody>
                {group.operations.map((op) => (
                  <tr key={`${op.method} ${op.path}`}>
                    <td><span className={`method ${op.method}`}>{op.method}</span></td>
                    <td><code>{op.path}</code></td>
                    <td>{op.summary}</td>
                    <td>
                      <a href={`${API}/docs#/${encodeURIComponent(group.name)}/${op.operationId}`} target="_blank" rel="noreferrer">
                        Try it
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </main>
  );
}
