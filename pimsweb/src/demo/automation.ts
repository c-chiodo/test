/* The automation layer, re-implemented for the browser-only sandbox.
 *
 * Mirrors `pims/services/numbering.py`, `prefill.py`, `scan.py`, `alerts.py`,
 * `jobs.py` and `pims/integrations/` closely enough that the operator screens
 * behave the same: numbers are minted, screens arrive pre-filled with the
 * reason shown, scanned codes resolve, alert rules fire once, and the
 * scheduled jobs can be run by hand. The server remains the tested
 * implementation — this exists so the sandbox is worth clicking through. */

import { Row, byId, hoursSince, nextId, nowIso, store, todayIso } from './store'

/* --------------------------------------------------------------- settings */

export function setting(key: string, fallback = ''): string {
  const row = store.system_setting.find((item) => item.key === key)
  return row && row.value !== '' ? row.value : fallback
}

/* -------------------------------------------------------------- numbering */

export function nextInSequence(key: string): number {
  let row = store.number_sequence.find((item) => item.key === key)
  if (!row) {
    row = { key, next_value: 1 }
    store.number_sequence.push(row)
  }
  const value = Number(row.next_value)
  row.next_value = value + 1
  return value
}

export function peekSequence(key: string): number {
  const row = store.number_sequence.find((item) => item.key === key)
  return row ? Number(row.next_value) : 1
}

function formatBol(sequence: number): string {
  const prefix = setting('bol.prefix', '001-')
  return `${prefix}${String(sequence).padStart(6, '0')}-1`
}

export function nextBol(trailerNumber?: string | null): string {
  const number = formatBol(nextInSequence('bol'))
  return trailerNumber && setting('bol.append_trailer', 'true') === 'true'
    ? `${number}(${String(trailerNumber).trim()})`
    : number
}

export function bolPreview(): string {
  return formatBol(peekSequence('bol'))
}

function digitsOf(value: string | null | undefined): string {
  if (!value) return ''
  const digits = String(value).replace(/\D/g, '')
  return digits || String(value).trim().toUpperCase()
}

/** DMC1359D251216Q0360397 — plant, party, date, sequence. */
export function nextSampleNumber(orderId: number, when?: string): string {
  const order = byId.order().get(orderId)
  if (!order) return ''
  const plant = byId.plant().get(order.plant_id)?.code ?? '??'
  let party = 'W'
  if (order.customer_id) party = `C${digitsOf(byId.customer().get(order.customer_id)?.gp_custnmbr)}`
  else if (order.vendor_id) party = `A${digitsOf(byId.vendor().get(order.vendor_id)?.gp_vendorid)}`
  const moment = when ? new Date(when) : new Date()
  const date = moment.toISOString().slice(2, 10).replace(/-/g, '')
  return `${plant}${party}D${date}Q${String(nextInSequence('sample')).padStart(7, '0')}`
}

/* ---------------------------------------------------------------- prefill */

const SHAPE: Record<string, { from: boolean; to: boolean }> = {
  receive: { from: false, to: true },
  produce: { from: true, to: true },
  move: { from: true, to: true },
  load: { from: true, to: true },
  ship: { from: true, to: false },
  shrink: { from: true, to: false },
}

export function trailerHistory(trailerNumber: string, limit = 5): Row[] {
  const trailer = String(trailerNumber || '').trim()
  if (!trailer) return []
  const types = byId.transactionType()
  return store.inventory_transaction
    .filter((txn) => String(txn.trailer_number || '').trim() === trailer
      && !txn.voided && !txn.is_reversal
      && ['LOAD', 'SHIP'].includes(types.get(txn.transaction_type_id)?.code ?? ''))
    .sort((a, b) => b.transaction_id - a.transaction_id)
    .slice(0, limit)
    .map((txn) => {
      const material = byId.material().get(txn.from_material_id ?? txn.to_material_id)
      const order = byId.order().get(txn.order_id)
      return {
        transaction_id: txn.transaction_id,
        order_id: txn.order_id,
        user_date: txn.user_date,
        transaction_date: txn.transaction_date,
        operation: types.get(txn.transaction_type_id)?.code,
        material_number: material?.number ?? null,
        material_description: material?.description ?? null,
        customer_name: order ? byId.customer().get(order.customer_id)?.name ?? null : null,
      }
    })
}

