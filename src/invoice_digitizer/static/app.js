"use strict";

const $ = (id) => document.getElementById(id);
const statusLabels = {uploaded: "Uploaded", extracting: "Reading…", extracted: "Extracted", failed: "Failed"};
const typeLabels = {invoice: "Invoice", credit_note: "Credit note", other: "Other"};
const pageSize = 25;
let offset = 0;
let selected = null;
let selectedRecord = null;
let listVersion = 0;
let detailVersion = 0;
let listSignature = "";
let detailSignature = "";
let previewId = null;
let processing = false;
const queue = [];
const pendingIds = new Set();

function node(tag, value, className) {
  const element = document.createElement(tag);
  if (value !== undefined) element.textContent = value ?? "—";
  if (className) element.className = className;
  return element;
}

function formatAmount(value) {
  if (value === null || value === undefined) return "—";
  // Move the decimal point as text, including Decimal's scientific notation.
  const match = String(value).match(/^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/);
  if (!match) return String(value);
  const [, sign, whole, fraction = "", exponent = "0"] = match;
  const digits = whole + fraction;
  const point = whole.length + parseInt(exponent, 10);
  let integer, decimals;
  if (point <= 0) {
    integer = "0";
    decimals = "0".repeat(-point) + digits;
  } else if (point >= digits.length) {
    integer = digits + "0".repeat(point - digits.length);
    decimals = "";
  } else {
    integer = digits.slice(0, point);
    decimals = digits.slice(point);
  }
  integer = integer.replace(/^0+(?=\d)/, "");
  return sign + integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + "." + decimals.padEnd(2, "0");
}

function totalText(record) {
  if (record.total === null) return "—";
  return formatAmount(record.total) + (record.currency ? ` ${record.currency}` : "");
}

function badge(status) {
  return node("span", statusLabels[status] ?? status, `status ${status}`);
}

function showError(id, message = "") {
  $(id).textContent = message;
  $(id).hidden = !message;
}

