// Small RFC 4180 reader and writer for the batch scorer: quoted fields, doubled quotes, CRLF, a BOM.

export function parseCsv(text) {
  const records = [];
  let record = [];
  let field = "";
  let quoted = false;
  const src = text.replace(/^﻿/, "");
  for (let i = 0; i < src.length; i += 1) {
    const c = src[i];
    if (quoted) {
      if (c === '"' && src[i + 1] === '"') {
        field += '"';
        i += 1;
      } else if (c === '"') {
        quoted = false;
      } else {
        field += c;
      }
    } else if (c === '"') {
      quoted = true;
    } else if (c === ",") {
      record.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && src[i + 1] === "\n") i += 1;
      record.push(field);
      records.push(record);
      record = [];
      field = "";
    } else {
      field += c;
    }
  }
  if (field !== "" || record.length) {
    record.push(field);
    records.push(record);
  }
  const filled = records.filter((r) => r.some((v) => v.trim() !== ""));
  const [head = [], ...body] = filled;
  const header = head.map((h) => h.trim());
  const rows = body.map((r) => Object.fromEntries(header.map((h, j) => [h, (r[j] ?? "").trim()])));
  return { header, rows };
}

const cell = (value) => {
  const text = value == null ? "" : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

export function toCsv(header, rows) {
  return [header.map(cell).join(","), ...rows.map((row) => header.map((h) => cell(row[h])).join(","))].join("\r\n") + "\r\n";
}

export function downloadCsv(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