export function lastMaterialHauled(trailerNumber: string): string {
  const history = trailerHistory(trailerNumber, 1)
  if (!history.length) return ''
  return `${history[0].material_number ?? ''} ${history[0].material_description ?? ''}`.trim()
}

function defaultLocation(orderTypeId: number, plantId: number): number | null {
  const row = store.location_default.find(
    (item) => item.order_type_id === orderTypeId && item.plant_id === plantId,
  )
  return row ? row.location_id : null
}

function locationOfType(plantId: number, typeName: string): number | null {
  const typeId = store.location_type.find((type) => type.name === typeName)?.location_type_id
  const location = store.location.find(
    (item) => item.plant_id === plantId && item.location_type_id === typeId && item.active,
  )
  return location ? location.location_id : null
}

export function prefillOperation(
  operation: string,
  options: { order_id?: number | null; plant_id?: number | null; trailer_number?: string | null },
  balances: (filters: Row) => Row[],
  orderOf: (orderId: number) => Row,
): Row {
  const shape = SHAPE[operation.toLowerCase()]
  if (!shape) return { operation, values: {}, notes: [], trailer_history: [] }

  const values: Row = { user_date: todayIso() }
  const notes: string[] = []
  let plantId = options.plant_id ?? null
  let order: Row | null = null

  if (options.order_id) {
    order = orderOf(Number(options.order_id))
    plantId = plantId ?? order.plant_id
    const remaining = Math.max((order.material_one_quantity ?? 0) - order.qty_fulfilled, 0)
    if (shape.from) values.from_material_id = order.material_one_id
    if (shape.to) values.to_material_id = order.material_one_id
    if (remaining > 0) {
      values[shape.from ? 'from_qty' : 'to_qty'] = Math.round(remaining * 100) / 100
      notes.push(`${remaining.toLocaleString()} lbs outstanding on order ${order.order_id}`)
    }
    if (order.trailer_number) values.trailer_number = order.trailer_number
    if (order.department_id) values.department_id = order.department_id

    if (operation === 'receive') {
      const target = defaultLocation(order.order_type_id, plantId!)
      if (target) {
        values.to_location_id = target
        notes.push("Receiving location from the plant's default")
      }
    } else if (['load', 'ship', 'shrink'].includes(operation) && order.material_one_id) {
      const holdings = balances({ plant_id: plantId, material_id: order.material_one_id })
        .filter((row) => row.balance > 0 && ['Tank', 'Blend'].includes(row.location_type))
      if (holdings.length) {
        const best = holdings.reduce((max, row) => (row.balance > max.balance ? row : max))
        values.from_location_id = best.location_id
        notes.push(
          `${best.location_number} holds the most ${best.material_number} (${Math.round(best.balance).toLocaleString()} lbs)`,
        )
      }
      if (operation === 'load') {
        const staging = locationOfType(plantId!, 'Trailer')
        if (staging) values.to_location_id = staging
      }
    } else if (operation === 'produce') {
      const blend = locationOfType(plantId!, 'Blend')
      const target = defaultLocation(order.order_type_id, plantId!)
      if (blend) values.from_location_id = blend
      if (target) values.to_location_id = target
    }
  }

  const trailer = options.trailer_number || values.trailer_number || ''
  if (trailer) {
    const previous = lastMaterialHauled(trailer)
    if (previous) {
      values.last_material_hauled = previous
      notes.push(`Trailer ${trailer} last hauled ${previous}`)
    }
  }
  if (['receive', 'load'].includes(operation)) {
    values.bol_preview = bolPreview()
    notes.push('BOL number is generated when you post')
  }

  return {
    operation,
    order_id: options.order_id ?? null,
    plant_id: plantId,
    values,
    notes,
    trailer_history: trailer ? trailerHistory(trailer) : [],
  }
}