async function request(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status}). Please try again.`);
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function filters() {
  const params = new URLSearchParams();
  if ($("search").value.trim()) params.set("q", $("search").value.trim());
  if ($("status-filter").value) params.set("status", $("status-filter").value);
  return params;
}

function updateExport() {
  $("export-link").href = `/api/invoices/export.csv?${filters()}`;
}

function renderList(data) {
  const signature = JSON.stringify([data, selected]);
  if (signature === listSignature) return;
  listSignature = signature;
  const focusedId = document.activeElement?.dataset.invoiceId;
  const fragment = document.createDocumentFragment();
  for (const record of data.items) {
    const row = node("tr");
    row.dataset.invoiceId = record.id;
    row.classList.toggle("selected", record.id === selected);
    const fileCell = node("td");
    const button = node("button", record.filename, "file-link");
    button.dataset.invoiceId = record.id;
    button.setAttribute("aria-expanded", String(record.id === selected));
    button.setAttribute("aria-controls", "detail");
    fileCell.append(button);
    row.append(fileCell, node("td", record.supplier_name), node("td", record.invoice_number), node("td", record.invoice_date));
    const total = node("td", totalText(record), "numeric");
    if (record.total?.startsWith("-")) total.classList.add("negative");
    const state = node("td");
    state.append(badge(record.status));
    row.append(total, state);
    row.addEventListener("click", () => openDetail(record));
    fragment.append(row);
  }
  $("invoice-rows").replaceChildren(fragment);
  if (focusedId) {
    const replacement = $("invoice-rows").querySelector(`button[data-invoice-id="${focusedId}"]`);
    (replacement ?? $("invoices")).focus({preventScroll: true});
  }
  $("empty-state").hidden = data.items.length > 0;
  $("empty-state").textContent = filters().size ? "No invoices match these filters." : "No invoices yet. Upload a file to get started.";
  $("result-count").textContent = data.total ? `${offset + 1}–${offset + data.items.length} of ${data.total} ${data.total === 1 ? "invoice" : "invoices"}` : "0 invoices";
  $("previous").disabled = offset === 0;
  $("next").disabled = offset + pageSize >= data.total;
}

async function refreshList() {
  const version = ++listVersion;
  const params = filters();
  params.set("limit", pageSize);
  params.set("offset", offset);
  try {
    const data = await request(`/api/invoices?${params}`);
    if (version !== listVersion) return;
    if (!data.items.length && offset > 0) {
      offset = Math.max(0, Math.ceil(data.total / pageSize) - 1) * pageSize;
      return refreshList();
    }
    renderList(data);
    showError("list-error");
  } catch (error) {
    if (version === listVersion) showError("list-error", `Could not load invoices. ${error.message}`);
  }
}

function renderDetail(record) {
  selectedRecord = record;
  const busy = pendingIds.has(record.id);
  const signature = JSON.stringify([record, busy]);
  if (signature === detailSignature) return;
  detailSignature = signature;
  $("detail-title").textContent = record.filename;
  $("detail-status").replaceChildren(badge(record.status));
  showError("extraction-error", record.extraction_error);
  $("reading-message").hidden = record.status !== "extracting" && !busy;
  $("retry").hidden = record.status !== "failed" && record.status !== "uploaded";
  $("retry").textContent = record.status === "failed" ? "Retry" : "Read invoice";
  $("retry").disabled = busy;
  $("delete").hidden = record.status === "extracting";
  $("delete").disabled = busy;
  const fields = [
    ["Document type", typeLabels[record.document_type]], ["Invoice number", record.invoice_number],
    ["Invoice date", record.invoice_date], ["Due date", record.due_date],
    ["Supplier", record.supplier_name], ["Supplier tax ID", record.supplier_tax_id],
    ["Buyer", record.buyer_name], ["Buyer tax ID", record.buyer_tax_id],
    ["Currency", record.currency], ["Subtotal", formatAmount(record.subtotal)],
    ["Tax", formatAmount(record.tax_amount)], ["Total", totalText(record)],
  ];
  $("fields").replaceChildren(...fields.map(([label, value]) => {
    const group = node("div");
    group.append(node("dt", label), node("dd", value ?? "—"));
    if (label === "Total") {
      group.className = "total-field";
      if (record.total?.startsWith("-")) group.classList.add("negative");
    }
    return group;
  }));
  $("line-items").replaceChildren(...record.line_items.map((item) => {
    const row = node("tr");
    row.append(node("td", item.description), node("td", item.quantity, "numeric"));
    for (const key of ["unit_price", "amount"]) row.append(node("td", formatAmount(item[key]), "numeric"));
    return row;
  }));
  $("no-items").hidden = record.line_items.length > 0;
  if (previewId !== record.id) {
    previewId = record.id;
    const url = `/api/invoices/${record.id}/file`;
    $("original-link").href = url;
    const preview = node(record.content_type === "application/pdf" ? "iframe" : "img");
    if (preview.tagName === "IFRAME") {
      preview.title = `Original invoice: ${record.filename}`;
    } else {
      preview.alt = `Original invoice: ${record.filename}`;
    }
    preview.src = url;
    $("preview").replaceChildren(preview);
  }
}

function openDetail(record) {
  selected = record.id;
  detailVersion++;
  $("detail").hidden = false;
  showError("detail-error");
  renderDetail(record);
  $("detail-title").focus({preventScroll: true});
  $("detail").scrollIntoView({block: "start"});
  refreshList();
  refreshDetail();
}

function closeDetail() {
  const previous = selected;
  selected = null;
  selectedRecord = null;
  detailVersion++;
  detailSignature = "";
  previewId = null;
  $("preview").replaceChildren();
  $("detail").hidden = true;
  const trigger = $("invoice-rows").querySelector(`button[data-invoice-id="${previous}"]`);
  (trigger ?? $("invoices")).focus({preventScroll: true});
  refreshList();
}

async function refreshDetail() {
  if (selected === null) return;
  const id = selected;
  const version = ++detailVersion;
  try {
    const record = await request(`/api/invoices/${id}`);
    if (selected === id && version === detailVersion) renderDetail(record);
  } catch (error) {
    if (selected !== id || version !== detailVersion) return;
    if (error.status === 404) closeDetail();
    else showError("detail-error", error.message);
  }
}

function refresh() {
  return Promise.all([refreshList(), refreshDetail()]);
}

function addFiles(files) {
  for (const file of files) {
    const log = node("li", `${file.name} — Queued`);
    $("upload-log").append(log);
    queue.push({file, log});
  }
  $("upload-log").hidden = false;
  runQueue();
}

async function runQueue() {
  if (processing) return;
  processing = true;
  while (queue.length) {
    const job = queue.shift();
    let id = job.id;
    const name = job.file?.name ?? job.name;
    try {
      if (job.file) {
        $("queue-status").textContent = `Uploading ${name} · ${queue.length} waiting`;
        job.log.textContent = `${name} — Uploading…`;
        const form = new FormData();
        form.append("file", job.file);
        const uploaded = await request("/api/invoices", {method: "POST", body: form});
        id = uploaded.id;
        pendingIds.add(id);
      }
      $("queue-status").textContent = `Reading ${name} · ${queue.length} waiting`;
      job.log.textContent = `${name} — Reading…`;
      const reading = request(`/api/invoices/${id}/extract`, {method: "POST"});
      refresh();
      const record = await reading;
      job.log.textContent = `${name} — ${record.status === "failed" ? record.extraction_error : "Extracted"}`;
      job.log.classList.toggle("error", record.status === "failed");
    } catch (error) {
      job.log.textContent = `${name} — ${error.message}`;
      job.log.classList.add("error");
    } finally {
      pendingIds.delete(id);
      await refresh();
    }
  }
  processing = false;
  $("queue-status").textContent = "Queue complete. See each file’s result below.";
}

$("file-input").addEventListener("change", (event) => {
  addFiles(event.target.files);
  event.target.value = "";
});
for (const eventName of ["dragenter", "dragover"]) {
  $("drop-area").addEventListener(eventName, (event) => {
    event.preventDefault();
    $("drop-area").classList.add("dragging");
  });
}
$("drop-area").addEventListener("dragleave", (event) => {
  if (!$("drop-area").contains(event.relatedTarget)) $("drop-area").classList.remove("dragging");
});
$("drop-area").addEventListener("drop", (event) => {
  event.preventDefault();
  $("drop-area").classList.remove("dragging");
  addFiles(event.dataTransfer.files);
});

let searchTimer;
$("search").addEventListener("input", () => {
  updateExport();
  ++listVersion;
  offset = 0;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(refreshList, 250);
});
$("status-filter").addEventListener("change", () => {
  offset = 0;
  updateExport();
  refreshList();
});
$("previous").addEventListener("click", () => { offset = Math.max(0, offset - pageSize); refreshList(); });
$("next").addEventListener("click", () => { offset += pageSize; refreshList(); });
$("close-detail").addEventListener("click", closeDetail);
$("retry").addEventListener("click", () => {
  if (!selectedRecord || pendingIds.has(selected)) return;
  pendingIds.add(selected);
  const log = node("li", `${selectedRecord.filename} — Queued`);
  $("upload-log").append(log);
  $("upload-log").hidden = false;
  queue.push({id: selected, name: selectedRecord.filename, log});
  renderDetail(selectedRecord);
  runQueue();
});
$("delete").addEventListener("click", async () => {
  if (!selectedRecord || pendingIds.has(selected)) return;
  const id = selected;
  if (!window.confirm(`Delete “${selectedRecord.filename}” and its extracted record?`)) return;
  $("delete").disabled = true;
  try {
    await request(`/api/invoices/${id}`, {method: "DELETE"});
    if (selected === id) closeDetail();
    await refreshList();
  } catch (error) {
    if (selected === id) {
      showError("detail-error", error.message);
      $("delete").disabled = false;
    }
  }
});

updateExport();
refresh();
// Also discovers jobs started in another tab, even under a status filter.
setInterval(() => { if (!document.hidden) refresh(); }, 2000);
