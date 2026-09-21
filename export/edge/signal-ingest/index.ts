// Signal ingest Edge Function.
// Holds service role on the server. Agent sends x-signal-upload-secret only.
// Spec: collector_framework/package/INGEST_SPEC.md
// Accepts source_events (events) and optional curated observables.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.1";

const MAX_EVENTS = 500;
const MAX_OBSERVABLES = 500;
const SECRET_HEADER = "x-signal-upload-secret";

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method !== "POST") {
    return jsonResponse({ error: "method_not_allowed" }, 405);
  }
  const authError = checkSecret(req);
  if (authError) {
    return authError;
  }
  try {
    const body = await req.json();
    return await handleIngest(body);
  } catch (err) {
    const message = err instanceof Error ? err.message : "server_error";
    return jsonResponse({ error: message }, 500);
  }
});

function jsonResponse(payload: unknown, status: number): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function checkSecret(req: Request): Response | null {
  const expected = Deno.env.get("SIGNAL_UPLOAD_SECRET") || "";
  const provided = req.headers.get(SECRET_HEADER) || "";
  if (!expected || !secretsEqual(expected, provided)) {
    return jsonResponse({ error: "unauthorized" }, 401);
  }
  return null;
}

function secretsEqual(a: string, b: string): boolean {
  const encoder = new TextEncoder();
  const left = encoder.encode(a);
  const right = encoder.encode(b);
  if (left.length !== right.length) {
    return false;
  }
  let diff = 0;
  for (let i = 0; i < left.length; i++) {
    diff |= left[i] ^ right[i];
  }
  return diff === 0;
}

function validateEvents(events: unknown): Response | null {
  if (!Array.isArray(events)) {
    return jsonResponse({ error: "events_must_be_list" }, 400);
  }
  if (events.length > MAX_EVENTS) {
    return jsonResponse({ error: "events_exceed_max" }, 400);
  }
  for (let i = 0; i < events.length; i++) {
    const row = events[i];
    if (!row || typeof row !== "object" || !("payload" in (row as object))) {
      return jsonResponse({ error: `events[${i}] missing payload` }, 400);
    }
  }
  return null;
}

function validateObservables(observables: unknown): Response | null {
  if (observables === undefined || observables === null) {
    return null;
  }
  if (!Array.isArray(observables)) {
    return jsonResponse({ error: "observables_must_be_list" }, 400);
  }
  if (observables.length > MAX_OBSERVABLES) {
    return jsonResponse({ error: "observables_exceed_max" }, 400);
  }
  for (let i = 0; i < observables.length; i++) {
    const row = observables[i];
    if (!row || typeof row !== "object") {
      return jsonResponse({ error: `observables[${i}] not_object` }, 400);
    }
    const rec = row as Record<string, unknown>;
    if (!rec.dedup_key || !rec.observable_type || !rec.tool) {
      return jsonResponse(
        { error: `observables[${i}] missing dedup_key|observable_type|tool` },
        400,
      );
    }
  }
  return null;
}

async function handleIngest(body: Record<string, unknown>): Promise<Response> {
  const eventsError = validateEvents(body.events);
  if (eventsError) {
    return eventsError;
  }
  const obsError = validateObservables(body.observables);
  if (obsError) {
    return obsError;
  }

  const events = body.events as unknown[];
  const observables = Array.isArray(body.observables)
    ? (body.observables as Record<string, unknown>[])
    : [];

  const supabase = makeClient();
  const ingestBatchId = String(body.ingest_batch_id || crypto.randomUUID());

  if (body.tenant && typeof body.tenant === "object") {
    await upsertTenant(supabase, body.tenant as Record<string, unknown>);
  }
  if (body.device && typeof body.device === "object") {
    await upsertDevice(supabase, body.device as Record<string, unknown>);
  }

  const inserted = await insertSourceEvents(supabase, events, ingestBatchId);
  const observablesUpserted = await upsertObservables(
    supabase,
    observables,
    ingestBatchId,
  );

  return jsonResponse(
    {
      inserted,
      observables_upserted: observablesUpserted,
      ingest_batch_id: ingestBatchId,
    },
    200,
  );
}

async function insertSourceEvents(
  supabase: ReturnType<typeof createClient>,
  events: unknown[],
  ingestBatchId: string,
): Promise<number> {
  if (events.length === 0) {
    return 0;
  }
  const rows = events.map((row) => {
    const r = row as Record<string, unknown>;
    return { ...r, ingest_batch_id: ingestBatchId };
  });
  const { error } = await supabase.from("source_events").insert(rows);
  if (error) {
    throw new Error(error.message);
  }
  return rows.length;
}

async function upsertObservables(
  supabase: ReturnType<typeof createClient>,
  observables: Record<string, unknown>[],
  ingestBatchId: string,
): Promise<number> {
  if (observables.length === 0) {
    return 0;
  }
  const rows = observables.map((row) => ({
    ...row,
    ingest_batch_id: row.ingest_batch_id || ingestBatchId,
  }));
  const { error } = await supabase.from("observables").upsert(rows, {
    onConflict: "dedup_key",
    ignoreDuplicates: false,
  });
  if (error) {
    throw new Error(`observables_upsert: ${error.message}`);
  }
  return rows.length;
}

function makeClient() {
  // Dashboard secrets must not start with SUPABASE_ (platform rule).
  const url = Deno.env.get("SIGNAL_SUPABASE_URL") || "";
  const key = Deno.env.get("SIGNAL_SERVICE_ROLE_KEY") || "";
  if (!url || !key) {
    throw new Error("missing_supabase_env");
  }
  return createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

async function upsertTenant(
  supabase: ReturnType<typeof createClient>,
  tenant: Record<string, unknown>,
): Promise<void> {
  const row = {
    tenant_id: tenant.tenant_id,
    name: tenant.name || "Pilot",
    redaction_policy: tenant.redaction_policy || "redacted",
    active: true,
    domain: tenant.domain ?? null,
  };
  const { error } = await supabase.from("tenants").upsert(row, {
    onConflict: "tenant_id",
  });
  if (error) {
    throw new Error(`tenant_upsert: ${error.message}`);
  }
}

async function upsertDevice(
  supabase: ReturnType<typeof createClient>,
  device: Record<string, unknown>,
): Promise<void> {
  const row = {
    device_id: device.device_id,
    tenant_id: device.tenant_id,
    hostname: device.hostname ?? null,
    os: device.os ?? null,
    operator_email: device.operator_email ?? null,
    package_version: device.package_version ?? null,
    last_seen: new Date().toISOString(),
    active: true,
  };
  const { error } = await supabase.from("devices").upsert(row, {
    onConflict: "device_id",
  });
  if (error) {
    throw new Error(`device_upsert: ${error.message}`);
  }
}