export function prefillQc(orderId: number, validate: (id: number, payload: Row) => Row): Row {
  const order = byId.order().get(orderId)
  const types = byId.transactionType()
  const load = store.inventory_transaction
    .filter((txn) => txn.order_id === orderId && !txn.voided && !txn.is_reversal
      && types.get(txn.transaction_type_id)?.code === 'LOAD')
    .sort((a, b) => b.transaction_id - a.transaction_id)[0]
  const previous = store.qc
    .filter((record) => record.order_id === orderId && record.active)
    .sort((a, b) => b.qc_id - a.qc_id)[0]

  const values: Row = { test_date: todayIso() }
  if (load) {
    values.bol_number = load.to_bol
    if (load.trailer_number) values.last_material_hauled = lastMaterialHauled(load.trailer_number)
  }
  if (!values.last_material_hauled && previous) {
    values.last_material_hauled = previous.last_material_hauled
  }

  return {
    order_id: orderId,
    values,
    sample_auto_generate: setting('sample.auto_generate', 'false') === 'true',
    sample_preview: { sample_sequence: peekSequence('sample'), bol_number: bolPreview() },
    validation: order ? validate(orderId, values) : null,
  }
}

/* ------------------------------------------------------------------- scan */

export function resolveScan(code: string, plantId?: number | null): Row {
  const query = (code || '').trim()
  if (!query) return { query, hits: [] }
  const upper = query.toUpperCase()
  const hits: Row[] = []

  if (/^\d+$/.test(query)) {
    const order = byId.order().get(Number(query))
    if (order && order.active) {
      const material = byId.material().get(order.material_one_id)
      hits.push({
        type: 'order',
        id: order.order_id,
        label: `Order ${order.order_id}`,
        sublabel: [
          byId.orderType().get(order.order_type_id)?.code,
          byId.plant().get(order.plant_id)?.code,
          byId.status().get(order.status_id)?.name,
          `${material?.number ?? ''} ${material?.description ?? ''}`.trim(),
        ].filter(Boolean).join(' · '),
        route: `orders/${order.order_id}`,
      })
    }
  }

  for (const record of store.qc) {
    if (!record.active || String(record.sample_number).toUpperCase() !== upper) continue
    hits.push({
      type: 'sample',
      id: record.sample_number,
      label: `Sample ${record.sample_number}`,
      sublabel: `QC ${record.qc_id} on order ${record.order_id} · ${record.test_date}`,
      route: `orders/${record.order_id}`,
      order_id: record.order_id,
    })
    if (hits.length >= 3) break
  }
  if (!hits.some((hit) => hit.type === 'sample')) {
    const known = store.lims_result.find((row) => String(row.sample_code).toUpperCase() === upper)
    if (known) {
      hits.push({
        type: 'sample',
        id: known.sample_code,
        label: `Sample ${known.sample_code}`,
        sublabel: 'Has LIMS results but no QC record in PIMS',
        route: 'inquiry/qc',
      })
    }
  }

  for (const txn of store.inventory_transaction) {
    if (txn.voided) continue
    const bol = String(txn.to_bol || '').toUpperCase() === upper
      ? txn.to_bol
      : String(txn.from_bol || '').toUpperCase() === upper ? txn.from_bol : null
    if (!bol) continue
    hits.push({
      type: 'bol',
      id: txn.transaction_id,
      label: `BOL ${bol}`,
      sublabel: `${byId.transactionType().get(txn.transaction_type_id)?.code} on order ${txn.order_id}`,
      route: `orders/${txn.order_id}`,
      order_id: txn.order_id,
    })
    if (hits.filter((hit) => hit.type === 'bol').length >= 3) break
  }

  for (const location of store.location) {
    if (!location.active || String(location.number).toUpperCase() !== upper) continue
    if (plantId && location.plant_id !== Number(plantId)) continue
    hits.push({
      type: 'location',
      id: location.location_id,
      label: location.number,
      sublabel: `${location.description} · ${byId.plant().get(location.plant_id)?.code}`,
      route: 'inventory',
      location_id: location.location_id,
    })
  }

  for (const material of store.material) {
    if (!material.active || String(material.number).toUpperCase() !== upper) continue
    hits.push({
      type: 'material',
      id: material.material_id,
      label: `${material.number} ${material.description}`,
      sublabel: `Product · ${material.family || 'no family'}`,
      route: 'specs',
      material_id: material.material_id,
    })
  }

  for (const stage of store.pending_shipment) {
    if (stage.shipped || String(stage.trailer_number).trim() !== query) continue
    const order = byId.order().get(stage.order_id)
    hits.push({
      type: 'trailer',
      id: stage.stage_id,
      label: `Trailer ${stage.trailer_number}`,
      sublabel: `Loaded and waiting to ship · order ${stage.order_id}`
        + (order?.customer_id ? ` · ${byId.customer().get(order.customer_id)?.name}` : ''),
      route: 'operations/ship',
      stage_id: stage.stage_id,
      order_id: stage.order_id,
    })
    if (hits.filter((hit) => hit.type === 'trailer').length >= 3) break
  }

  return { query, hits: hits.slice(0, 8) }
}

/* ------------------------------------------------------------------ scale */

const WEIGHT_LABEL_FIRST = /\b(GROSS|TARE|NET|G|T|N)\b\s*[:=]?\s*(-?\d+(?:\.\d+)?)/gi
const WEIGHT_LABEL_LAST = /(-?\d+(?:\.\d+)?)\s*(?:lbs?|LB)?\s*\b(GROSS|TARE|NET|G|T|N)\b/gi

export function parseIndicatorLine(line: string): Row {
  const found: Row = { gross_lbs: null, tare_lbs: null, net_lbs: null }
  const text = (line || '').replace(/,/g, '')
  const field = (kind: string) =>
    ({ G: 'gross_lbs', T: 'tare_lbs', N: 'net_lbs' } as Record<string, string>)[kind[0].toUpperCase()]

  for (const pattern of [WEIGHT_LABEL_FIRST, WEIGHT_LABEL_LAST]) {
    pattern.lastIndex = 0
    let match = pattern.exec(text)
    while (match) {
      const [value, kind] = pattern === WEIGHT_LABEL_FIRST
        ? [match[2], match[1]]
        : [match[1], match[2]]
      const key = field(kind)
      if (found[key] === null) found[key] = Number(value)
      match = pattern.exec(text)
    }
    if (Object.values(found).some((value) => value !== null)) break
  }
  if (Object.values(found).every((value) => value === null)) {
    const bare = /-?\d+(?:\.\d+)?/.exec(text)
    if (bare) found.gross_lbs = Number(bare[0])
  }
  if (found.net_lbs === null && found.gross_lbs !== null && found.tare_lbs !== null) {
    found.net_lbs = found.gross_lbs - found.tare_lbs
  }
  return found
}

export function recordScaleReading(payload: Row): Row {
  const weights = payload.line
    ? parseIndicatorLine(String(payload.line))
    : {
        gross_lbs: payload.gross_lbs ?? null,
        tare_lbs: payload.tare_lbs ?? null,
        net_lbs: payload.net_lbs ?? null,
      }
  const row: Row = {
    reading_id: nextId('scale_reading', 'reading_id'),
    plant_id: Number(payload.plant_id),
    scale_id: payload.scale_id ?? '',
    trailer_number: String(payload.trailer_number ?? '').trim(),
    ...weights,
    captured_at: payload.captured_at ?? nowIso(),
    received_at: nowIso(),
    source: payload.source ?? 'sandbox',
    consumed_by: null,
  }
  store.scale_reading.push(row)
  return row
}

export function latestScaleReading(plantId: number, trailerNumber?: string | null): Row | null {
  const cutoff = Date.now() - 120 * 60_000
  const matches = store.scale_reading.filter((row) => {
    if (row.plant_id !== Number(plantId) || row.consumed_by) return false
    if (new Date(row.captured_at).getTime() < cutoff) return false
    if (trailerNumber) {
      const trailer = String(row.trailer_number || '').trim()
      return trailer === String(trailerNumber).trim() || trailer === ''
    }
    return true
  })
  return matches.sort((a, b) => b.reading_id - a.reading_id)[0] ?? null
}

export function consumeScaleReading(readingId: number, transactionId: number): void {
  const row = store.scale_reading.find((item) => item.reading_id === Number(readingId))
  if (row) row.consumed_by = transactionId
}

/** The sandbox has no plant agent, so it can mint a plausible weigh-out. */
export function simulateScaleReading(plantId: number, trailerNumber?: string): Row {
  const tare = 14_000 + Math.round(Math.random() * 2_400 / 20) * 20
  const gross = tare + 20_000 + Math.round(Math.random() * 26_000 / 20) * 20
  return recordScaleReading({
    plant_id: plantId,
    trailer_number: trailerNumber ?? '',
    scale_id: 'sandbox-scale',
    line: `GROSS ${gross} LB TARE ${tare} LB`,
    source: 'sandbox:simulated',
  })
}

/* ----------------------------------------------------------------- alerts */

const SEVERITY_ORDER: Record<string, number> = { info: 0, warning: 1, critical: 2 }

export function evaluateAlerts(
  plantId: number | null,
  limsFreshness: () => Row,
  outOfSpec: (plantId: number | null, days: number, limit: number) => Row[],
  dataQuality: (plantId: number | null) => Row,
  balances: (filters: Row) => Row[],
): Row[] {
  const found: Row[] = []

  const freshness = limsFreshness()
  if (freshness.status !== 'ok') {
    found.push({
      rule: 'lims_stale',
      severity: freshness.status === 'failed' ? 'critical' : 'warning',
      subject: `LIMS feed ${freshness.status}`,
      body: `${freshness.detail} Lab results shown in PIMS may be out of date.`,
      fingerprint: `lims_stale:${freshness.status}`,
      entity: 'lims',
      entity_id: freshness.expected_source ?? '',
      plant_id: null,
    })
  }

  for (const record of outOfSpec(plantId, 2, 50)) {
    found.push({
      rule: 'out_of_spec',
      severity: 'warning',
      subject: `Out of spec: ${record.material_number} on order ${record.order_id} (${record.plant_code})`,
      body: record.evaluations
        .map((evaluation: Row) => `${evaluation.label} ${evaluation.value}`).join(', '),
      fingerprint: `out_of_spec:${record.qc_id}`,
      entity: 'qc',
      entity_id: String(record.qc_id),
      plant_id: record.plant_id,
    })
  }

  const stale = dataQuality(plantId).findings.find((finding: Row) => finding.key === 'stale_loads')
  for (const row of stale?.rows ?? []) {
    const age = hoursSince(row.loaded_at) ?? 0
    found.push({
      rule: 'stale_load',
      severity: 'warning',
      subject: `Trailer ${row.trailer_number} loaded ${Math.round(age / 24)} days ago and not shipped (${row.plant_code})`,
      body: `Order ${row.order_id}, ${Math.round(row.quantity).toLocaleString()} lbs staged since ${row.loaded_at}.`,
      fingerprint: `stale_load:${row.stage_id}`,
      entity: 'pending_shipment',
      entity_id: String(row.stage_id),
      plant_id: plantId,
    })
  }

  for (const row of balances({ plant_id: plantId })) {
    if (row.location_type !== 'Tank' || !row.max_capacity) continue
    if ((row.percent_full ?? 0) < 95) continue
    found.push({
      rule: 'tank_full',
      severity: 'warning',
      subject: `${row.location_number} is ${Math.round(row.percent_full)}% full (${row.plant_code})`,
      body: `${Math.round(row.balance).toLocaleString()} lbs of ${row.material_number} against a ${Math.round(row.max_capacity).toLocaleString()} lb capacity.`,
      fingerprint: `tank_full:${row.location_id}:${row.material_id}`,
      entity: 'location',
      entity_id: String(row.location_id),
      plant_id: row.plant_id,
    })
  }

  const needsReview = store.material_spec.filter((spec) => spec.needs_review && spec.active).length
  if (needsReview) {
    found.push({
      rule: 'product_setup',
      severity: 'info',
      subject: 'Product setup needs attention',
      body: `${needsReview} limit(s) flagged for confirmation. See Products & limits.`,
      fingerprint: `product_setup:${needsReview}`,
      entity: 'material_spec',
      entity_id: '',
      plant_id: null,
    })
  }

  return found
}

export function runAlerts(found: Row[], send: boolean): Row {
  const repeatHours = Number(setting('alerts.repeat_hours', '24'))
  const floor = SEVERITY_ORDER[setting('alerts.min_severity', 'warning')] ?? 1
  const webhook = setting('alerts.webhook_url', '')
  const fresh: Row[] = []
  let suppressed = 0

  for (const alert of found) {
    const previous = store.alert_log
      .filter((row) => row.fingerprint === alert.fingerprint)
      .sort((a, b) => b.alert_id - a.alert_id)[0]
    const age = previous ? hoursSince(previous.created_at) : null
    if (previous && age !== null && age < repeatHours) { suppressed += 1; continue }
    if (send) {
      store.alert_log.push({
        alert_id: nextId('alert_log', 'alert_id'),
        created_at: nowIso(),
        ...alert,
        // The sandbox cannot reach a webhook host, so delivery is recorded
        // as "would have sent" rather than pretending it succeeded.
        channel: webhook && SEVERITY_ORDER[alert.severity] >= floor ? 'webhook' : '',
        delivered: 0,
        error: webhook ? 'sandbox: outbound webhooks are blocked' : '',
        acknowledged_at: null,
        acknowledged_by: null,
      })
    }
    fresh.push(alert)
  }

  return {
    evaluated: found.length,
    new: fresh,
    suppressed,
    delivered: 0,
    webhook_configured: Boolean(webhook),
    min_severity: setting('alerts.min_severity', 'warning'),
    dry_run: !send,
  }
}

export function alertSettings(): Row {
  return {
    webhook_configured: Boolean(setting('alerts.webhook_url', '')),
    min_severity: setting('alerts.min_severity', 'warning'),
    repeat_hours: setting('alerts.repeat_hours', '24'),
    auto_close: setting('autoclose.enabled', 'true') === 'true',
    sample_auto_generate: setting('sample.auto_generate', 'false') === 'true',
  }
}

/* ------------------------------------------------------------------- jobs */

export function recordJobRun(job: string, detail: Row, status = 'ok', error = ''): Row {
  const row = {
    run_id: nextId('job_run', 'run_id'),
    job,
    started_at: nowIso(),
    finished_at: nowIso(),
    status,
    detail: JSON.stringify(detail),
    error,
  }
  store.job_run.push(row)
  return row
}

export function jobHealth(): Row {
  const summary: Row = {}
  for (const run of store.job_run) {
    const current = summary[run.job]
    if (!current || run.started_at > current.last_started) {
      summary[run.job] = {
        last_started: run.started_at,
        last_status: run.status,
        hours_ago: Math.round((hoursSince(run.started_at) ?? 0) * 10) / 10,
      }
    }
  }
  return summary
}
