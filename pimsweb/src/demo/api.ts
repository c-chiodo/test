/* The PIMS rules, re-implemented in the page for the browser-only demo build.
 *
 * This mirrors `pims/services/` closely enough that the UI behaves the same —
 * stock and capacity checks refuse impossible movements, QC validation asks the
 * *material* what it is tested for, voids write reversing entries, the query
 * builder only reaches whitelisted fields — but it is a sandbox, not the
 * system. The server implementation in `pims/` is the one under test and the
 * one that would run in a plant.
 *
 * Every handler returns the same JSON shape as the FastAPI app, and failures
 * throw the same error body, so `lib/api.ts` cannot tell the difference. */

import {
  Row, byId, hoursSince, nextId, nowIso, store, todayIso,
} from './store'
import {
  alertSettings, consumeScaleReading, evaluateAlerts, jobHealth, latestScaleReading,
  nextBol, nextSampleNumber, prefillOperation, prefillQc, recordJobRun, recordScaleReading,
  resolveScan, runAlerts, setting, simulateScaleReading, trailerHistory,
} from './automation'
import { reportTable, runReport } from './reports'

/* ----------------------------------------------------------------- errors */

interface ErrorBody {
  code: string
  message: string
  detail?: Record<string, unknown>
  status: number
}

function fail(status: number, code: string, message: string, detail: Record<string, unknown> = {}): never {
  const body: ErrorBody = { status, code, message, detail }
  throw body
}

const notFound = (message: string): never => fail(404, 'not_found', message)
const invalid = (message: string, fields: Record<string, string>): never =>
  fail(422, 'validation_error', message, { fields })
const refuse = (message: string, detail: Record<string, unknown> = {}): never =>
  fail(409, 'business_rule', message, detail)

/* -------------------------------------------------------------- security */

const ROLE_PERMISSIONS: Record<string, string[]> = {
  operator: ['order.read', 'txn.read', 'txn.post', 'qc.read', 'query.run'],
  qc: ['order.read', 'txn.read', 'qc.read', 'qc.write', 'query.run', 'spec.read'],
  supervisor: [
    'order.read', 'order.write', 'order.close', 'txn.read', 'txn.post', 'txn.void',
    'qc.read', 'qc.write', 'query.run', 'spec.read', 'spec.write', 'support.read',
  ],
  admin: ['*'],
}

let session: Row | null = null

function publicUser(user: Row): Row {
  return {
    user_id: user.user_id,
    username: user.username,
    full_name: user.full_name,
    email: user.email,
    role: user.role,
    plants: store.user_plant_access
      .filter((access) => access.user_id === user.user_id)
      .map((access) => byId.plant().get(access.plant_id))
      .filter((plant): plant is Row => Boolean(plant))
      .map((plant) => ({ plant_id: plant.plant_id, code: plant.code, name: plant.name }))
      .sort((a, b) => String(a.code).localeCompare(String(b.code))),
    permissions: [...(ROLE_PERMISSIONS[user.role] ?? [])].sort(),
  }
}

function requireUser(): Row {
  if (!session) fail(401, 'unauthenticated', 'Sign in to continue.')
  return session as Row
}

function can(user: Row, permission: string): boolean {
  const granted = ROLE_PERMISSIONS[user.role] ?? []
  return granted.includes('*') || granted.includes(permission)
}

/** Plain-language names for permissions, and who to ask. */
const PERMISSION_HELP: Record<string, [string, string]> = {
  'txn.void': ['void a transaction', 'a supervisor'],
  'order.write': ['edit an order', 'a supervisor'],
  'order.close': ['close an order', 'a supervisor'],
  'qc.write': ['record QC', 'QC or a supervisor'],
  'spec.write': ['change product limits', 'an administrator'],
  'support.read': ['open the support console', 'a supervisor'],
}

function hasPermission(user: Row, permission: string): boolean {
  return can(user, permission)
}

function requirePermission(user: Row, permission: string): void {
  if (can(user, permission)) return
  // A refusal that only quotes the permission string leaves an operator stuck.
  const [action, ask] = PERMISSION_HELP[permission] ?? [permission.replace('.', ' '), 'a supervisor']
  fail(403, 'forbidden',
    `Your role (${user.role}) cannot ${action}. Ask ${ask} to do it.`, { permission, ask, action })
}

function requirePlant(user: Row, plantId: number): void {
  if (user.role === 'admin') return
  const allowed = store.user_plant_access.filter((a) => a.user_id === user.user_id)
  if (!allowed.some((a) => a.plant_id === Number(plantId))) {
    fail(403, 'forbidden', 'You do not have access to that plant.', { plant_id: plantId })
  }
}

/* ----------------------------------------------------------------- audit */

/** `orderId` is the order this change belongs to, whatever entity it was
 *  recorded against — a transaction and a QC record are events in an order's
 *  life, and the order's history screen is where a supervisor goes looking. */
function audit(
  action: string, entity: string, entityId: any, summary: string,
  detail: Row = {}, orderId: number | null = null,
): void {
  store.audit_log.push({
    audit_id: store.audit_log.length + 1,
    occurred_at: nowIso(),
    username: session?.username ?? 'system',
    action, entity, entity_id: String(entityId), order_id: orderId ?? null,
    summary, detail,
  })
}

function diff(before: Row, after: Row): Row {
  const changes: Row = {}
  for (const key of Object.keys(after)) {
    if (before[key] !== after[key]) changes[key] = { from: before[key] ?? null, to: after[key] ?? null }
  }
  return changes
}

/* ------------------------------------------------------------ specs / QC */

const ANALYTE_LABELS: Record<string, string> = {
  moisture: 'Moisture', temp: 'Temperature', ph: 'pH', ffa: 'FFA', tfa: 'TFA',
  spintest: 'Spintest fallout', flash: 'Flash', impurities: 'Impurities',
  unsaponifiables: 'Unsaponifiables', linoleic: 'Linoleic acid', stearic: 'Stearic acid',
}
const ANALYTE_UNITS: Record<string, string> = {
  moisture: '%', temp: '°F', ph: '', ffa: '%', tfa: '%', spintest: 'mils', flash: 'p/f',
  impurities: '%', unsaponifiables: '%', linoleic: '%', stearic: '%',
}
const FIELD_ANALYTE: Record<string, string> = {
  moisture: 'moisture', temp: 'temp', ph: 'ph', ffa: 'ffa', tfa: 'tfa',
  spintest_fallout: 'spintest',
}
const ANALYTE_FIELD: Record<string, string> = Object.fromEntries(
  Object.entries(FIELD_ANALYTE).map(([field, analyte]) => [analyte, field]),
)

function requiredTests(materialId: number | null): string[] {
  if (!materialId) return []
  return store.material_test
    .filter((row) => row.material_id === materialId && row.required)
    .map((row) => row.analyte)
    .sort()
}

function specsFor(materialId: number | null): Map<string, Row> {
  const map = new Map<string, Row>()
  if (!materialId) return map
  for (const row of store.material_spec) {
    if (row.material_id === materialId && row.active) map.set(row.analyte, row)
  }
  return map
}

function num(value: any): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isNaN(parsed) ? null : parsed
}

function evaluate(materialId: number | null, values: Record<string, any>): Row[] {
  const specs = specsFor(materialId)
  const tests = new Set(requiredTests(materialId))
  const analytes = new Set<string>([
    ...specs.keys(), ...tests,
    ...Object.entries(values).filter(([, v]) => num(v) !== null).map(([k]) => k),
  ])
  return [...analytes].sort().map((analyte) => {
    const value = num(values[analyte])
    const spec = specs.get(analyte)
    const lo = spec ? spec.min_value : null
    const hi = spec ? spec.max_value : null
    let verdict: string
    if (value === null) verdict = tests.has(analyte) ? 'missing' : 'not_run'
    else if (!spec) verdict = 'no_spec'
    else if ((lo !== null && value < lo) || (hi !== null && value > hi)) verdict = 'out_of_spec'
    else verdict = 'in_spec'
    return {
      analyte,
      label: ANALYTE_LABELS[analyte] ?? analyte,
      unit: ANALYTE_UNITS[analyte] ?? '',
      value, min_value: lo, max_value: hi,
      required: tests.has(analyte),
      verdict,
      needs_review: spec ? Boolean(spec.needs_review) : false,
      note: spec ? spec.note : '',
    }
  })
}

function summarize(evaluations: Row[]): Row {
  const out = evaluations.filter((e) => e.verdict === 'out_of_spec').map((e) => e.analyte)
  const missing = evaluations.filter((e) => e.verdict === 'missing').map((e) => e.analyte)
  return {
    out_of_spec: out,
    missing_required: missing,
    status: out.length ? 'out_of_spec' : missing.length ? 'incomplete' : 'in_spec',
  }
}

function qcValues(record: Row): Record<string, any> {
  const values: Record<string, any> = {}
  for (const [field, analyte] of Object.entries(FIELD_ANALYTE)) values[analyte] = num(record[field])
  return values
}

/* ------------------------------------------------------------- inventory */

function ledgerBalances(asOf?: string | null): Map<string, number> {
  const cutoff = asOf ? new Date(String(asOf).replace(' ', 'T')).getTime() : null
  const totals = new Map<string, number>()
  const add = (location: number, material: number, qty: number) => {
    const key = `${location}:${material}`
    totals.set(key, (totals.get(key) ?? 0) + qty)
  }
  for (const txn of store.inventory_transaction) {
    if (cutoff !== null) {
      const when = new Date(String(txn.transaction_date).replace(' ', 'T')).getTime()
      if (Number.isNaN(when) || when > cutoff) continue
    }
    if (txn.to_location_id && txn.to_qty) add(txn.to_location_id, txn.to_material_id, txn.to_qty)
    if (txn.from_location_id && txn.from_qty) add(txn.from_location_id, txn.from_material_id, -txn.from_qty)
  }
  return totals
}

function balances(filters: {
  plant_id?: number | null
  location_id?: number | null
  material_id?: number | null
  as_of?: string | null
  include_zero?: boolean
} = {}): Row[] {
  const totals = ledgerBalances(filters.as_of)
  const locations = byId.location()
  const materials = byId.material()
  const plants = byId.plant()
  const locationTypes = new Map(store.location_type.map((t) => [t.location_type_id, t.name]))

  const rows: Row[] = []
  for (const [key, raw] of totals) {
    const [locationId, materialId] = key.split(':').map(Number)
    const location = locations.get(locationId)
    const material = materials.get(materialId)
    if (!location || !material) continue
    const balance = Math.round(raw * 100) / 100
    if (!filters.include_zero && balance === 0) continue
    if (filters.plant_id && location.plant_id !== Number(filters.plant_id)) continue
    if (filters.location_id && locationId !== Number(filters.location_id)) continue
    if (filters.material_id && materialId !== Number(filters.material_id)) continue
    rows.push({
      location_id: locationId,
      location_number: location.number,
      location_description: location.description,
      location_type: locationTypes.get(location.location_type_id),
      max_capacity: location.max_capacity,
      plant_id: location.plant_id,
      plant_code: plants.get(location.plant_id)?.code,
      material_id: materialId,
      material_number: material.number,
      material_description: material.description,
      balance,
      percent_full: location.max_capacity
        ? Math.round((balance / location.max_capacity) * 1000) / 10
        : null,
    })
  }
  rows.sort((a, b) =>
    (a.plant_code ?? '').localeCompare(b.plant_code ?? '') ||
    a.location_number.localeCompare(b.location_number) ||
    a.material_number.localeCompare(b.material_number))
  return rows
}

function balanceOf(locationId: number, materialId: number): number {
  return ledgerBalances().get(`${locationId}:${materialId}`) ?? 0
}

function locationTotal(locationId: number): number {
  let total = 0
  for (const [key, value] of ledgerBalances()) {
    if (Number(key.split(':')[0]) === locationId) total += value
  }
  return total
}

const OPERATIONS: Record<string, { from: boolean; to: boolean; label: string }> = {
  RECEIVE: { from: false, to: true, label: 'Receive' },
  PRODUCE: { from: true, to: true, label: 'Produce' },
  MOVE: { from: true, to: true, label: 'Move' },
  LOAD: { from: true, to: true, label: 'Load trailer' },
  SHIP: { from: true, to: false, label: 'Ship trailer' },
  SHRINK: { from: true, to: false, label: 'Shrinkage' },
  ADJUST: { from: false, to: true, label: 'Adjustment' },
  // A component pumped from its tank onto the trailer as the ordered blend.
  PROD_LOAD: { from: true, to: true, label: 'Blend onto trailer' },
}

/** What a transaction type is, whatever it is called (MOVE-LOAD is a load). */
function kindOf(typeId: number): string | undefined {
  const t = byId.transactionType().get(typeId)
  return t ? (t.kind || t.code) : undefined
}

function hydrateTransaction(txn: Row): Row {
  const materials = byId.material()
  const locations = byId.location()
  return {
    ...txn,
    transaction_type: byId.transactionType().get(txn.transaction_type_id)?.code,
    transaction_description: byId.transactionType().get(txn.transaction_type_id)?.description,
    plant_code: byId.plant().get(txn.plant_id)?.code,
    username: byId.user().get(txn.user_id)?.username ?? 'system',
    full_name: byId.user().get(txn.user_id)?.full_name ?? 'system',
    department_code: byId.department().get(txn.department_id)?.code ?? null,
    from_material_number: materials.get(txn.from_material_id)?.number ?? null,
    from_material_description: materials.get(txn.from_material_id)?.description ?? null,
    to_material_number: materials.get(txn.to_material_id)?.number ?? null,
    to_material_description: materials.get(txn.to_material_id)?.description ?? null,
    from_location_number: locations.get(txn.from_location_id)?.number ?? null,
    to_location_number: locations.get(txn.to_location_id)?.number ?? null,
  }
}

function postTransaction(operation: string, payload: Row): Row {
  /* eslint-disable no-param-reassign */
  const user = requireUser()
  requirePermission(user, 'txn.post')
  const op = operation.toUpperCase()
  const spec = OPERATIONS[op]
  if (!spec) return invalid(`${op} is not an inventory operation.`, { operation: 'Unknown operation.' })

  // A retry after a lost answer carries the key of the attempt it is retrying.
  const idempotencyKey = String(payload.idempotency_key ?? '').trim()
  if (idempotencyKey) {
    const already = store.inventory_transaction.find((t) => t.idempotency_key === idempotencyKey)
    if (already) return hydrateTransaction(already)
  }

  const fields: Record<string, string> = {}
  let plantId = payload.plant_id ? Number(payload.plant_id) : null
  const orderId = payload.order_id ? Number(payload.order_id) : null
  let order: Row | undefined

  if (orderId) {
    order = byId.order().get(orderId)
    if (!order) return notFound(`Order ${orderId} was not found.`)
    const status = byId.status().get(order.status_id)
    if (status?.is_terminal) {
      refuse(`Order ${orderId} is ${status.name}; reopen it before posting.`, { order_id: orderId })
    }
    plantId = plantId ?? order.plant_id
  }
  if (!plantId) fields.plant_id = 'Choose a plant.'
  else requirePlant(user, plantId)

  const fromQty = num(payload.from_qty) ?? 0
  let toQty = num(payload.to_qty) ?? 0
  const fromLocationId = payload.from_location_id ? Number(payload.from_location_id) : null
  const toLocationId = payload.to_location_id ? Number(payload.to_location_id) : null
  const fromMaterialId = payload.from_material_id ? Number(payload.from_material_id) : null
  const toMaterialId = payload.to_material_id ? Number(payload.to_material_id) : null

  if (spec.from) {
    if (!fromLocationId) fields.from_location_id = 'Choose a from location.'
    if (!fromMaterialId) fields.from_material_id = 'Choose the material being taken.'
    if (fromQty <= 0) fields.from_qty = 'Enter a quantity greater than zero.'
  }
  if (spec.to) {
    if (!toLocationId) fields.to_location_id = 'Choose a to location.'
    if (!toMaterialId) fields.to_material_id = 'Choose the material being put away.'
    if (toQty <= 0 && op !== 'PRODUCE') toQty = toQty || fromQty
    if (toQty <= 0) fields.to_qty = 'Enter a quantity greater than zero.'
  }
  if (op === 'MOVE' && fromLocationId && fromLocationId === toLocationId) {
    fields.to_location_id = 'From and to locations must differ.'
  }
  // Move and load relocate product; only Produce may change what it is or how
  // much there is. Mirrors `pims/services/inventory.py`.
  if ((op === 'MOVE' || op === 'LOAD') && Object.keys(fields).length === 0) {
    if (fromMaterialId !== toMaterialId) {
      fields.to_material_id = `A ${spec.label.toLowerCase()} moves product, it cannot change it. `
        + 'Take out and put in the same product.'
    } else if (Math.abs(fromQty - toQty) > 0.01) {
      fields.to_qty = `${Math.round(fromQty).toLocaleString()} lbs out but `
        + `${Math.round(toQty).toLocaleString()} lbs in. A move cannot create or lose `
        + 'product — use Shrinkage or an Adjustment for that.'
    }
  }
  if ((op === 'ADJUST' || op === 'SHRINK') && !String(payload.remarks ?? '').trim()) {
    fields.remarks = 'Say why the count is being changed.'
  }
  if (Object.keys(fields).length) invalid('This transaction cannot be posted.', fields)

  const locations = byId.location()
  for (const [key, locationId] of [['from_location_id', fromLocationId], ['to_location_id', toLocationId]] as const) {
    if (!locationId) continue
    const location = locations.get(locationId)
    if (!location) return notFound(`Location ${locationId} does not exist.`)
    if (location.plant_id !== plantId) {
      invalid(`${location.number} belongs to another plant.`, { [key]: 'Location is not at this plant.' })
    }
    if (location.bol_required && key === 'to_location_id' && !payload.to_bol && (op === 'RECEIVE' || op === 'LOAD' || op === 'PROD_LOAD')) {
      if (setting('bol.auto_generate', 'true') !== 'false') {
        payload = { ...payload, to_bol: nextBol(payload.trailer_number) }
      } else {
        invalid(`${location.number} requires a BOL number.`, { to_bol: 'Enter the BOL number.' })
      }
    }
  }

  if (op === 'LOAD' && order && order.material_one_id) {
    // A load must be of the product the order is for. Fulfilment used to count
    // any material, so loading the wrong tank both put the wrong feed on the
    // truck and marked the order as progressing.
    if (fromMaterialId && fromMaterialId !== order.material_one_id) {
      const wanted = byId.material().get(order.material_one_id)
      const loading = byId.material().get(fromMaterialId)
      refuse(
        `Order ${order.order_id} is for ${wanted?.number} ${wanted?.description}, but this `
        + `load is ${loading?.number} ${loading?.description}. Check the tank, or post this `
        + 'against the right order.',
        { order_id: order.order_id, expected: wanted?.number, loading: loading?.number },
      )
    }
    // Loading past the ordered quantity is possible, but never by accident.
    if (!payload.acknowledge_over_load) {
      const ordered = order.material_one_quantity || 0
      const already = progress(order).qty_fulfilled
      const over = already + fromQty - ordered
      if (ordered && over > 0.01) {
        refuse(
          `Order ${order.order_id} is for ${Math.round(ordered).toLocaleString()} lbs and `
          + `${Math.round(already).toLocaleString()} lbs are already loaded. This load puts it `
          + `${Math.round(over).toLocaleString()} lbs over. Confirm to post it anyway, or check `
          + 'whether this belongs on another order.',
          {
            rule: 'over_fulfilment',
            order_id: order.order_id,
            ordered: Math.round(ordered),
            already_loaded: Math.round(already),
            over_by: Math.round(over),
            acknowledge_field: 'acknowledge_over_load',
          },
        )
      }
    }
  }

  // Steam and city water come from a utility: no stock to overdraw.
  const fromUtility = fromLocationId
    && store.location_type.find((t) => t.location_type_id === locations.get(fromLocationId)?.location_type_id)?.name === 'Utility'
  if (spec.from && fromLocationId && fromMaterialId && !fromUtility) {
    const available = balanceOf(fromLocationId, fromMaterialId)
    if (fromQty - available > 0.01) {
      const location = locations.get(fromLocationId)!
      const material = byId.material().get(fromMaterialId)!
      refuse(
        `${location.number} holds ${Math.round(available).toLocaleString()} lbs of ${material.number} — ` +
        `cannot take ${Math.round(fromQty).toLocaleString()} lbs.`,
        {
          available: Math.round(available * 100) / 100,
          requested: Math.round(fromQty * 100) / 100,
          location: location.number,
          material: material.number,
        },
      )
    }
  }

  if (spec.to && toLocationId) {
    const location = locations.get(toLocationId)!
    if (location.max_capacity) {
      const current = locationTotal(toLocationId)
      if (current + toQty - location.max_capacity > 0.01) {
        refuse(
          `${location.number} holds ${Math.round(current).toLocaleString()} lbs of ` +
          `${Math.round(location.max_capacity).toLocaleString()} lbs capacity — ` +
          `${Math.round(toQty).toLocaleString()} lbs will not fit.`,
          {
            capacity: location.max_capacity,
            current: Math.round(current * 100) / 100,
            requested: Math.round(toQty * 100) / 100,
            location: location.number,
          },
        )
      }
    }
  }

  const transactionId = nextId('inventory_transaction', 'transaction_id')
  const row: Row = {
    transaction_id: transactionId,
    parent_transaction_id: payload.parent_transaction_id ?? null,
    transaction_type_id: store.transaction_type.find((t) => t.code === op)!.transaction_type_id,
    order_id: orderId,
    plant_id: plantId,
    department_id: payload.department_id ?? order?.department_id ?? null,
    transaction_date: nowIso(),
    user_date: String(payload.user_date || todayIso()).slice(0, 10),
    user_id: user.user_id,
    from_material_id: spec.from ? fromMaterialId : null,
    from_location_id: spec.from ? fromLocationId : null,
    from_qty: spec.from ? Math.round(fromQty * 100) / 100 : 0,
    from_bol: payload.from_bol || '',
    to_material_id: spec.to ? toMaterialId : null,
    to_location_id: spec.to ? toLocationId : null,
    to_qty: spec.to ? Math.round(toQty * 100) / 100 : 0,
    to_bol: payload.to_bol || '',
    trailer_number: payload.trailer_number || '',
    tank_hours: num(payload.tank_hours),
    employee_hours: num(payload.employee_hours),
    remarks: payload.remarks || '',
    voided: 0,
    is_reversal: payload.is_reversal ? 1 : 0,
    idempotency_key: idempotencyKey || null,
  }
  store.inventory_transaction.push(row)

  if (payload.scale_reading_id) {
    consumeScaleReading(Number(payload.scale_reading_id), transactionId)
  }
  if (op === 'LOAD') {
    store.pending_shipment.push({
      stage_id: nextId('pending_shipment', 'stage_id'),
      order_id: orderId,
      transaction_id: transactionId,
      trailer_number: row.trailer_number,
      quantity: row.from_qty,
      shipped: 0,
      cancelled: 0,
    })
  }
  if (order && order.status_id === 1) order.status_id = 2

  audit(`post.${op.toLowerCase()}`, 'transaction', transactionId,
    `${spec.label} ${Math.round(fromQty || toQty).toLocaleString()} lbs` +
    (orderId ? ` on order ${orderId}` : ''), { values: row }, orderId)

  return hydrateTransaction(row)
}

/** How long an operator has to reverse their own posting. */
const SELF_VOID_HOURS = 12

/** Whether this user may reverse this row, and why not if they cannot. */
export function selfVoidCheck(txn: Row, user: Row): { allowed: boolean; why: string } {
  if (txn.user_id !== user.user_id) return { allowed: false, why: 'It was posted by someone else.' }
  if (txn.voided || txn.is_reversal) return { allowed: false, why: 'It has already been reversed.' }
  const age = hoursSince(txn.transaction_date)
  if (age === null || age > SELF_VOID_HOURS) {
    return { allowed: false, why: `It is more than ${SELF_VOID_HOURS} hours old.` }
  }
  const code = byId.transactionType().get(txn.transaction_type_id)?.code
  if (code === 'SHIP') return { allowed: false, why: 'The trailer has already left.' }
  if (code === 'LOAD') {
    const stage = store.pending_shipment.find((s) => s.transaction_id === txn.transaction_id)
    if (stage?.shipped) return { allowed: false, why: 'The trailer has already shipped.' }
  }
  return { allowed: true, why: '' }
}

function voidTransaction(transactionId: number, reason: string): Row {
  const user = requireUser()
  const original = store.inventory_transaction.find((t) => t.transaction_id === transactionId)
  if (!original) return notFound(`Transaction ${transactionId} was not found.`)
  // An operator may undo their own recent, unshipped posting. Needing a
  // supervisor for every slip is what turns a thirty-second correction into a
  // phone call, and a phone call into a load that never gets corrected.
  if (!hasPermission(user, 'txn.void') && !selfVoidCheck(original, user).allowed) {
    requirePermission(user, 'txn.void')
  }
  if (original.voided) refuse(`Transaction ${transactionId} is already voided.`, { transaction_id: transactionId })
  if (!reason.trim()) {
    invalid('A reason is required to void a transaction.', { reason: 'Explain why this is being reversed.' })
  }
  requirePlant(user, original.plant_id)

  const reversalId = nextId('inventory_transaction', 'transaction_id')
  const reversal: Row = {
    ...original,
    transaction_id: reversalId,
    parent_transaction_id: transactionId,
    transaction_date: nowIso(),
    user_date: todayIso(),
    user_id: user.user_id,
    from_material_id: original.to_material_id,
    from_location_id: original.to_location_id,
    from_qty: original.to_qty,
    to_material_id: original.from_material_id,
    to_location_id: original.from_location_id,
    to_qty: original.from_qty,
    remarks: `Reversal of transaction ${transactionId}: ${reason.trim()}`,
    voided: 0,
    is_reversal: 1,
  }
  store.inventory_transaction.push(reversal)
  original.voided = 1
  reversal.idempotency_key = null
  // Voiding a load cancels the stage; voiding a ship puts the trailer back on
  // the dock. Overloading `shipped` for both meant a cancelled load counted as
  // a shipment and a reversed shipment could never be re-shipped.
  const code = byId.transactionType().get(original.transaction_type_id)?.code
  for (const stage of store.pending_shipment) {
    if (code === 'LOAD' && stage.transaction_id === transactionId) stage.cancelled = 1
    if (code === 'SHIP' && stage.transaction_id === original.parent_transaction_id
        && !stage.cancelled) stage.shipped = 0
  }
  audit('void', 'transaction', transactionId, `Voided transaction ${transactionId}`,
    { reason, reversal_id: reversalId }, original.order_id ?? null)
  return hydrateTransaction(reversal)
}

function activity(filters: Row): Row[] {
  let rows = store.inventory_transaction.slice()
  if (!filters.include_voided) rows = rows.filter((t) => !t.voided && !t.is_reversal)
  if (filters.plant_id) rows = rows.filter((t) => t.plant_id === Number(filters.plant_id))
  if (filters.order_id) rows = rows.filter((t) => t.order_id === Number(filters.order_id))
  if (filters.location_id) {
    const id = Number(filters.location_id)
    rows = rows.filter((t) => t.from_location_id === id || t.to_location_id === id)
  }
  if (filters.material_id) {
    const id = Number(filters.material_id)
    rows = rows.filter((t) => t.from_material_id === id || t.to_material_id === id)
  }
  if (filters.operation) {
    const code = String(filters.operation).toUpperCase()
    const typeId = store.transaction_type.find((t) => t.code === code)?.transaction_type_id
    rows = rows.filter((t) => t.transaction_type_id === typeId)
  }
  if (filters.date_from) rows = rows.filter((t) => t.user_date >= filters.date_from)
  if (filters.date_to) rows = rows.filter((t) => t.user_date <= filters.date_to)
  rows.sort((a, b) => b.transaction_id - a.transaction_id)
  return annotateVoidRights(rows.slice(0, Number(filters.limit) || 500).map(hydrateTransaction))
}

/** Tell each row whether this user can reverse it, and if not, why not.
 *
 *  The screen used to render nothing at all where the button would be, so an
 *  operator looking at their own duplicate load saw no way to fix it and no
 *  statement that one existed. */
function annotateVoidRights(rows: Row[]): Row[] {
  const user = session
  if (!user) return rows
  const supervisor = hasPermission(user, 'txn.void')
  for (const row of rows) {
    if (supervisor) {
      row.can_void = !(row.voided || row.is_reversal)
      row.void_blocked = row.can_void ? '' : 'It has already been reversed.'
      continue
    }
    const { allowed, why } = selfVoidCheck(row, user)
    row.can_void = allowed
    row.void_blocked = allowed ? '' : `${why} Ask a supervisor to reverse it.`
  }
  return rows
}

function pendingShipments(plantId?: number | null, orderId?: number | null): Row[] {
  const voided = new Set(store.inventory_transaction.filter((t) => t.voided).map((t) => t.transaction_id))
  return store.pending_shipment
    .filter((stage) => !stage.shipped && !stage.cancelled && !voided.has(stage.transaction_id))
    .map((stage): Row => {
      const order = byId.order().get(stage.order_id)!
      const txn = store.inventory_transaction.find((t) => t.transaction_id === stage.transaction_id)!
      // The product on the trailer is the one that was loaded onto it, not
      // whatever the order header says.
      const material = byId.material().get(txn.to_material_id)
      const ordered = byId.material().get(order.material_one_id)
      return {
        ...stage,
        plant_id: order.plant_id,
        plant_code: byId.plant().get(order.plant_id)?.code,
        customer_name: byId.customer().get(order.customer_id)?.name ?? null,
        bol_number: txn.to_bol,
        material_number: material?.number ?? null,
        material_description: material?.description ?? null,
        order_material_number: ordered?.number ?? null,
        loaded_by: byId.user().get(txn.user_id)?.full_name ?? null,
        loaded_at: txn.transaction_date,
      }
    })
    .filter((stage) => (!plantId || stage.plant_id === Number(plantId))
      && (!orderId || stage.order_id === Number(orderId)))
    .sort((a, b) => (a.customer_name ?? '').localeCompare(b.customer_name ?? '') || a.order_id - b.order_id)
}

function ship(stageId: number, userDate?: string): Row {
  const stage = store.pending_shipment.find((s) => s.stage_id === stageId)
  if (!stage) return notFound(`Staged load ${stageId} was not found.`)
  // Claim the stage before writing the shipment. The server does this inside a
  // transaction, where it also serialises two terminals racing each other;
  // there is one thread here, so claiming first is the whole of it.
  if (stage.shipped) refuse('That trailer has already shipped.', { stage_id: stageId })
  if (stage.cancelled) refuse('That load was voided, so there is nothing to ship.', { stage_id: stageId })
  stage.shipped = 1
  const load = store.inventory_transaction.find((t) => t.transaction_id === stage.transaction_id)!
  const txn = postTransaction('SHIP', {
    order_id: stage.order_id,
    plant_id: load.plant_id,
    from_location_id: load.to_location_id,
    from_material_id: load.to_material_id,
    from_qty: stage.quantity,
    from_bol: load.to_bol,
    trailer_number: stage.trailer_number,
    parent_transaction_id: stage.transaction_id,
    user_date: userDate,
    remarks: 'Shipped',
  })
  return txn
}

/* ---------------------------------------------------------------- orders */

const PROGRESS_TYPES: Record<number, string[]> = { 1: ['LOAD'], 2: ['PRODUCE'], 3: ['RECEIVE'], 4: ['MOVE'] }

function progress(order: Row): Row {
  const codes = PROGRESS_TYPES[order.order_type_id] ?? []
  let fulfilled = 0
  let shipped = 0
  for (const txn of store.inventory_transaction) {
    if (txn.order_id !== order.order_id || txn.voided || txn.is_reversal) continue
    const code = kindOf(txn.transaction_type_id)
    if (code && codes.includes(code)) {
      // What arrived counts: for a load, what landed in the trailer — a
      // blend pumped onto the truck counts as the blend the order is for.
      if (!order.material_one_id || txn.to_material_id === order.material_one_id) {
        fulfilled += txn.to_qty
      }
    }
    if (code === 'SHIP') shipped += txn.from_qty
  }
  const ordered = order.material_one_quantity || 0
  const remaining = Math.round((ordered - fulfilled) * 100) / 100
  return {
    qty_fulfilled: Math.round(fulfilled * 100) / 100,
    qty_shipped: Math.round(shipped * 100) / 100,
    // Signed: clamping at zero hid the order that is already over-loaded.
    qty_remaining: remaining,
    over_by: remaining < -0.01 ? Math.round(-remaining * 100) / 100 : 0,
    percent_complete: ordered ? Math.min(Math.round((fulfilled / ordered) * 10000) / 100, 999) : 0,
  }
}

function hydrateOrder(order: Row): Row {
  const materials = byId.material()
  const type = byId.orderType().get(order.order_type_id)
  const status = byId.status().get(order.status_id)
  const plant = byId.plant().get(order.plant_id)
  const customer = byId.customer().get(order.customer_id)
  const vendor = byId.vendor().get(order.vendor_id)
  return {
    ...order,
    order_type: type?.code,
    order_type_description: type?.description,
    plant_code: plant?.code,
    plant_name: plant?.name,
    department_code: byId.department().get(order.department_id)?.code ?? null,
    status: status?.name,
    is_terminal: status?.is_terminal ?? 0,
    company_name: byId.company().get(order.company_id)?.name,
    customer_name: customer?.name ?? null,
    gp_custnmbr: customer?.gp_custnmbr ?? null,
    vendor_name: vendor?.name ?? null,
    gp_vendorid: vendor?.gp_vendorid ?? null,
    material_one_number: materials.get(order.material_one_id)?.number ?? null,
    material_one_description: materials.get(order.material_one_id)?.description ?? null,
    material_two_number: materials.get(order.material_two_id)?.number ?? null,
    material_three_number: materials.get(order.material_three_id)?.number ?? null,
    material_four_number: materials.get(order.material_four_id)?.number ?? null,
    ...progress(order),
  }
}

function searchOrders(query: Row): Row {
  let rows = store.order.filter((order) => order.active)
  const pick = (key: string) => (query[key] === undefined || query[key] === '' ? null : query[key])

  if (pick('plant_id')) rows = rows.filter((o) => o.plant_id === Number(query.plant_id))
  if (pick('order_type_id')) rows = rows.filter((o) => o.order_type_id === Number(query.order_type_id))
  if (pick('department_id')) rows = rows.filter((o) => o.department_id === Number(query.department_id))
  if (pick('status_id')) rows = rows.filter((o) => o.status_id === Number(query.status_id))
  if (pick('customer_id')) rows = rows.filter((o) => o.customer_id === Number(query.customer_id))
  if (pick('vendor_id')) rows = rows.filter((o) => o.vendor_id === Number(query.vendor_id))
  if (pick('material_id')) {
    const id = Number(query.material_id)
    rows = rows.filter((o) => [o.material_one_id, o.material_two_id, o.material_three_id, o.material_four_id].includes(id))
  }
  if (pick('due_from')) rows = rows.filter((o) => o.due_date >= query.due_from)
  if (pick('due_to')) rows = rows.filter((o) => o.due_date <= query.due_to)
  if (query.open_only === true || query.open_only === 'true') {
    rows = rows.filter((o) => !byId.status().get(o.status_id)?.is_terminal)
  }
  if (pick('text')) {
    const needle = String(query.text).trim().toLowerCase()
    const materials = byId.material()
    rows = rows.filter((o) => {
      const material = materials.get(o.material_one_id)
      const haystack = [
        o.order_id, o.order_reference, o.blend_serial_number, o.trailer_number,
        byId.customer().get(o.customer_id)?.name, byId.vendor().get(o.vendor_id)?.name,
        material?.number, material?.description,
      ].filter(Boolean).join(' ').toLowerCase()
      return haystack.includes(needle)
    })
  }

  const total = rows.length
  const limit = Number(query.limit) || 200
  const offset = Number(query.offset) || 0
  rows = rows
    .slice()
    .sort((a, b) => (b.due_date ?? '').localeCompare(a.due_date ?? '') || b.order_id - a.order_id)
    .slice(offset, offset + limit)
  return { total, rows: rows.map(hydrateOrder), limit, offset }
}

function validateOrder(payload: Row): void {
  const fields: Record<string, string> = {}
  if (!payload.order_type_id) fields.order_type_id = 'Choose an order type.'
  if (!payload.plant_id) fields.plant_id = 'Choose a plant.'
  if (!payload.company_id) fields.company_id = 'Choose a company.'
  if (!payload.due_date) fields.due_date = 'Enter a due date.'
  if (payload.order_date && payload.due_date && payload.due_date < payload.order_date) {
    fields.due_date = 'Due date cannot be before the order date.'
  }
  if (!payload.material_one_id) fields.material_one_id = 'Choose the primary material.'
  else if (payload.plant_id) {
    const known = store.material_plant.some(
      (mp) => mp.material_id === Number(payload.material_one_id) && mp.plant_id === Number(payload.plant_id))
    if (!known) fields.material_one_id = 'That material is not set up at this plant.'
  }
  const qty = num(payload.material_one_quantity)
  if (qty === null || qty <= 0) fields.material_one_quantity = 'Enter a quantity greater than zero.'
  if (Number(payload.order_type_id) === 1 && !payload.customer_id) {
    fields.customer_id = 'A sales order needs a customer.'
  }
  if (Number(payload.order_type_id) === 3 && !payload.vendor_id) {
    fields.vendor_id = 'A purchase order needs a vendor.'
  }
  if (Object.keys(fields).length) invalid('This order cannot be saved yet.', fields)
}

const EDITABLE_ORDER_FIELDS = [
  'order_type_id', 'order_date', 'due_date', 'order_reference', 'company_id', 'plant_id',
  'department_id', 'blend_serial_number', 'vendor_id', 'customer_id', 'material_one_id',
  'material_two_id', 'material_three_id', 'material_four_id', 'material_one_quantity',
  'ship_method', 'trailer_number', 'load_by_eta', 'comments', 'status_id',
]

function createOrders(payload: Row): Row[] {
  const user = requireUser()
  requirePermission(user, 'order.write')
  const count = Number(payload.count) || 1
  if (count < 1 || count > 50) invalid('Create between 1 and 50 orders at a time.', { count: '1 to 50.' })
  const body: Row = { order_date: todayIso(), ...payload }
  validateOrder(body)
  requirePlant(user, Number(body.plant_id))

  const created: Row[] = []
  for (let i = 0; i < count; i += 1) {
    const orderId = nextId('order', 'order_id', 328_000)
    const row: Row = {
      order_id: orderId,
      order_type_id: Number(body.order_type_id),
      order_date: String(body.order_date).slice(0, 10),
      due_date: String(body.due_date).slice(0, 10),
      order_reference: body.order_reference || '',
      company_id: Number(body.company_id),
      plant_id: Number(body.plant_id),
      department_id: body.department_id ? Number(body.department_id) : null,
      blend_serial_number: body.blend_serial_number || '',
      vendor_id: body.vendor_id ? Number(body.vendor_id) : null,
      customer_id: body.customer_id ? Number(body.customer_id) : null,
      material_one_id: Number(body.material_one_id),
      material_two_id: body.material_two_id ? Number(body.material_two_id) : null,
      material_three_id: body.material_three_id ? Number(body.material_three_id) : null,
      material_four_id: body.material_four_id ? Number(body.material_four_id) : null,
      material_one_quantity: Number(body.material_one_quantity),
      ship_method: body.ship_method || '',
      trailer_number: body.trailer_number || '',
      load_by_eta: body.load_by_eta ?? null,
      comments: body.comments || '',
      status_id: body.status_id ? Number(body.status_id) : 1,
      active: 1,
      date_added: nowIso(),
      added_by: user.username,
      date_modified: null,
      modified_by: null,
    }
    store.order.push(row)
    audit('create', 'order', orderId, `Created order ${orderId}`, { values: row })
    created.push(hydrateOrder(row))
  }
  return created
}

function updateOrder(orderId: number, payload: Row): Row {
  const user = requireUser()
  requirePermission(user, 'order.write')
  const order = byId.order().get(orderId)
  if (!order) return notFound(`Order ${orderId} was not found.`)
  const status = byId.status().get(order.status_id)
  if (status?.is_terminal) {
    refuse(`Order ${orderId} is ${status.name} and can no longer be edited.`,
      { order_id: orderId, status: status.name })
  }
  requirePlant(user, order.plant_id)

  const merged = { ...order, ...payload }
  validateOrder(merged)
  requirePlant(user, Number(merged.plant_id))

  const before = { ...order }
  for (const key of EDITABLE_ORDER_FIELDS) {
    if (key in payload) order[key] = payload[key] === '' ? null : payload[key]
  }
  order.date_modified = nowIso()
  order.modified_by = user.username
  audit('update', 'order', orderId, `Updated order ${orderId}`,
    { changes: diff(before, Object.fromEntries(EDITABLE_ORDER_FIELDS.map((k) => [k, order[k]]))) })
  return hydrateOrder(order)
}

function closeOrders(orderIds: number[], force: boolean): Row {
  const user = requireUser()
  requirePermission(user, 'order.close')
  const closed: number[] = []
  const skipped: Row[] = []
  for (const orderId of orderIds) {
    const order = byId.order().get(Number(orderId))
    if (!order) { skipped.push({ order_id: orderId, reason: 'not found' }); continue }
    const status = byId.status().get(order.status_id)
    if (status?.is_terminal) {
      skipped.push({ order_id: orderId, reason: `already ${status.name}` })
      continue
    }
    const pending = store.pending_shipment.filter((s) => s.order_id === order.order_id && !s.shipped).length
    if (pending && !force) {
      skipped.push({ order_id: orderId, reason: `${pending} trailer(s) loaded but not shipped` })
      continue
    }
    order.status_id = 4
    order.date_modified = nowIso()
    order.modified_by = user.username
    audit('close', 'order', order.order_id, `Closed order ${order.order_id}`, { forced: force })
    closed.push(order.order_id)
  }
  return { closed, skipped }
}

function orderDetail(orderId: number): Row {
  const order = byId.order().get(orderId)
  if (!order) return notFound(`Order ${orderId} was not found.`)
  const detail = hydrateOrder(order)
  detail.transactions = activity({ order_id: orderId, limit: 200 })
  detail.qc = qcForOrder(orderId)
  detail.qa_checklists = qaChecklists(orderId)
  detail.in_process = inProcess(orderId)
  detail.pending_shipments = pendingShipments(null, orderId)
  if (order.customer_id) detail.customer_requirements = requirements('customer', order.customer_id)
  if (order.vendor_id) detail.vendor_requirements = requirements('vendor', order.vendor_id)
  return detail
}

/** A bill of lading travels with one truck: pass `transactionId` and it covers
 *  that load alone. Without it every load on the order is listed, which is the
 *  order-level view and not what the driver carries. */
function billOfLading(orderId: number, transactionId?: number | null): Row {
  const order = orderDetail(orderId)
  const one = transactionId ? store.inventory_transaction.find((t) => t.transaction_id === Number(transactionId)) : null
  const rows = store.inventory_transaction
    .filter((t) => t.order_id === orderId && !t.voided && kindOf(t.transaction_type_id) === 'LOAD'
      // One truck: every component blended onto the same trailer, same BOL.
      && (!one || (t.to_bol === one.to_bol && (t.trailer_number || '') === (one.trailer_number || ''))))
    .sort((a, b) => a.transaction_id - b.transaction_id)
  // One line per truck, however many components went onto it.
  const byTruck = new Map<string, Row>()
  for (const t of rows) {
    const k = `${t.to_bol}|${t.trailer_number}|${t.to_material_id}`
    const line = byTruck.get(k)
    if (line) { line.quantity += t.to_qty; line.components += 1; continue }
    byTruck.set(k, {
      transaction_id: t.transaction_id,
      bol_number: t.to_bol,
      trailer_number: t.trailer_number,
      quantity: t.to_qty,
      components: 1,
      transaction_date: t.transaction_date,
      material_number: byId.material().get(t.to_material_id)?.number ?? null,
      material_description: byId.material().get(t.to_material_id)?.description ?? null,
      loaded_by: byId.user().get(t.user_id)?.full_name ?? 'system',
    })
  }
  const loads = [...byTruck.values()]
  // The product printed is the product on the truck. If a load went out under
  // a different product from the order header, both are reported.
  const products: Row[] = []
  for (const load of loads) {
    if (load.material_number && !products.some((p) => p.number === load.material_number)) {
      products.push({ number: load.material_number, description: load.material_description ?? '' })
    }
  }
  return {
    shipper: { name: 'FEED ENERGY COMPANY', address: '3121 Dean Avenue, Des Moines, IA 50317' },
    order,
    loads,
    products,
    material_mismatch: Boolean(
      products.length && order.material_one_number
      && !(products.length === 1 && products[0].number === order.material_one_number),
    ),
    single_load: Boolean(transactionId),
    qc: store.qc.filter((q) => q.order_id === orderId && q.active)
      .map((q) => ({
        qc_id: q.qc_id, sample_number: q.sample_number, seal_number: q.seal_number,
        bol_number: q.bol_number, test_date: q.test_date,
      })),
    total_quantity: Math.round(loads.reduce((sum, load) => sum + load.quantity, 0) * 100) / 100,
  }
}

/* -------------------------------------------------------------------- QC */

function hydrateQc(record: Row): Row {
  const order = byId.order().get(record.order_id)!
  const material = byId.material().get(order.material_one_id)
  const evaluations = evaluate(order.material_one_id, qcValues(record))
  return {
    ...record,
    plant_id: order.plant_id,
    plant_code: byId.plant().get(order.plant_id)?.code,
    order_type_id: order.order_type_id,
    order_type: byId.orderType().get(order.order_type_id)?.code,
    material_one_id: order.material_one_id,
    material_number: material?.number ?? null,
    material_description: material?.description ?? null,
    family: material?.family ?? '',
    customer_id: order.customer_id,
    evaluations,
    spec_summary: summarize(evaluations),
  }
}

function qcForOrder(orderId: number): Row[] {
  return store.qc
    .filter((record) => record.order_id === orderId && record.active)
    .sort((a, b) => b.qc_id - a.qc_id)
    .map(hydrateQc)
}

function validateQc(orderId: number, payload: Row): Row {
  const order = byId.order().get(orderId)
  if (!order) return notFound(`Order ${orderId} was not found.`)
  const material = byId.material().get(order.material_one_id) ??
    { number: '?', description: 'unknown material', family: '' }
  const values: Record<string, any> = {}
  for (const [field, analyte] of Object.entries(FIELD_ANALYTE)) values[analyte] = num(payload[field])
  const evaluations = evaluate(order.material_one_id, values)
  const required = requiredTests(order.material_one_id)

  const warnings: Row[] = []
  for (const analyte of required) {
    const field = ANALYTE_FIELD[analyte]
    if (!field) continue
    const value = values[analyte]
    const label = ANALYTE_LABELS[analyte] ?? analyte
    if (value === null) {
      warnings.push({
        field, analyte, severity: 'warning',
        message: `${label} is missing. ${material.number} ${material.description} is tested for ${label.toLowerCase()}.`,
      })
    } else if (value <= 0 && analyte !== 'temp') {
      warnings.push({
        field, analyte, severity: 'warning',
        message: `${label} is ${value}. Expected a value greater than zero.`,
      })
    }
  }
  for (const evaluation of evaluations) {
    if (evaluation.verdict !== 'out_of_spec') continue
    const bounds: string[] = []
    if (evaluation.min_value !== null) bounds.push(`min ${evaluation.min_value}`)
    if (evaluation.max_value !== null) bounds.push(`max ${evaluation.max_value}`)
    warnings.push({
      field: ANALYTE_FIELD[evaluation.analyte] ?? null,
      analyte: evaluation.analyte,
      severity: 'out_of_spec',
      message: `${evaluation.label} ${evaluation.value} is outside spec for ${material.number} (${bounds.join(', ')}).`,
    })
  }
  const requireSample = store.system_setting.find((s) => s.key === 'qc.require_sample_number')?.value === 'true'
  if (requireSample && required.length && !String(payload.sample_number || '').trim()) {
    warnings.push({
      field: 'sample_number', analyte: null, severity: 'warning',
      message: 'No sample number — the LIMS result cannot be matched back to this record.',
    })
  }

  return {
    order_id: orderId,
    material: { number: material.number, description: material.description, family: material.family },
    required_tests: required,
    not_tested: Object.values(FIELD_ANALYTE).filter((a) => !required.includes(a)).sort(),
    evaluations,
    warnings,
    summary: summarize(evaluations),
  }
}

function saveQc(orderId: number, payload: Row, qcId: number | null, acknowledge: boolean): Row {
  const user = requireUser()
  requirePermission(user, 'qc.write')
  const order = byId.order().get(orderId)
  if (!order) return notFound(`Order ${orderId} was not found.`)
  const status = byId.status().get(order.status_id)
  if (status?.is_terminal && !qcId) {
    refuse(`Order ${orderId} is ${status.name}; QC cannot be added.`, { order_id: orderId })
  }

  const hard: Record<string, string> = {}
  for (const field of ['moisture', 'temp', 'ph', 'ffa', 'tfa', 'spintest_fallout']) {
    const value = num(payload[field])
    if (value === null) {
      // A number that will not parse used to be thrown away silently, and the
      // operator got a green "QC recorded" for an empty record.
      const raw = payload[field]
      if (typeof raw === 'string' && raw.trim()) hard[field] = `"${raw.trim()}" is not a number.`
      continue
    }
    if (value < 0) hard[field] = 'Cannot be negative.'
    else if (field === 'ph' && value > 14) hard[field] = 'pH must be between 0 and 14.'
    else if (['moisture', 'ffa', 'tfa'].includes(field) && value > 100) hard[field] = 'Percentages cannot exceed 100.'
  }
  const flash = String(payload.flash_pf || '').trim().toUpperCase()
  if (flash && !['P', 'F', 'PASS', 'FAIL'].includes(flash)) hard.flash_pf = 'Enter P (pass) or F (fail).'
  if (Object.keys(hard).length) invalid('Check the highlighted values.', hard)

  const check = validateQc(orderId, payload)
  // A missing reading is an incomplete record and stays advisory. A reading
  // outside spec is a decision, and the server asks for it to be made rather
  // than trusting one screen to have asked.
  const outOfSpec = check.warnings.filter((w: Row) => w.severity === 'out_of_spec')
  if (outOfSpec.length && !acknowledge) {
    refuse('This result is outside spec. Confirm you have reviewed it before saving.', {
      rule: 'unacknowledged_warnings',
      warnings: outOfSpec,
      acknowledge_field: 'acknowledge_warnings',
    })
  }
  let sampleNumber = String(payload.sample_number || '').trim()
  if (!sampleNumber && !qcId
      && (setting('sample.auto_generate', 'false') === 'true' || payload.generate_sample_number)) {
    sampleNumber = nextSampleNumber(orderId)
  }
  const values: Row = {
    bol_number: payload.bol_number || '',
    test_date: String(payload.test_date || todayIso()).slice(0, 10),
    performed_by: payload.performed_by || user.username,
    moisture: num(payload.moisture),
    temp: num(payload.temp),
    ph: num(payload.ph),
    ffa: num(payload.ffa),
    tfa: num(payload.tfa),
    spintest_fallout: num(payload.spintest_fallout),
    flash_pf: flash ? flash[0] : null,
    steam_on: payload.steam_on ? 1 : 0,
    seal_number: payload.seal_number || '',
    last_material_hauled: payload.last_material_hauled || '',
    sample_number: sampleNumber,
    blend_serial_number: payload.blend_serial_number || order.blend_serial_number,
    comments: payload.comments || '',
    // On the record, not only in the audit detail: re-opening a QC record
    // should show that someone signed off and what they were looking at.
    acknowledged_warnings: outOfSpec.length && acknowledge ? 1 : 0,
    warning_snapshot: outOfSpec.length ? JSON.stringify(outOfSpec) : '',
  }

  let record: Row
  if (qcId) {
    const existing = store.qc.find((q) => q.qc_id === qcId)
    if (!existing) return notFound(`QC record ${qcId} was not found.`)
    record = existing
    const before = { ...record }
    Object.assign(record, values, { date_modified: nowIso(), modified_by: user.username })
    audit('qc.update', 'qc', qcId, `Updated QC ${qcId} on order ${orderId}`, {
      changes: diff(before, values),
      spec_summary: check.summary,
      ...(check.warnings.length ? { warnings: check.warnings, acknowledged_warnings: acknowledge } : {}),
    }, orderId)
  } else {
    const newId = nextId('qc', 'qc_id')
    record = {
      qc_id: newId, order_id: orderId, ...values, active: 1,
      date_added: nowIso(), added_by: user.username, date_modified: null, modified_by: null,
    }
    store.qc.push(record)
    audit('qc.create', 'qc', newId, `Recorded QC ${newId} on order ${orderId}`, {
      values,
      spec_summary: check.summary,
      ...(check.warnings.length ? { warnings: check.warnings, acknowledged_warnings: acknowledge } : {}),
    }, orderId)
  }
  return { ...hydrateQc(record), warnings: check.warnings }
}

function outOfSpec(plantId: number | null, days: number, limit: number): Row[] {
  const cutoff = new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10)
  return store.qc
    .filter((record) => record.active && record.test_date >= cutoff)
    .map(hydrateQc)
    .filter((record) => (!plantId || record.plant_id === Number(plantId))
      && record.spec_summary.status === 'out_of_spec')
    .sort((a, b) => b.test_date.localeCompare(a.test_date) || b.qc_id - a.qc_id)
    .slice(0, limit)
    .map((record) => ({
      ...record,
      evaluations: record.evaluations.filter((e: Row) => e.verdict === 'out_of_spec'),
    }))
}

function inProcess(orderId: number): Row[] {
  return store.qc_in_process
    .filter((row) => row.order_id === orderId)
    .map((row): Row => ({ ...row, test_point_name: byId.testPoint().get(row.test_point_id)?.name }))
    .sort((a, b) => String(b.reading_time).localeCompare(String(a.reading_time)))
}

function addInProcess(orderId: number, payload: Row): Row {
  const user = requireUser()
  requirePermission(user, 'qc.write')
  if (!byId.order().get(orderId)) notFound(`Order ${orderId} was not found.`)
  if (!ANALYTE_LABELS[payload.analyte]) invalid('Choose what was measured.', { analyte: 'Unknown analyte.' })
  if (!payload.test_point_id) invalid('Choose a test point.', { test_point_id: 'Required.' })
  const row: Row = {
    reading_id: nextId('qc_in_process', 'reading_id'),
    order_id: orderId,
    test_point_id: Number(payload.test_point_id),
    reading_time: payload.reading_time || nowIso(),
    analyte: payload.analyte,
    value: num(payload.value),
    comments: payload.comments || '',
    added_by: user.username,
    date_added: nowIso(),
  }
  store.qc_in_process.push(row)
  audit('qc.in_process', 'qc_in_process', row.reading_id,
    `In-process ${row.analyte} reading on order ${orderId}`, { order_id: orderId }, orderId)
  return row
}

function qaChecklists(orderId: number): Row[] {
  return store.qa_header
    .filter((header) => header.order_id === orderId)
    .sort((a, b) => b.header_id - a.header_id)
    .map((header) => {
      const responses = store.qa_response
        .filter((response) => response.header_id === header.header_id)
        .map((response): Row => {
          const question = byId.question().get(response.question_id)
          return {
            ...response,
            question: question?.question ?? '',
            answer_type: question?.answer_type ?? 'text',
            sort_order: question?.sort_order ?? 0,
          }
        })
        .sort((a, b) => a.sort_order - b.sort_order)
      // "No" is a finding; "N/A" is a deliberate answer that the question did
      // not apply. Collapsing N/A into silence lost the difference between
      // "checked, does not apply" and "never asked".
      const isNa = (value: string) => ['N/A', 'NA', 'NOT APPLICABLE']
        .includes(String(value ?? '').trim().toUpperCase())
      return {
        ...header,
        responses,
        exceptions: responses.filter((r) => r.response === 'No').map((r) => r.question),
        not_applicable: responses.filter((r) => isNa(r.response)).map((r) => r.question),
      }
    })
}

function saveQaChecklist(orderId: number, payload: Row, stage?: string | null): Row {
  const user = requireUser()
  requirePermission(user, 'qc.write')
  const order = byId.order().get(orderId)
  if (!order) return notFound(`Order ${orderId} was not found.`)
  const questions = store.qa_question
    .filter((q) => q.enabled && (!stage || (q.stage ?? 'post_load') === stage))
  const responses = payload.responses ?? {}
  const unanswered = questions
    .filter((q) => !String(responses[String(q.question_id)] ?? responses[q.question_id] ?? '').trim())
    .map((q) => q.question)
  if (unanswered.length) {
    invalid('Answer every checklist question before saving. '
      + 'Mark anything that does not apply as N/A.', { responses: unanswered.join('; ') })
  }

  const headerId = nextId('qa_header', 'header_id')
  store.qa_header.push({
    header_id: headerId,
    order_id: orderId,
    plant_id: order.plant_id,
    qc_id: payload.qc_id ?? null,
    trailer_number: payload.trailer_number || '',
    trailer_load_time: payload.trailer_load_time ?? null,
    comments: payload.comments || '',
    stage: stage || 'post_load',
    voided: 0,
    date_added: nowIso(),
    added_by: user.username,
  })
  for (const question of questions) {
    store.qa_response.push({
      response_id: nextId('qa_response', 'response_id'),
      header_id: headerId,
      question_id: question.question_id,
      response: String(responses[String(question.question_id)] ?? responses[question.question_id] ?? ''),
    })
  }
  const failures = questions
    .filter((q) => String(responses[String(q.question_id)] ?? responses[q.question_id]) === 'No')
    .map((q) => q.question)
  audit('qa.checklist', 'qa_header', headerId, `QA checklist completed on order ${orderId}`,
    { failures, order_id: orderId }, orderId)
  return qaChecklists(orderId)[0]
}

function companionPermissions(permissions: string[]): string[] {
  const all = new Set<string>()
  for (const granted of Object.values(ROLE_PERMISSIONS)) for (const p of granted) if (p !== '*') all.add(p)
  all.add('spec.write')
  const held = permissions.includes('*') ? [...all] : permissions
  return held.filter((p) => !COMPANION_BLOCKED.has(p)).sort()
}

/* ----------------------------------------------------------- tank board */

const TANK_TYPES = new Set(['Tank', 'Blend', 'Acid', 'Settle', 'MGR'])

function tankState(total: number, capacity: number | null): string {
  if (total < -0.5) return 'negative'
  if (!capacity) return total > 0.5 ? 'normal' : 'empty'
  const pct = (total / capacity) * 100
  if (pct > 100.01) return 'over'
  if (pct >= 95) return 'high'
  if (pct >= 85) return 'warn'
  if (total <= 0.5) return 'empty'
  if (pct < 5) return 'low'
  return 'normal'
}

/* ----------------------------------------------------------- departments */

/** pims/services/departments.py, from the sandbox's store. */
function departmentsForPlant(plantId: number): Row[] {
  const assigned = new Set((store.plant_department ?? [])
    .filter((pd) => pd.plant_id === plantId).map((pd) => pd.department_id))
  const terminal = new Set(store.status.filter((s) => s.is_terminal).map((s) => s.status_id))
  const vessels = new Map<number, Set<string>>()
  const methods = new Map<number, Set<string>>()
  for (const r of store.blend_recipe.filter((r) => r.active && r.department_id)) {
    if (!vessels.has(r.department_id)) vessels.set(r.department_id, new Set())
    if (!methods.has(r.department_id)) methods.set(r.department_id, new Set())
    vessels.get(r.department_id)!.add(r.vessel_type || 'Blend')
    methods.get(r.department_id)!.add(r.method || 'blend')
  }
  return store.department
    .filter((d) => d.active && assigned.has(d.department_id))
    .sort((a, b) => String(a.description).localeCompare(String(b.description)))
    .map((d) => ({
      department_id: d.department_id,
      code: d.code,
      description: d.description,
      open_orders: store.order.filter((o) => o.plant_id === plantId && o.active !== 0
        && o.department_id === d.department_id && !terminal.has(o.status_id)).length,
      runs_batches: vessels.has(d.department_id),
      vessel_types: [...(vessels.get(d.department_id) ?? [])].sort(),
      methods: [...(methods.get(d.department_id) ?? [])].sort(),
    }))
}

function departmentMaterials(departmentId: number, plantId: number | null): Set<number> {
  const found = new Set<number>()
  const types = new Set(store.material_type.filter((t) => t.department_id === departmentId)
    .map((t) => t.material_type_id))
  for (const m of store.material) if (types.has(m.material_type_id)) found.add(m.material_id)
  for (const r of store.blend_recipe.filter((r) => r.active && r.department_id === departmentId)) {
    found.add(r.material_id)
    for (const c of store.blend_recipe_component.filter((c) => c.recipe_id === r.recipe_id)) found.add(c.material_id)
  }
  const terminal = new Set(store.status.filter((s) => s.is_terminal).map((s) => s.status_id))
  for (const o of store.order) {
    if (o.department_id === departmentId && !terminal.has(o.status_id) && o.material_one_id
      && (!plantId || o.plant_id === plantId)) found.add(o.material_one_id)
  }
  return found
}

/** The same tiles as pims/services/display.py, from the sandbox's store. */
export function tankBoard(plantId: number, departmentId: number | null = null): Row {
  const plant = byId.plant().get(plantId)
  if (!plant) return notFound(`Plant ${plantId} was not found.`)
  const types = new Map(store.location_type.map((t) => [t.location_type_id, t.name]))
  const rows = balances({ plant_id: plantId, location_id: null, material_id: null, as_of: null, include_zero: false })
  const lastMoved = new Map<number, string>()
  for (const t of store.inventory_transaction) {
    for (const loc of [t.from_location_id, t.to_location_id]) {
      if (loc && (!lastMoved.has(loc) || lastMoved.get(loc)! < t.transaction_date)) lastMoved.set(loc, t.transaction_date)
    }
  }
  const tanks = store.location
    .filter((l) => l.plant_id === plantId && l.active && TANK_TYPES.has(types.get(l.location_type_id)))
    .sort((a, b) => String(a.number).localeCompare(String(b.number)))
    .map((l) => {
      const held = rows.filter((r: Row) => r.location_id === l.location_id).sort((a: Row, b: Row) => b.balance - a.balance)
      const total = Math.round(held.reduce((sum: number, r: Row) => sum + r.balance, 0) * 100) / 100
      const capacity = l.max_capacity ?? null
      return {
        location_id: l.location_id,
        number: l.number,
        description: l.description,
        kind: types.get(l.location_type_id),
        capacity,
        total,
        percent_full: capacity ? Math.round((total / capacity) * 1000) / 10 : null,
        room: capacity ? Math.round((capacity - total) * 100) / 100 : null,
        state: tankState(total, capacity),
        products: held.map((r: Row) => ({ material_id: r.material_id, number: r.material_number, description: r.material_description, lbs: r.balance })),
        mixed: held.filter((r: Row) => r.balance > 0.5).length > 1,
        last_moved: lastMoved.get(l.location_id) ?? null,
      }
    })
  for (const batch of openProcessBatches(plantId)) {
    const tile: Row | undefined = tanks.find((t) => t.location_id === batch.vessel_id)
    if (tile) {
      tile.batch = { batch_id: batch.batch_id, stage: batch.status, label: batch.stage_label, minutes: batch.stage_minutes }
      tile.mixed = false
    }
  }
  let shown = tanks
  let department: Row | null = null
  if (departmentId) {
    const d = byId.department().get(departmentId)
    department = d ? { department_id: d.department_id, code: d.code, description: d.description } : null
    const handled = departmentMaterials(departmentId, plantId)
    const vessels = new Set(store.blend_recipe
      .filter((r) => r.active && r.department_id === departmentId).map((r) => r.vessel_type))
    const materialByNumber = new Map(store.material.map((m) => [m.number, m.material_id]))
    shown = tanks.filter((t) => vessels.has(t.kind)
      || t.products.some((p: Row) => p.lbs > 0.5 && handled.has(materialByNumber.get(p.number))))
  }
  return {
    plant: { plant_id: plant.plant_id, code: plant.code, name: plant.name },
    department,
    generated_at: nowIso(),
    tanks: shown,
    abnormal: shown.filter((t) => ['over', 'high', 'negative'].includes(t.state)).length,
  }
}

/* -------------------------------------------------------------- blending */

function recipeComponents(recipeId: number): Row[] {
  const materials = byId.material()
  return store.blend_recipe_component
    .filter((c) => c.recipe_id === recipeId)
    .sort((a, b) => a.sort_order - b.sort_order || a.component_id - b.component_id)
    .map((c) => ({
      ...c,
      material_number: materials.get(c.material_id)?.number ?? null,
      material_description: materials.get(c.material_id)?.description ?? null,
    }))
}

function recipeOutputs(recipeId: number): Row[] {
  const materials = byId.material()
  return (store.blend_recipe_output ?? [])
    .filter((o) => o.recipe_id === recipeId)
    .sort((a, b) => a.sort_order - b.sort_order || a.output_id - b.output_id)
    .map((o) => ({
      ...o,
      material_number: materials.get(o.material_id)?.number ?? null,
      material_description: materials.get(o.material_id)?.description ?? null,
    }))
}

/** blend.recipe_for_material: one per product per vessel; blend first. */
function recipeForMaterial(materialId: number, method: string | null = null, recipeId: number | null = null): Row | null {
  const recipe = recipeId
    ? store.blend_recipe.find((r) => r.recipe_id === recipeId)
    : store.blend_recipe
      .filter((r) => r.material_id === materialId && r.active && (!method || (r.method || 'blend') === method))
      .sort((a, b) => Number((b.method || 'blend') === 'blend') - Number((a.method || 'blend') === 'blend') || a.recipe_id - b.recipe_id)[0]
  if (!recipe) return null
  return { ...recipe, components: recipeComponents(recipe.recipe_id), outputs: recipeOutputs(recipe.recipe_id) }
}

function chargeFor(quantity: number, yieldPct: number): number {
  if (!quantity) return 0
  return Math.round((quantity * 100 / (yieldPct || 100)) * 100) / 100
}

function batchPrefix(departmentId: number | null): string {
  if (!departmentId) return 'B'
  const letter = String(byId.department().get(departmentId)?.code ?? '').trim().slice(0, 1).toUpperCase()
  return /^[A-Z]$/.test(letter) ? letter : 'B'
}

function blendPlan(params: Row): Row {
  requireUser()
  let materialId = params.material_id ? Number(params.material_id) : null
  let plantId = params.plant_id ? Number(params.plant_id) : null
  let order: Row | undefined
  if (params.order_id) {
    order = byId.order().get(Number(params.order_id))
    if (!order) return notFound(`Order ${params.order_id} was not found.`)
    materialId = materialId ?? order.material_one_id
    plantId = plantId ?? order.plant_id
  }
  if (!materialId) invalid('Say what product to blend.', { material_id: 'Choose a product.' })
  const material = byId.material().get(materialId!)!
  const recipe = recipeForMaterial(materialId!)
  if (!recipe) {
    refuse(
      `No recipe is set up for ${material.number} ${material.description}. `
      + 'An administrator adds one under Products & limits.',
      { rule: 'no_recipe', material_id: materialId },
    )
  }

  let target = Number(params.quantity || 0)
  if (target <= 0 && order) {
    target = Math.max(order.material_one_quantity - progress(order).qty_fulfilled, 0)
  }
  target = Math.round(target * 100) / 100

  const notes: string[] = []
  if (order && target) notes.push(`${Math.round(target).toLocaleString()} lbs outstanding on order ${order.order_id}`)

  // Balance per (location, material) at the plant, from the ledger.
  const locPlant = new Map(store.location.map((l) => [l.location_id, l]))
  const balances: Map<string, number> = new Map()
  for (const t of store.inventory_transaction) {
    if (t.to_location_id && locPlant.get(t.to_location_id)?.plant_id === plantId) {
      const k = `${t.to_location_id}:${t.to_material_id}`
      balances.set(k, (balances.get(k) ?? 0) + t.to_qty)
    }
    if (t.from_location_id && locPlant.get(t.from_location_id)?.plant_id === plantId) {
      const k = `${t.from_location_id}:${t.from_material_id}`
      balances.set(k, (balances.get(k) ?? 0) - t.from_qty)
    }
  }
  const bestTank = (componentMaterial: number) => {
    let best: { location: Row; balance: number } | null = null
    for (const [key, balance] of balances) {
      const [loc, mat] = key.split(':').map(Number)
      if (mat !== componentMaterial || balance <= 0) continue
      if (!best || balance > best.balance) best = { location: locPlant.get(loc)!, balance }
    }
    return best
  }

  // Pounds in for the pounds out the order wants (blend.py charge_for).
  const yieldPct = Number(recipe!.yield_pct || 100)
  const charge = chargeFor(target, yieldPct)
  if (yieldPct < 100 && target) {
    notes.push(`${yieldPct}% yield: ${Math.round(charge).toLocaleString()} lbs in for `
      + `${Math.round(target).toLocaleString()} lbs out`)
  }

  const components = (recipe!.components as Row[]).map((component) => {
    const required = Math.round(charge * component.percentage) / 100
    const tank = bestTank(component.material_id)
    if (tank) {
      notes.push(`${tank.location.number} holds the most ${component.material_number} `
        + `(${Math.round(tank.balance).toLocaleString()} lbs)`)
    }
    return {
      material_id: component.material_id,
      material_number: component.material_number,
      material_description: component.material_description,
      percentage: component.percentage,
      required,
      from_location_id: tank?.location.location_id ?? null,
      from_location_number: tank?.location.number ?? null,
      available: Math.round((tank?.balance ?? 0) * 100) / 100,
      short: required - (tank?.balance ?? 0) > 0.01,
    }
  })

  const vesselType = store.location_type.find((t) => t.name === (recipe!.vessel_type || 'Blend'))?.location_type_id
  const destination = store.location
    .filter((l) => l.plant_id === plantId && l.active && l.location_type_id === vesselType)
    .sort((a, b) => String(a.number).localeCompare(String(b.number)))[0]
  let headroom: number | null = null
  if (destination?.max_capacity) {
    let current = 0
    for (const [key, balance] of balances) {
      if (Number(key.split(':')[0]) === destination.location_id) current += balance
    }
    headroom = Math.round((destination.max_capacity - current) * 100) / 100
    notes.push(`${destination.number} has room for ${Math.max(Math.round(headroom), 0).toLocaleString()} lbs`)
  }

  return {
    order_id: order?.order_id ?? null,
    material_id: materialId,
    material_number: material.number,
    material_description: material.description,
    recipe: {
      recipe_id: recipe!.recipe_id, name: recipe!.name, notes: recipe!.notes,
      department_id: recipe!.department_id ?? null, yield_pct: yieldPct, vessel_type: recipe!.vessel_type || 'Blend',
      method: recipe!.method || 'blend',
    },
    quantity: target,
    yield_pct: yieldPct,
    charge,
    components,
    to_location_id: destination?.location_id ?? null,
    to_location_number: destination?.number ?? null,
    destination_headroom: headroom,
    short: components.some((c) => c.short),
    does_not_fit: headroom !== null && target - headroom > 0.01,
    notes,
  }
}

function blendExecute(payload: Row): Row {
  const user = requireUser()
  requirePermission(user, 'txn.post')

  const idempotencyKey = String(payload.idempotency_key ?? '').trim()
  if (idempotencyKey) {
    const already = store.inventory_transaction.find((t) => t.idempotency_key === idempotencyKey)
    if (already?.batch_id) return blendBatch(already.batch_id)
  }

  const materialId = Number(payload.material_id || 0)
  const quantity = Math.round(Number(payload.quantity || 0) * 100) / 100
  const components: Row[] = payload.components ?? []
  const fields: Record<string, string> = {}
  if (!materialId) fields.material_id = 'Choose the product being blended.'
  if (quantity <= 0) fields.quantity = 'Enter a quantity greater than zero.'
  if (!payload.to_location_id) fields.to_location_id = 'Choose the tank the blend goes into.'
  if (!components.length) fields.components = 'The batch has no components.'
  if (Object.keys(fields).length) invalid('This batch cannot be blended.', fields)

  // The recipe, not the client, decides the yield.
  const recipe = recipeForMaterial(materialId)
  if (recipe?.method === 'staged') {
    refuse('This product is made in stages — charged, settled and drawn off — not blended in one go. '
      + "Run it from its department's screen.", { rule: 'staged_recipe' })
  }
  const yieldPct = Number(recipe?.yield_pct || 100)
  const charge = chargeFor(quantity, yieldPct)
  const total = Math.round(components.reduce((sum, c) => sum + Number(c.quantity || 0), 0) * 100) / 100
  if (Math.abs(total - charge) > 0.5 * Math.max(components.length, 1)) {
    invalid(
      `The components add up to ${Math.round(total).toLocaleString()} lbs but the batch needs `
      + `${Math.round(charge).toLocaleString()} lbs in`
      + (yieldPct < 100 ? ` for ${Math.round(quantity).toLocaleString()} lbs out at ${yieldPct}% yield.` : '.'),
      { components: 'Component quantities must sum to the batch charge.' },
    )
  }
  // Each component's share of the output; the last takes the rounding.
  const outputs: number[] = components.slice(0, -1)
    .map((c) => Math.round(Number(c.quantity) * quantity / total * 100) / 100)
  outputs.push(Math.round((quantity - outputs.reduce((a, b) => a + b, 0)) * 100) / 100)
  const departmentId = recipe?.department_id ?? null
  const prefix = batchPrefix(departmentId)
  const department = departmentId ? byId.department().get(departmentId) : null

  let order: Row | undefined
  let plantId = payload.plant_id ? Number(payload.plant_id) : null
  if (payload.order_id) {
    order = byId.order().get(Number(payload.order_id))
    if (!order) return notFound(`Order ${payload.order_id} was not found.`)
    plantId = plantId ?? order.plant_id
    if (order.material_one_id && order.material_one_id !== materialId) {
      const wanted = byId.material().get(order.material_one_id)
      refuse(`Order ${order.order_id} is for ${wanted?.number}, not this product.`,
        { order_id: order.order_id })
    }
  }
  if (!plantId) invalid('Choose a plant.', { plant_id: 'Choose a plant.' })
  requirePlant(user, plantId!)

  // Validate every component posts before writing any: the store has no
  // rollback, so "check everything, then write everything" is what keeps a
  // refused batch from half-happening. The stock and capacity checks in
  // postTransaction run again per row; this pass exists to fail first.
  for (const component of components) {
    const available = balanceOf(Number(component.from_location_id), Number(component.material_id))
    if (Number(component.quantity) - available > 0.01) {
      const loc = byId.location().get(Number(component.from_location_id))
      const mat = byId.material().get(Number(component.material_id))
      refuse(
        `${loc?.number} holds ${Math.round(available).toLocaleString()} lbs of ${mat?.number} — `
        + `cannot take ${Math.round(Number(component.quantity)).toLocaleString()} lbs.`,
        { available: Math.round(available), location: loc?.number, material: mat?.number },
      )
    }
  }

  const sequenceKey = prefix === 'B' ? 'blend' : `batch-${prefix}`
  const sequence = store.number_sequence.find((row) => row.key === sequenceKey)
    ?? (store.number_sequence.push({ key: sequenceKey, next_value: prefix === 'B' ? 5_000 : 1 }),
        store.number_sequence[store.number_sequence.length - 1])
  const batchId = `${prefix}-${String(sequence.next_value).padStart(5, '0')}`
  sequence.next_value += 1
  const label = prefix === 'B' ? 'Blend' : department?.description ?? 'Batch'

  const serial = order?.blend_serial_number || ''
  components.forEach((component, index) => {
    const txn = postTransaction('PRODUCE', {
      order_id: order?.order_id ?? null,
      plant_id: plantId,
      from_location_id: component.from_location_id,
      from_material_id: component.material_id,
      from_qty: component.quantity,
      department_id: departmentId,
      to_location_id: payload.to_location_id,
      to_material_id: materialId,
      to_qty: outputs[index],
      user_date: payload.user_date,
      remarks: payload.remarks
        || `${label} batch ${batchId}${serial ? ` (serial ${serial})` : ''}`
        + (yieldPct < 100 ? ` · ${yieldPct}% yield` : ''),
    })
    const row = store.inventory_transaction.find((t) => t.transaction_id === txn.transaction_id)!
    row.batch_id = batchId
    row.idempotency_key = index === 0 && idempotencyKey ? idempotencyKey : null
  })
  audit('blend.batch', 'blend_batch', batchId,
    `${prefix === 'B' ? 'Blended' : 'Ran'} ${Math.round(quantity).toLocaleString()} lbs in batch ${batchId}`
    + (order ? ` on order ${order.order_id}` : ''),
    { components, to_location_id: payload.to_location_id }, order?.order_id ?? null)
  return blendBatch(batchId)
}

function blendBatch(batchId: string): Row {
  const rows = store.inventory_transaction
    .filter((t) => t.batch_id === batchId)
    .sort((a, b) => a.transaction_id - b.transaction_id)
    .map(hydrateTransaction)
  if (!rows.length) return notFound(`Batch ${batchId} was not found.`)
  return {
    batch_id: batchId,
    order_id: rows[0].order_id,
    product_number: rows[0].to_material_number,
    product_description: rows[0].to_material_description,
    to_location_number: rows[0].to_location_number,
    quantity: Math.round(rows.filter((r) => !r.voided).reduce((sum, r) => sum + r.to_qty, 0) * 100) / 100,
    charged: Math.round(rows.filter((r) => !r.voided).reduce((sum, r) => sum + r.from_qty, 0) * 100) / 100,
    voided: rows.every((r) => r.voided),
    transactions: rows,
  }
}

/* ------------------------------------------------- blend onto the trailer */

/* pims/services/loadblend.py: components straight from their tanks onto the
 * trailer as the ordered blend; caustic dosed to the pH on the screen, so
 * the whole truck posts once and nothing is reversed to get it right. */
function specRange(materialId: number, analyte: string): Row | null {
  const row = store.material_spec.find((s) => s.material_id === materialId && s.analyte === analyte)
  if (!row || (row.min_value === null && row.max_value === null)) return null
  return { analyte, min: row.min_value, max: row.max_value }
}

function loadBlendPlan(orderId: number, quantity: number | null): Row | null {
  requireUser()
  const order = byId.order().get(orderId)
  if (!order) return notFound(`Order ${orderId} was not found.`)
  const recipe = recipeForMaterial(order.material_one_id, 'blend')
  if (!recipe || !recipe.components.length) return null
  if (recipe.components.length === 1 && recipe.components[0].material_id === order.material_one_id) return null
  const remaining = round2(Math.max(order.material_one_quantity - progress(order).qty_fulfilled, 0))
  const target = quantity ? round2(quantity) : remaining
  const types = new Map(store.location_type.map((t) => [t.location_type_id, t.name]))
  const rows = balances({ plant_id: order.plant_id, location_id: null, material_id: null, as_of: null, include_zero: false })
  const components = (recipe.components as Row[]).map((c) => {
    const holding = rows.filter((b: Row) => b.material_id === c.material_id && b.balance > 0.5
      && ['Tank', 'Blend'].includes(types.get(byId.location().get(b.location_id)?.location_type_id)))
      .sort((a: Row, b: Row) => b.balance - a.balance)
    const required = round2(target * c.percentage / 100)
    const tank = holding[0]
    return {
      material_id: c.material_id, material_number: c.material_number, material_description: c.material_description,
      percentage: c.percentage, required, dose: c.dose ?? null,
      from_location_id: tank?.location_id ?? null, from_location_number: tank?.location_number ?? null,
      available: round2(tank?.balance ?? 0), short: !tank || required - tank.balance > 0.01,
      tanks: holding.slice(0, 5).map((b: Row) => ({ location_id: b.location_id, number: b.location_number, lbs: round2(b.balance) })),
    }
  })
  const dose = components.find((c) => c.dose)?.dose
  return {
    order_id: order.order_id, material_id: order.material_one_id,
    recipe: { recipe_id: recipe.recipe_id, name: recipe.name, notes: recipe.notes },
    remaining, quantity: target, components,
    target: dose ? specRange(order.material_one_id, dose) : null,
    trailer_number: order.trailer_number || '',
  }
}

function blendedLoad(firstId: number): Row {
  const first = hydrateTransaction(store.inventory_transaction.find((t) => t.transaction_id === firstId)!)
  const rows = store.inventory_transaction
    .filter((t) => t.to_bol === first.to_bol && (t.trailer_number || '') === (first.trailer_number || '')
      && t.order_id === first.order_id && !t.voided && !t.is_reversal)
    .sort((a, b) => a.transaction_id - b.transaction_id).map(hydrateTransaction)
  const total = round2(rows.reduce((a, r) => a + Number(r.to_qty || 0), 0))
  const ids = new Set(rows.map((r) => r.transaction_id))
  const reading = (store.txn_reading ?? []).filter((r) => ids.has(r.transaction_id)).slice(-1)[0] ?? null
  return {
    ...first, from_qty: total, to_qty: total,
    from_material_number: first.to_material_number,
    from_location_number: `blended from ${[...new Set(rows.map((r) => r.from_location_number))].sort().join(', ')}`,
    components: rows.map((r) => ({
      material_number: r.from_material_number, material_description: r.from_material_description,
      from_location_number: r.from_location_number, quantity: r.from_qty,
    })),
    reading: reading ? { analyte: reading.analyte, value: reading.value } : null,
    blended: true,
  }
}

function loadBlendExecute(orderId: number, payload: Row): Row {
  const user = requireUser()
  requirePermission(user, 'txn.post')
  const order = byId.order().get(orderId)
  if (!order) return notFound(`Order ${orderId} was not found.`)
  requirePlant(user, order.plant_id)
  if (order.order_type_id !== 1) invalid('Only a sales order is loaded onto a trailer.', { order_id: 'Choose a sales order.' })
  const key = String(payload.idempotency_key ?? '').trim()
  if (key) {
    const first = store.inventory_transaction.find((t) => t.idempotency_key === `${key}:0`)
    if (first) return blendedLoad(first.transaction_id)
  }
  const planned = loadBlendPlan(orderId, null)
  if (!planned) refuse('This product is not blended on the trailer — load it from its tank.', { rule: 'not_blended' })
  const specs = new Map((planned!.components as Row[]).map((c) => [c.material_id, c]))
  const trailer = String(payload.trailer_number ?? '').trim()
  const fields: Record<string, string> = {}
  if (!trailer) fields.trailer_number = 'Which trailer?'
  const doses: Row[] = (payload.doses ?? []).filter((d: Row) => Number(d.quantity || 0) > 0)
  const rows: Row[] = []
  for (const c of (payload.components ?? []) as Row[]) {
    const spec = specs.get(Number(c.material_id))
    if (!spec) { fields.components = "A component that is not in this blend's recipe."; continue }
    let qty = Number(c.quantity || 0)
    if (spec.dose && doses.length) qty = doses.reduce((a, d) => a + Number(d.quantity), 0)
    if (qty <= 0) continue
    if (!c.from_location_id) { fields.components = `Say which tank the ${spec.material_number} comes from.`; continue }
    rows.push({ material_id: Number(c.material_id), from_location_id: Number(c.from_location_id), quantity: round2(qty), spec })
  }
  if (!rows.length) fields.components = fields.components ?? 'Nothing to load.'
  if (Object.keys(fields).length) invalid('This load cannot be saved yet.', fields)
  const total = round2(rows.reduce((a, r) => a + r.quantity, 0))
  // The pH first: it is the one the operator can still fix.
  const target = planned!.target
  const lastDose = [...doses].reverse().find((d) => d.reading !== null && d.reading !== undefined && d.reading !== '')
  const final = lastDose ? Number(lastDose.reading) : null
  if (target && final !== null && !payload.acknowledge_reading) {
    if ((target.min !== null && final < target.min) || (target.max !== null && final > target.max)) {
      refuse(`The last ${target.analyte} reading is ${final}; this product should be ${target.min ?? '—'} to ${target.max ?? '—'}. `
        + 'Add more, or confirm to load it as it is.', { rule: 'reading_out_of_range', reading: final, acknowledge_field: 'acknowledge_reading' })
    }
  }
  if (!payload.acknowledge_over_load && order.material_one_quantity) {
    const already = progress(order).qty_fulfilled
    const over = already + total - order.material_one_quantity
    if (over > 0.01) {
      refuse(`Order ${order.order_id} is for ${Math.round(order.material_one_quantity).toLocaleString()} lbs and `
        + `${Math.round(already).toLocaleString()} lbs are already loaded. This load puts it ${Math.round(over).toLocaleString()} lbs over.`,
      { rule: 'over_fulfilment', over_by: round2(over), ordered: order.material_one_quantity, already_loaded: round2(already), acknowledge_field: 'acknowledge_over_load' })
    }
  }
  // Every component must be there before anything is written.
  for (const r of rows) {
    const have = balanceOf(r.from_location_id, r.material_id)
    if (r.quantity - have > 0.01) {
      refuse(`${byId.location().get(r.from_location_id)?.number} holds ${Math.round(have).toLocaleString()} lbs of `
        + `${r.spec.material_number} — cannot take ${Math.round(r.quantity).toLocaleString()} lbs.`, { rule: 'insufficient_stock' })
    }
  }
  const trailerType = store.location_type.find((t) => t.name === 'Trailer')?.location_type_id
  const toLocation = store.location.find((l) => l.plant_id === order.plant_id && l.active && l.location_type_id === trailerType)
  if (!toLocation) refuse('This plant has no trailer location to load onto.', { rule: 'no_trailer_location' })
  let bol = payload.to_bol || ''
  const posted: Row[] = []
  rows.forEach((r, index) => {
    const remarks = r.spec.dose && doses.length
      ? `Dosed to ${r.spec.dose}: ` + doses.map((d) => `+${Math.round(Number(d.quantity)).toLocaleString()} lbs -> ${r.spec.dose} ${d.reading ?? '—'}`).join('; ')
      : ''
    const txn = postTransaction('PROD_LOAD', {
      order_id: orderId, plant_id: order.plant_id, department_id: order.department_id,
      from_location_id: r.from_location_id, from_material_id: r.material_id, from_qty: r.quantity,
      to_location_id: toLocation!.location_id, to_material_id: order.material_one_id, to_qty: r.quantity,
      to_bol: bol || undefined, trailer_number: trailer, user_date: payload.user_date, remarks,
      idempotency_key: key ? `${key}:${index}` : null,
    })
    bol = bol || txn.to_bol
    posted.push(txn)
    if (r.spec.dose && final !== null) {
      store.txn_reading = store.txn_reading ?? []
      store.txn_reading.push({ transaction_id: txn.transaction_id, analyte: r.spec.dose, value: final, source: 'entered' })
    }
  })
  store.pending_shipment.push({
    stage_id: nextId('pending_shipment', 'stage_id'), order_id: orderId, transaction_id: posted[0].transaction_id,
    trailer_number: trailer, quantity: total, shipped: 0, cancelled: 0,
  })
  audit('load.blend', 'transaction', posted[0].transaction_id,
    `Blended ${Math.round(total).toLocaleString()} lbs onto trailer ${trailer} (BOL ${bol})`, { doses }, orderId)
  return blendedLoad(posted[0].transaction_id)
}

/* -------------------------------------------------------- staged batches */

/* pims/services/process.py, from the sandbox's store: soap into a settle
 * tank as 1006 Soap in Process, acid and steam on top, cook, mix, settle,
 * break into oil, MGR and water, each measured with its readings. */
const STAGES = ['charging', 'acid', 'mixing', 'settling', 'drawn']
const OPEN_STAGES = ['charging', 'acid', 'mixing', 'settling']
const STAGE_LABEL: Record<string, string> = {
  charging: 'Soap going in', acid: 'Acid going in', mixing: 'Cooking & mixing', settling: 'Settling',
  drawn: 'Broken', cancelled: 'Cancelled',
}
const STAGE_TIME: Record<string, string> = {
  charging: 'started_at', acid: 'acid_at', mixing: 'mixing_at', settling: 'settling_at', drawn: 'drawn_at',
}
const YIELD_TOLERANCE = 8
const BALANCE_TOLERANCE = 0.10
const round2 = (n: number) => Math.round(n * 100) / 100

function processRow(batchId: string): Row {
  const row = (store.process_batch ?? []).find((b) => b.batch_id === batchId)
  if (!row) return notFound(`Batch ${batchId} was not found.`)
  return row
}

function batchRecipe(batch: Row): Row | null {
  return batch.recipe_id ? recipeForMaterial(batch.material_id, null, batch.recipe_id) : null
}

function batchCharges(batch: Row): Row[] {
  return store.inventory_transaction.filter((t) => t.batch_id === batch.batch_id
    && t.to_location_id === batch.vessel_id && !t.voided && !t.is_reversal)
}

/** Pounds of each ingredient in — from the ingredient side when the tank
 *  holds a process material (1006). */
function processContents(batch: Row): Map<number, number> {
  const recipe = batchRecipe(batch)
  const byIngredient = Boolean(recipe?.process_material_id)
  const held = new Map<number, number>()
  for (const t of batchCharges(batch)) {
    const m = byIngredient && t.from_material_id ? t.from_material_id : t.to_material_id
    held.set(m, (held.get(m) ?? 0) + Number(t.to_qty || 0))
  }
  for (const [k, v] of held) if (v <= 0.005) held.delete(k)
  return held
}

function inVessel(batch: Row): Map<number, number> {
  const held = new Map<number, number>()
  for (const t of batchCharges(batch)) held.set(t.to_material_id, (held.get(t.to_material_id) ?? 0) + Number(t.to_qty || 0))
  return held
}

function recipeGroups(recipe: Row | null): Row[] {
  const groups: Row[] = []
  for (const c of (recipe?.components ?? []) as Row[]) {
    const key = c.grp || `m${c.material_id}`
    let group = groups.find((g) => g.key === key)
    if (!group) {
      group = {
        key,
        label: c.grp ? String(c.grp).replace(/^./, (x: string) => x.toUpperCase()) : c.material_description,
        percentage: c.percentage,
        materials: [],
      }
      groups.push(group)
    }
    group.materials.push({ material_id: c.material_id, number: c.material_number, description: c.material_description })
  }
  return groups
}

function suggestedTanks(plantId: number, materialId: number, exclude: number): number[] {
  const types = new Map(store.location_type.map((t) => [t.location_type_id, t.name]))
  return balances({ plant_id: plantId, location_id: null, material_id: materialId, as_of: null, include_zero: false })
    .filter((r: Row) => r.location_id !== exclude && r.balance > 0.5
      && types.get(byId.location().get(r.location_id)?.location_type_id) === 'Tank')
    .sort((a: Row, b: Row) => ((b.max_capacity || 0) - locationTotal(b.location_id)) - ((a.max_capacity || 0) - locationTotal(a.location_id)))
    .slice(0, 3).map((r: Row) => r.location_id)
}

function processGet(batchId: string): Row {
  const batch = processRow(batchId)
  const recipe = batchRecipe(batch)
  const held = processContents(batch)
  const materials = byId.material()
  const vessel = byId.location().get(batch.vessel_id)
  const readings = new Map<number, Row>()
  for (const r of store.txn_reading ?? []) {
    if (!readings.has(r.transaction_id)) readings.set(r.transaction_id, {})
    readings.get(r.transaction_id)![r.analyte] = r.value
  }
  const rows = store.inventory_transaction.filter((t) => t.batch_id === batch.batch_id)
    .sort((a, b) => a.transaction_id - b.transaction_id)
    .map((t): Row => ({ ...hydrateTransaction(t), readings: readings.get(t.transaction_id) ?? {} }))
  const totalIn = round2([...held.values()].reduce((a, b) => a + b, 0))
  const tfa = Number(recipe?.expected_tfa || 0) || null
  const expectedYield = Number(recipe?.yield_pct || 100)
  const groups = recipeGroups(recipe)
  const guide: Row[] = []
  if (groups.length) {
    const lead = groups[0]
    const leadIn = lead.materials.reduce((a: number, m: Row) => a + (held.get(m.material_id) ?? 0), 0)
    let basisLbs: number
    let basis: string
    if (leadIn > 0) {
      basisLbs = leadIn
      basis = `for the ${Math.round(leadIn).toLocaleString()} lbs of ${String(lead.label).toLowerCase()} in`
    } else {
      const target = Number(batch.target_lbs || 0)
      basisLbs = !target ? 0 : tfa && recipe?.outputs?.length
        ? target / (tfa / 100) / (expectedYield / 100)
        : chargeFor(target, expectedYield) * lead.percentage / 100
      basisLbs = round2(basisLbs)
      basis = `for ${Math.round(target).toLocaleString()} lbs out`
    }
    for (const g of groups) {
      const wanted = round2(basisLbs * g.percentage / 100)
      const have = round2(g.materials.reduce((a: number, m: Row) => a + (held.get(m.material_id) ?? 0), 0))
      guide.push({
        ...g, material_id: g.materials[0].material_id, material_number: g.materials[0].number,
        material_description: g.label, guide_lbs: wanted, charged_lbs: have,
        still_to_add: round2(Math.max(wanted - have, 0)), basis,
      })
    }
  }
  const outRows = rows.filter((r) => r.from_location_id === batch.vessel_id && !r.voided && !r.is_reversal)
  const outputs = ((recipe?.outputs ?? []) as Row[]).map((o): Row => {
    const mine = outRows.filter((r) => r.to_material_id === o.material_id)
    return {
      ...o,
      lbs: round2(mine.reduce((a, r) => a + Number(r.to_qty || 0), 0)),
      into: [...new Set(mine.map((r) => r.to_location_number).filter(Boolean))].sort(),
      measured: mine[0]?.readings ?? {},
      suggested_tanks: suggestedTanks(batch.plant_id, o.material_id, batch.vessel_id),
    }
  })
  const leadIn = guide[0]?.charged_lbs ?? 0
  const measured = outputs.length ? round2(outputs.reduce((a, o) => a + o.lbs, 0))
    : round2(outRows.reduce((a, r) => a + Number(r.to_qty || 0), 0))
  const metrics: Row = { lead_in: leadIn, total_in: totalIn, measured_out: measured }
  for (const g of guide.slice(1)) if (leadIn) metrics[`${g.key}_per_100`] = Math.round(g.charged_lbs / leadIn * 10000) / 100
  if (tfa && leadIn) {
    metrics.tfa = tfa
    metrics.theoretical_oil = round2(leadIn * tfa / 100)
    metrics.expected_oil = round2(leadIn * tfa / 100 * expectedYield / 100)
    const oil = outputs.find((o) => o.role === 'oil')
    if (oil && batch.status === 'drawn') metrics.fpy = Math.round(oil.lbs / (leadIn * tfa / 100) * 1000) / 10
  }
  if (measured && outputs.length && batch.status === 'drawn') {
    metrics.split = Object.fromEntries(outputs.map((o) => [o.role, Math.round(o.lbs / measured * 1000) / 10]))
    metrics.unaccounted = round2(totalIn - measured)
  }
  const since = batch[STAGE_TIME[batch.status] ?? 'started_at'] ?? batch.started_at
  return {
    ...batch,
    stage_label: STAGE_LABEL[batch.status] ?? batch.status,
    stage_minutes: OPEN_STAGES.includes(batch.status)
      ? Math.round((Date.now() - new Date(since).getTime()) / 6000) / 10 : null,
    stage_since: since,
    vessel: vessel ? { location_id: vessel.location_id, number: vessel.number, description: vessel.description, max_capacity: vessel.max_capacity } : null,
    product: materials.get(batch.material_id) ?? null,
    process_material: recipe?.process_material_id ? materials.get(recipe.process_material_id) ?? null : null,
    recipe: recipe ? {
      recipe_id: recipe.recipe_id, name: recipe.name, notes: recipe.notes, yield_pct: expectedYield,
      vessel_type: recipe.vessel_type, expected_tfa: recipe.expected_tfa ?? null,
    } : null,
    expected_yield: expectedYield,
    contents: [...held.entries()].map(([m, lbs]) => ({
      material_id: m, number: materials.get(m)?.number, description: materials.get(m)?.description, lbs: round2(lbs),
    })),
    total_in: totalIn,
    expected_out: outputs.length ? metrics.expected_oil ?? null : round2(totalIn * expectedYield / 100),
    guide,
    outputs,
    metrics,
    transactions: rows,
    timeline: STAGES.map((stage) => ({ stage, label: STAGE_LABEL[stage], at: batch[STAGE_TIME[stage]] ?? null })),
  }
}

function openProcessBatches(plantId: number, departmentId: number | null = null): Row[] {
  return (store.process_batch ?? [])
    .filter((b) => b.plant_id === plantId && OPEN_STAGES.includes(b.status)
      && (!departmentId || b.department_id === departmentId))
    .sort((a, b) => String(a.started_at).localeCompare(String(b.started_at)))
    .map((b) => processGet(b.batch_id))
}

function processVessels(plantId: number, departmentId: number | null = null): Row[] {
  const types = new Set(store.blend_recipe
    .filter((r) => r.active && r.method === 'staged' && (!departmentId || r.department_id === departmentId))
    .map((r) => r.vessel_type))
  const typeName = new Map(store.location_type.map((t) => [t.location_type_id, t.name]))
  const busy = new Map(openProcessBatches(plantId).map((b) => [b.vessel_id, b]))
  return store.location
    .filter((l) => l.plant_id === plantId && l.active && types.has(typeName.get(l.location_type_id)))
    .sort((a, b) => String(typeName.get(b.location_type_id)).localeCompare(String(typeName.get(a.location_type_id)))
      || String(a.number).localeCompare(String(b.number), undefined, { numeric: true }))
    .map((l) => ({
      location_id: l.location_id, number: l.number, description: l.description, max_capacity: l.max_capacity,
      vessel_type: typeName.get(l.location_type_id),
      total: round2(locationTotal(l.location_id)), batch: busy.get(l.location_id) ?? null,
    }))
}

function processSpur(plantId: number): Row[] {
  const types = new Map(store.location_type.map((t) => [t.location_type_id, t.name]))
  const receiveType = store.transaction_type.find((t) => (t.kind || t.code) === 'RECEIVE')?.transaction_type_id
  return balances({ plant_id: plantId, location_id: null, material_id: null, as_of: null, include_zero: false })
    .filter((r: Row) => types.get(byId.location().get(r.location_id)?.location_type_id) === 'Receiving' && r.balance > 0.5)
    .map((r: Row) => {
      const cars = store.inventory_transaction
        .filter((t) => t.to_location_id === r.location_id && t.to_material_id === r.material_id
          && t.transaction_type_id === receiveType && !t.voided && !t.is_reversal)
        .sort((a, b) => b.transaction_id - a.transaction_id).slice(0, 5)
        .map((t) => ({ trailer_number: t.trailer_number, transaction_date: t.transaction_date, to_qty: t.to_qty, order_id: t.order_id }))
      const oldest = cars[cars.length - 1]
      return {
        ...r, location_type: 'Receiving', cars,
        oldest_minutes: oldest ? Math.round((Date.now() - new Date(oldest.transaction_date).getTime()) / 6000) / 10 : null,
      }
    })
}

function processStart(payload: Row): Row {
  const user = requireUser()
  requirePermission(user, 'txn.post')
  if (!payload.order_id) invalid('Say which work order.', { order_id: 'Choose a work order.' })
  const order = byId.order().get(Number(payload.order_id))
  if (!order) return notFound(`Order ${payload.order_id} was not found.`)
  requirePlant(user, order.plant_id)
  const recipe = recipeForMaterial(order.material_one_id, 'staged', order.recipe_id ?? null)
  if (!recipe || recipe.method !== 'staged') {
    refuse(`Order ${order.order_id} is not made in stages — run it on the Blend screen.`, { rule: 'not_staged' })
  }
  const existing = (store.process_batch ?? []).find((b) => b.order_id === order.order_id && OPEN_STAGES.includes(b.status))
  if (existing) return processGet(existing.batch_id)
  const all = processVessels(order.plant_id, recipe!.department_id).filter((v) => v.vessel_type === recipe!.vessel_type)
  const free = all.filter((v) => !v.batch)
  let vesselId = payload.vessel_id ? Number(payload.vessel_id) : null
  if (vesselId) {
    const chosen = all.find((v) => v.location_id === vesselId)
    if (!chosen) invalid("That is not one of this recipe's tanks.", { vessel_id: 'Choose a tank.' })
    if (chosen!.batch) {
      refuse(`${chosen!.number} already has batch ${chosen!.batch.batch_id} in it `
        + `(${chosen!.batch.stage_label.toLowerCase()}). Break that first, or use another tank.`,
      { rule: 'vessel_busy', batch_id: chosen!.batch.batch_id })
    }
  } else if (free.length) {
    vesselId = free[0].location_id
  } else {
    refuse(`Every ${String(recipe!.vessel_type).toLowerCase()} tank at this plant has a batch in it. Break one first.`,
      { rule: 'no_free_vessel' })
  }
  const target = payload.target_lbs
    ? Number(payload.target_lbs)
    : Math.max(order.material_one_quantity - progress(order).qty_fulfilled, 0)
  const prefix = batchPrefix(recipe!.department_id ?? null)
  const key = prefix === 'B' ? 'blend' : `batch-${prefix}`
  const sequence = store.number_sequence.find((row) => row.key === key)
    ?? (store.number_sequence.push({ key, next_value: 1 }), store.number_sequence[store.number_sequence.length - 1])
  const batchId = `${prefix}-${String(sequence.next_value).padStart(5, '0')}`
  sequence.next_value += 1
  store.process_batch = store.process_batch ?? []
  store.process_batch.push({
    batch_id: batchId, order_id: order.order_id, plant_id: order.plant_id,
    department_id: recipe!.department_id ?? order.department_id, recipe_id: recipe!.recipe_id,
    vessel_id: vesselId, material_id: order.material_one_id, target_lbs: round2(target),
    status: 'charging', started_at: nowIso(), started_by: user.username,
    acid_at: null, mixing_at: null, settling_at: null, drawn_at: null, drawn_by: null, drawn_lbs: null, notes: '',
  })
  audit('process.start', 'process_batch', batchId, `Started batch ${batchId} for ${Math.round(target).toLocaleString()} lbs`,
    { vessel_id: vesselId }, order.order_id)
  return processGet(batchId)
}

function receivingLocation(plantId: number, conveyance: string): number {
  const receiving = store.location_type.find((t) => t.name === 'Receiving')?.location_type_id
  const rows = store.location.filter((l) => l.plant_id === plantId && l.active && l.location_type_id === receiving)
    .sort((a, b) => String(a.number).localeCompare(String(b.number)))
  if (!rows.length) refuse('This plant has no receiving location to take a delivery onto.', { rule: 'no_receiving_location' })
  const word = /^rail/i.test(conveyance) ? 'RAIL' : 'TRUCK'
  return (rows.find((l) => String(l.number).toUpperCase().includes(word)) ?? rows[0]).location_id
}

function tagBatch(txn: Row, batchId: string) {
  store.inventory_transaction.find((t) => t.transaction_id === txn.transaction_id)!.batch_id = batchId
}

function processCharge(batchId: string, payload: Row): Row {
  const user = requireUser()
  requirePermission(user, 'txn.post')
  const batch = processRow(batchId)
  requirePlant(user, batch.plant_id)
  if (!['charging', 'acid', 'mixing'].includes(batch.status)) {
    refuse(`Batch ${batchId} is ${STAGE_LABEL[batch.status].toLowerCase()} — nothing more goes in now.`,
      { rule: 'batch_closed_to_charges' })
  }
  const recipe = batchRecipe(batch)
  const allowed = new Set(((recipe?.components ?? []) as Row[]).map((c) => c.material_id))
  const materialId = Number(payload.material_id || 0)
  const quantity = round2(Number(payload.quantity || 0))
  if (allowed.size && !allowed.has(materialId)) {
    invalid("That material is not in this batch's recipe.", { material_id: "Charge one of the recipe's ingredients." })
  }
  if (quantity <= 0) invalid('Enter the pounds that went in.', { quantity: 'Greater than zero.' })
  const into = recipe?.process_material_id || materialId
  const kind = recipe?.process_material_id ? 'PRODUCE' : 'MOVE'
  const vehicle = String(payload.vehicle ?? '').trim()
  const conveyance = String(payload.conveyance ?? '').trim()
  const base = {
    plant_id: batch.plant_id, department_id: batch.department_id, order_id: batch.order_id,
    user_date: payload.user_date, trailer_number: vehicle,
    remarks: payload.remarks || `Charged to ${batchId}${conveyance ? ` off ${conveyance.toLowerCase()} ${vehicle}`.trimEnd() : ''}`,
  }
  let fromLocation = payload.from_location_id ? Number(payload.from_location_id) : null
  if (payload.order_id) {
    // Received onto the truck/rail location against its PO, then unloaded.
    fromLocation = receivingLocation(batch.plant_id, conveyance || 'Truck')
    const receipt = postTransaction('RECEIVE', {
      ...base, order_id: Number(payload.order_id), to_location_id: fromLocation, to_material_id: materialId, to_qty: quantity,
    })
    tagBatch(receipt, batchId)
  }
  if (!fromLocation) {
    invalid('Say where it came from — a tank, the spur, or a delivery.', { from_location_id: 'Choose the tank, or the truck or railcar.' })
  }
  const txn = postTransaction(kind, {
    ...base, from_location_id: fromLocation, from_material_id: materialId, from_qty: quantity,
    to_location_id: batch.vessel_id, to_material_id: into, to_qty: quantity,
  })
  tagBatch(txn, batchId)
  return processGet(batchId)
}

function processAdvance(batchId: string, to: string): Row {
  const user = requireUser()
  requirePermission(user, 'txn.post')
  const batch = processRow(batchId)
  requirePlant(user, batch.plant_id)
  if (!['acid', 'mixing', 'settling'].includes(to)) {
    invalid('The break is its own step; the stages here are acid, mixing and settling.', {})
  }
  const current = STAGES.indexOf(batch.status)
  if (STAGES.indexOf(to) !== current + 1) {
    refuse(`Batch ${batchId} is ${String(STAGE_LABEL[batch.status]).toLowerCase()}; `
      + `it goes to ${current >= 0 && current < 4 ? STAGE_LABEL[STAGES[current + 1]].toLowerCase() : 'nothing'} next.`,
    { rule: 'stage_order' })
  }
  if (to === 'acid' && processContents(batch).size === 0) {
    refuse('Nothing has been charged yet — put the soap in first.', { rule: 'empty_batch' })
  }
  batch.status = to
  batch[STAGE_TIME[to]] = nowIso()
  audit(`process.${to}`, 'process_batch', batchId, `Batch ${batchId}: ${STAGE_LABEL[to].toLowerCase()}`, {}, batch.order_id)
  return processGet(batchId)
}

function processDraw(batchId: string, payload: Row): Row {
  const user = requireUser()
  requirePermission(user, 'txn.post')
  const batch = processRow(batchId)
  requirePlant(user, batch.plant_id)
  if (batch.status !== 'settling') {
    refuse(`Batch ${batchId} is ${String(STAGE_LABEL[batch.status]).toLowerCase()}. It is broken once it has settled.`,
      { rule: 'not_settled' })
  }
  const recipe = batchRecipe(batch)
  const held = [...inVessel(batch).entries()].filter(([, v]) => v > 0.005)
  const totalIn = round2(held.reduce((a, [, v]) => a + v, 0))
  const single = !(recipe?.outputs ?? []).length
  let outputs: Row[] = single
    ? [{ material_id: batch.material_id, to_location_id: payload.to_location_id, quantity: payload.quantity, readings: payload.readings ?? {} }]
    : (payload.outputs ?? []).filter((o: Row) => Number(o.quantity || 0) > 0)
  if (!single) {
    const wanted = new Set((recipe!.outputs as Row[]).map((o) => o.material_id))
    if (outputs.some((o) => !wanted.has(Number(o.material_id)))) {
      invalid("A layer that is not one of this recipe's outputs.", { outputs: "Oil, MGR or water — the recipe's outputs." })
    }
  }
  const fields: Record<string, string> = {}
  if (!outputs.length || !outputs.every((o) => Number(o.quantity || 0) > 0)) fields.quantity = 'Enter the pounds that came off.'
  if (outputs.some((o) => !o.to_location_id)) fields.to_location_id = 'Choose the tank each layer went into.'
  if (Object.keys(fields).length) invalid('This break cannot be recorded.', fields)
  outputs = outputs.map((o) => ({ ...o, material_id: Number(o.material_id), to_location_id: Number(o.to_location_id), quantity: round2(Number(o.quantity)) }))
  const measured = round2(outputs.reduce((a, o) => a + o.quantity, 0))
  if (single) {
    if (measured - totalIn > 0.5) {
      invalid(`${Math.round(measured).toLocaleString()} lbs drawn off is more than the ${Math.round(totalIn).toLocaleString()} lbs that went in.`,
        { quantity: 'Cannot be more than was charged.' })
    }
    const expected = Number(recipe?.yield_pct || 100)
    const actual = totalIn ? Math.round(measured / totalIn * 1000) / 10 : 0
    if (Math.abs(actual - expected) > YIELD_TOLERANCE && !payload.acknowledge_yield) {
      refuse(`That is a ${actual}% yield; this recipe usually gives about ${expected}%. Check the gauge — confirm if the number is right.`,
        { rule: 'unexpected_yield', actual, expected, acknowledge_field: 'acknowledge_yield' })
    }
  } else if (totalIn && Math.abs(measured - totalIn) / totalIn > BALANCE_TOLERANCE && !payload.acknowledge_balance) {
    const gap = totalIn - measured
    refuse(`${Math.round(totalIn).toLocaleString()} lbs went in and ${Math.round(measured).toLocaleString()} lbs are measured coming off — `
      + `${Math.round(Math.abs(gap)).toLocaleString()} lbs ${gap > 0 ? 'unaccounted for' : 'more than went in'}. `
      + 'Check the gauges; confirm if the numbers are right.',
    { rule: 'unbalanced_break', total_in: totalIn, measured, acknowledge_field: 'acknowledge_balance' })
  }
  // Every destination must take its layer before anything is written.
  for (const o of outputs) {
    const dest = byId.location().get(o.to_location_id)
    if (dest?.max_capacity && locationTotal(dest.location_id) + o.quantity - dest.max_capacity > 0.5) {
      refuse(`${dest.number} has room for ${Math.round(dest.max_capacity - locationTotal(dest.location_id)).toLocaleString()} lbs.`,
        { rule: 'over_capacity' })
    }
  }
  const shares = outputs.slice(0, -1).map((o) => round2(totalIn * o.quantity / measured))
  shares.push(round2(totalIn - shares.reduce((a, b) => a + b, 0)))
  const remarks = payload.remarks || `Broke batch ${batchId}: ${Math.round(measured).toLocaleString()} lbs measured off ${Math.round(totalIn).toLocaleString()} lbs in`
  const produce = (fromMaterial: number, fromQty: number, output: Row) => {
    const txn = postTransaction('PRODUCE', {
      order_id: batch.order_id, plant_id: batch.plant_id, department_id: batch.department_id,
      from_location_id: batch.vessel_id, from_material_id: fromMaterial, from_qty: fromQty,
      to_location_id: output.to_location_id, to_material_id: output.material_id, to_qty: output.quantity,
      user_date: payload.user_date, remarks,
    })
    tagBatch(txn, batchId)
    store.txn_reading = store.txn_reading ?? []
    for (const [analyte, value] of Object.entries(output.readings ?? {})) {
      if (value === null || value === undefined || value === '') continue
      store.txn_reading.push({ transaction_id: txn.transaction_id, analyte, value: Number(value), source: 'entered' })
    }
  }
  outputs.forEach((o, i) => {
    if (held.length === 1) produce(held[0][0], shares[i], o)
    else for (const [m, lbs] of held) produce(m, round2(shares[i] * lbs / totalIn), { ...o, quantity: round2(o.quantity * lbs / totalIn) })
  })
  batch.status = 'drawn'
  batch.drawn_at = nowIso()
  batch.drawn_by = user.username
  batch.drawn_lbs = measured
  audit('process.draw', 'process_batch', batchId,
    `Broke batch ${batchId}: ${Math.round(measured).toLocaleString()} lbs measured off ${Math.round(totalIn).toLocaleString()} lbs in`,
    { outputs, total_in: totalIn }, batch.order_id)
  return processGet(batchId)
}

function processCancel(batchId: string, reason: string): Row {
  const user = requireUser()
  requirePermission(user, 'txn.post')
  const batch = processRow(batchId)
  requirePlant(user, batch.plant_id)
  if (!OPEN_STAGES.includes(batch.status)) refuse(`Batch ${batchId} is already ${batch.status}.`, { rule: 'batch_closed' })
  if (processContents(batch).size) {
    refuse(`Batch ${batchId} has product in ${byId.location().get(batch.vessel_id)?.number}. `
      + 'Undo the charges, or break it, before cancelling.', { rule: 'batch_not_empty' })
  }
  batch.status = 'cancelled'
  batch.notes = reason
  audit('process.cancel', 'process_batch', batchId, `Cancelled batch ${batchId}`, { reason }, batch.order_id)
  return processGet(batchId)
}

/* ------------------------------------------------------------------ LIMS */

const reportable = (row: Row) =>
  row.include_in_report && row.current_version && !String(row.component).startsWith('DATE-')

function limsFreshness(): Row {
  const rows = store.lims_result
  const sources = [...new Set(rows.map((row) => row.source))]
  const lastRetrieved = rows.reduce(
    (latest: string | null, row) => (!latest || row.retrieved_at > latest ? row.retrieved_at : latest), null)
  const age = hoursSince(lastRetrieved)
  let status = 'ok'
  let detail = age === null ? 'Projection rows carry no retrieval timestamp.' : `Last refresh ${age.toFixed(1)} h ago.`
  if (!rows.length) { status = 'failed'; detail = 'No LIMS results have ever been projected.' }
  else if (age === null) status = 'degraded'
  else if (age > 72) { status = 'failed'; detail = `Last refresh was ${age.toFixed(0)} h ago (fails past 72 h) — PIMS is showing stale lab data.` }
  else if (age > 24) { status = 'degraded'; detail = `Last refresh was ${age.toFixed(0)} h ago (warns past 24 h).` }
  if (sources.length > 1) {
    if (status === 'ok') status = 'degraded'
    detail += ` Results present from ${sources.length} sources: ${sources.join(', ')}.`
  }
  return {
    status, detail, mode: 'local', sources, expected_source: 'XLIMSFEEDGROUP',
    results: rows.length,
    samples: new Set(rows.map((row) => row.sample_code)).size,
    test_codes: new Set(rows.filter(reportable).map((row) => row.test_code)).size,
    last_retrieved: lastRetrieved,
    newest_sample: rows.reduce((latest: string | null, row) =>
      (!latest || (row.sampled_at ?? '') > latest ? row.sampled_at : latest), null),
    age_hours: age === null ? null : Math.round(age * 100) / 100,
    warn_after_hours: 24,
    fail_after_hours: 72,
  }
}

function limsMatrix(sampleCodes: string[], selections?: Row[] | null): Row {
  const codes = [...new Set(sampleCodes.filter(Boolean))]
  if (!codes.length) return { columns: [], rows: [], misses: [], source: 'XLIMSFEEDGROUP', requested_samples: 0 }
  const wanted = new Set(codes)
  const hits = store.lims_result.filter((row) => wanted.has(row.sample_code) && reportable(row))

  const available = [...new Set(hits.map((row) => `${row.test_code} ${row.component}`))]
    .sort()
    .map((key) => key.split(' ') as [string, string])
  const columns = selections?.length
    ? selections.filter((s) => s.test_code && s.component).map((s) => [s.test_code, s.component] as [string, string])
    : available
  const columnKeys = columns.map(([test, component]) => `${test} - ${component}`)

  const indexed = new Map<string, Row>()
  for (const row of hits) indexed.set(`${row.sample_code} ${row.test_code} ${row.component}`, row)

  const misses: Row[] = []
  const rows = codes.map((sample) => {
    const out: Row = { sample_code: sample }
    columns.forEach(([test, component], i) => {
      const hit = indexed.get(`${sample} ${test} ${component}`)
      if (!hit) {
        out[columnKeys[i]] = null
        misses.push({ sample_code: sample, column: columnKeys[i], reason: 'no result recorded' })
      } else {
        out[columnKeys[i]] = hit.value_num ?? hit.value_text
      }
    })
    return out
  })

  return {
    columns: columnKeys, rows, misses,
    source: store.lims_result[store.lims_result.length - 1]?.source ?? 'XLIMSFEEDGROUP',
    requested_samples: codes.length,
  }
}

/* ------------------------------------------------------------- reference */

function requirements(partyType: string, partyId: number): Row[] {
  return store.partner_requirement
    .filter((row) => row.party_type === partyType && row.party_id === Number(partyId) && row.active)
    .sort((a, b) => a.sort_order - b.sort_order || a.requirement_id - b.requirement_id)
}

function materialsFor(plantId?: number | null): Row[] {
  const types = new Map(store.material_type.map((t) => [t.material_type_id, t.name]))
  return store.material
    .filter((material) => material.active
      && (!plantId || store.material_plant.some(
        (mp) => mp.material_id === material.material_id && mp.plant_id === Number(plantId))))
    .map((material): Row => ({ ...material, material_type: types.get(material.material_type_id) }))
    .sort((a, b) => a.number.localeCompare(b.number))
}

function locationsFor(plantId?: number | null): Row[] {
  const types = new Map(store.location_type.map((t) => [t.location_type_id, t.name]))
  return store.location
    .filter((location) => location.active && (!plantId || location.plant_id === Number(plantId)))
    .map((location): Row => ({
      ...location,
      location_type: types.get(location.location_type_id),
      plant_code: byId.plant().get(location.plant_id)?.code,
    }))
    .sort((a, b) => (a.plant_code ?? '').localeCompare(b.plant_code ?? '') || a.number.localeCompare(b.number))
}

function referenceBundle(plantId?: number | null): Row {
  return {
    plants: store.plant.filter((p) => p.active),
    companies: store.company.filter((c) => c.active),
    departments: store.department.filter((d) => d.active),
    order_types: store.order_type,
    statuses: store.status,
    transaction_types: store.transaction_type,
    materials: materialsFor(plantId),
    locations: locationsFor(plantId),
    customers: store.customer.filter((c) => c.active).sort((a, b) => a.name.localeCompare(b.name)),
    vendors: store.vendor.filter((v) => v.active).sort((a, b) => a.name.localeCompare(b.name)),
    test_points: store.test_point.filter((t) => t.active && (!plantId || t.plant_id === Number(plantId))),
    qa_questions: store.qa_question.filter((q) => q.enabled).sort((a, b) => a.sort_order - b.sort_order),
    analytes: Object.keys(ANALYTE_LABELS).map((key) => ({
      key, label: ANALYTE_LABELS[key], unit: ANALYTE_UNITS[key] ?? '',
    })),
  }
}

function materialDetail(materialId: number): Row {
  const material = store.material.find((m) => m.material_id === materialId)
  if (!material) return notFound(`Material ${materialId} does not exist.`)
  return {
    ...material,
    material_type: store.material_type.find((t) => t.material_type_id === material.material_type_id)?.name,
    tests: requiredTests(materialId),
    specs: store.material_spec
      .filter((spec) => spec.material_id === materialId && spec.active)
      .sort((a, b) => a.analyte.localeCompare(b.analyte)),
    plants: store.material_plant
      .filter((mp) => mp.material_id === materialId)
      .map((mp) => byId.plant().get(mp.plant_id))
      .filter(Boolean),
  }
}

function listSpecs(family?: string | null, needsReview?: boolean): Row[] {
  const materials = byId.material()
  return store.material_spec
    .filter((spec) => spec.active)
    .map((spec): Row => {
      const material = materials.get(spec.material_id)!
      return {
        ...spec,
        number: material.number,
        description: material.description,
        family: material.family,
      }
    })
    .filter((row) => (!family || row.family === family) && (!needsReview || row.needs_review))
    .sort((a, b) => a.number.localeCompare(b.number) || a.analyte.localeCompare(b.analyte))
}

function upsertSpec(payload: Row): Row {
  const user = requireUser()
  // Widening a limit makes a failing result pass, so reading the limit and
  // changing it are not the same permission.
  requirePermission(user, 'spec.write')
  const materialId = Number(payload.material_id)
  const analyte = payload.analyte
  if (!ANALYTE_LABELS[analyte]) invalid(`Unknown analyte ${analyte}.`, { analyte: 'Not a known analyte.' })
  const min = num(payload.min_value)
  const max = num(payload.max_value)
  if (min !== null && max !== null && min > max) {
    invalid('Minimum cannot be greater than maximum.', { min_value: 'Must not exceed the maximum.' })
  }
  let spec = store.material_spec.find((row) => row.material_id === materialId && row.analyte === analyte)
  if (!spec) {
    spec = {
      spec_id: nextId('material_spec', 'spec_id'),
      material_id: materialId, analyte, active: 1,
    }
    store.material_spec.push(spec)
  }
  Object.assign(spec, {
    min_value: min, max_value: max,
    note: payload.note ?? '', source: payload.source ?? 'PIMS',
    needs_review: payload.needs_review ? 1 : 0, active: 1,
  })
  audit('spec.update', 'material_spec', spec.spec_id,
    `Limit updated for material ${materialId} / ${analyte}`, payload)
  return spec
}

/* ----------------------------------------------------------- query builder */

interface QueryField { name: string; label: string; type: string; get: (row: Row) => any }

function queryField(name: string, label: string, type: string, get: (row: Row) => any): QueryField {
  return { name, label, type, get }
}

function querySources(): Record<string, { label: string; description: string; rows: () => Row[]; fields: QueryField[] }> {
  const materials = byId.material()
  const locations = byId.location()
  return {
    orders: {
      label: 'Orders',
      description: 'Order header with plant, material, customer and vendor',
      rows: () => store.order.filter((o) => o.active).map(hydrateOrder),
      fields: [
        queryField('order_id', 'Order Id', 'number', (r) => r.order_id),
        queryField('order_type', 'Type', 'text', (r) => r.order_type),
        queryField('order_date', 'Order Date', 'date', (r) => r.order_date),
        queryField('due_date', 'Due Date', 'date', (r) => r.due_date),
        queryField('plant_code', 'Plant', 'text', (r) => r.plant_code),
        queryField('department_code', 'Dept', 'text', (r) => r.department_code),
        queryField('status', 'Status', 'text', (r) => r.status),
        queryField('material_number', 'Mat 1', 'text', (r) => r.material_one_number),
        queryField('material_description', 'Mat 1 Description', 'text', (r) => r.material_one_description),
        queryField('quantity', 'Qty Ordered', 'number', (r) => r.material_one_quantity),
        queryField('customer_name', 'Customer', 'text', (r) => r.customer_name),
        queryField('vendor_name', 'Vendor', 'text', (r) => r.vendor_name),
        queryField('order_reference', 'Reference', 'text', (r) => r.order_reference),
        queryField('blend_serial_number', 'Blend SN', 'text', (r) => r.blend_serial_number),
        queryField('trailer_number', 'Trailer #', 'text', (r) => r.trailer_number),
        queryField('comments', 'Comments', 'text', (r) => r.comments),
        queryField('added_by', 'Added By', 'text', (r) => r.added_by),
        queryField('date_added', 'Date Added', 'date', (r) => r.date_added),
      ],
    },
    transactions: {
      label: 'Inventory activity',
      description: 'Every posted movement, with from/to locations and quantities',
      rows: () => store.inventory_transaction.filter((t) => !t.voided).map(hydrateTransaction),
      fields: [
        queryField('transaction_id', 'Trans ID', 'number', (r) => r.transaction_id),
        queryField('order_id', 'Order Id', 'number', (r) => r.order_id),
        queryField('operation', 'Type', 'text', (r) => r.transaction_type),
        queryField('plant_code', 'Plant', 'text', (r) => r.plant_code),
        queryField('user_date', 'User Trans Date', 'date', (r) => r.user_date),
        queryField('transaction_date', 'Actual Trans Date', 'date', (r) => r.transaction_date),
        queryField('username', 'User', 'text', (r) => r.username),
        queryField('from_material', 'From Mat', 'text', (r) => r.from_material_number),
        queryField('from_location', 'From Loc', 'text', (r) => r.from_location_number),
        queryField('from_qty', 'From Qty', 'number', (r) => r.from_qty),
        queryField('from_bol', 'From BOL', 'text', (r) => r.from_bol),
        queryField('to_material', 'To Mat', 'text', (r) => r.to_material_number),
        queryField('to_location', 'To Loc', 'text', (r) => r.to_location_number),
        queryField('to_qty', 'To Qty', 'number', (r) => r.to_qty),
        queryField('to_bol', 'To BOL', 'text', (r) => r.to_bol),
        queryField('trailer_number', 'Trailer #', 'text', (r) => r.trailer_number),
        queryField('tank_hours', 'Tank Time', 'number', (r) => r.tank_hours),
        queryField('employee_hours', 'Labor', 'number', (r) => r.employee_hours),
        queryField('remarks', 'Remarks', 'text', (r) => r.remarks),
      ],
    },
    qc: {
      label: 'Quality control',
      description: 'QC records with the order and product they belong to',
      rows: () => store.qc.filter((q) => q.active).map(hydrateQc),
      fields: [
        queryField('qc_id', 'QC Id', 'number', (r) => r.qc_id),
        queryField('order_id', 'Order Id', 'number', (r) => r.order_id),
        queryField('order_type', 'Type', 'text', (r) => r.order_type),
        queryField('plant_code', 'Plant', 'text', (r) => r.plant_code),
        queryField('test_date', 'Test Date', 'date', (r) => r.test_date),
        queryField('sample_number', 'Sample #', 'text', (r) => r.sample_number),
        queryField('bol_number', 'BOL #', 'text', (r) => r.bol_number),
        queryField('material_number', 'Material', 'text', (r) => r.material_number),
        queryField('material_description', 'Material Description', 'text', (r) => r.material_description),
        queryField('customer_name', 'Customer', 'text', (r) => byId.customer().get(r.customer_id)?.name ?? null),
        queryField('moisture', 'Moisture', 'number', (r) => r.moisture),
        queryField('temp', 'Temp', 'number', (r) => r.temp),
        queryField('ph', 'pH', 'number', (r) => r.ph),
        queryField('ffa', 'FFA', 'number', (r) => r.ffa),
        queryField('tfa', 'TFA', 'number', (r) => r.tfa),
        queryField('spintest_fallout', 'Spintest', 'number', (r) => r.spintest_fallout),
        queryField('flash_pf', 'Flash', 'text', (r) => r.flash_pf),
        queryField('seal_number', 'Seal #', 'text', (r) => r.seal_number),
        queryField('last_material_hauled', 'Last Material Hauled', 'text', (r) => r.last_material_hauled),
        queryField('performed_by', 'Performed By', 'text', (r) => r.performed_by),
        queryField('comments', 'Comments', 'text', (r) => r.comments),
      ],
    },
    balances: {
      label: 'Location balances',
      description: 'Current quantity on hand by location and material',
      rows: () => balances().map((row) => ({
        ...row,
        family: materials.get(row.material_id)?.family ?? '',
        max_capacity: locations.get(row.location_id)?.max_capacity ?? null,
      })),
      fields: [
        queryField('plant_code', 'Plant', 'text', (r) => r.plant_code),
        queryField('location_number', 'Location', 'text', (r) => r.location_number),
        queryField('location_description', 'Location Description', 'text', (r) => r.location_description),
        queryField('location_type', 'Location Type', 'text', (r) => r.location_type),
        queryField('material_number', 'Material', 'text', (r) => r.material_number),
        queryField('material_description', 'Material Description', 'text', (r) => r.material_description),
        queryField('family', 'Family', 'text', (r) => r.family),
        queryField('balance', 'Balance (lbs)', 'number', (r) => r.balance),
        queryField('max_capacity', 'Capacity (lbs)', 'number', (r) => r.max_capacity),
      ],
    },
  }
}

const OPERATORS: Record<string, number | 'list'> = {
  '=': 1, '<>': 1, '>': 1, '>=': 1, '<': 1, '<=': 1,
  LIKE: 1, 'NOT LIKE': 1, BETWEEN: 2, IN: 'list', 'IS NULL': 0, 'IS NOT NULL': 0,
}

const MAX_ROWS = 5000

function likeToRegExp(pattern: string): RegExp {
  const escaped = String(pattern).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`^${escaped.replace(/%/g, '.*').replace(/_/g, '.')}$`, 'i')
}

function matches(value: any, operator: string, operand: any): boolean {
  if (operator === 'IS NULL') return value === null || value === undefined || value === ''
  if (operator === 'IS NOT NULL') return !(value === null || value === undefined || value === '')
  if (value === null || value === undefined) return false
  const asNumber = typeof value === 'number'
  const cast = (input: any) => (asNumber ? Number(input) : String(input))
  switch (operator) {
    case '=': return cast(value) === cast(operand)
    case '<>': return cast(value) !== cast(operand)
    case '>': return cast(value) > cast(operand)
    case '>=': return cast(value) >= cast(operand)
    case '<': return cast(value) < cast(operand)
    case '<=': return cast(value) <= cast(operand)
    case 'LIKE': return likeToRegExp(String(operand)).test(String(value))
    case 'NOT LIKE': return !likeToRegExp(String(operand)).test(String(value))
    case 'BETWEEN': {
      const [low, high] = Array.isArray(operand) ? operand : String(operand).split(',')
      return cast(value) >= cast(String(low).trim()) && cast(value) <= cast(String(high).trim())
    }
    case 'IN': {
      const items = (Array.isArray(operand) ? operand : String(operand).split(','))
        .map((item) => String(item).trim()).filter(Boolean)
      return items.some((item) => cast(value) === cast(item))
    }
    default: return false
  }
}

function runQuery(definition: Row, prompts: Row = {}): Row {
  const user = requireUser()
  requirePermission(user, 'query.run')
  const sources = querySources()
  const source = sources[definition.source]
  if (!source) return invalid(`Unknown data source ${definition.source}.`, { source: 'Not a known data area.' })

  const byName = new Map(source.fields.map((field) => [field.name, field]))
  const requested: string[] = definition.fields?.length
    ? definition.fields
    : source.fields.slice(0, 8).map((field) => field.name)
  const unknown = requested.filter((name) => !byName.has(name))
  if (unknown.length) invalid(`Unknown field(s): ${unknown.join(', ')}.`, { fields: 'Not in this data area.' })

  interface CompiledFilter { field: QueryField; operator: string; value: any; logic: string }

  const filters: CompiledFilter[] = (definition.filters ?? []).map((filter: Row, index: number) => {
    const field = byName.get(filter.field)
    if (!field) return invalid(`Cannot filter on ${filter.field}.`, { field: 'Not in this data area.' })
    const operator = String(filter.operator || '=').toUpperCase()
    if (!(operator in OPERATORS)) invalid(`Unsupported operator ${operator}.`, { operator: 'Not supported.' })
    let value = filter.value
    if (filter.prompt) {
      const key = filter.prompt_key || filter.field
      if (!(key in prompts)) invalid(`This query prompts for ${key}.`, { [key]: 'Enter a value.' })
      value = prompts[key]
    }
    if (OPERATORS[operator] === 1 && (value === '' || value === undefined || value === null)) {
      invalid(`Enter a value for ${filter.field}.`, { [filter.field]: 'Required.' })
    }
    return { field, operator, value, logic: index === 0 ? 'AND' : String(filter.logic || 'AND').toUpperCase() }
  })

  let rows = source.rows().filter((row) => {
    if (!filters.length) return true
    let keep = matches(filters[0].field.get(row), filters[0].operator, filters[0].value)
    for (const filter of filters.slice(1)) {
      const hit = matches(filter.field.get(row), filter.operator, filter.value)
      keep = filter.logic === 'OR' ? keep || hit : keep && hit
    }
    return keep
  })

  for (const sort of [...(definition.sort ?? [])].reverse() as Row[]) {
    const field = byName.get(sort.field)
    if (!field) return invalid(`Cannot sort on ${sort.field}.`, { sort: 'Not in this data area.' })
    const direction = String(sort.direction || 'ASC').toUpperCase()
    if (!['ASC', 'DESC'].includes(direction)) invalid('Sort direction must be ASC or DESC.', { sort: 'ASC or DESC.' })
    rows = rows.slice().sort((a, b) => {
      const left = field.get(a)
      const right = field.get(b)
      const cmp = left === right ? 0
        : left === null || left === undefined ? -1
        : right === null || right === undefined ? 1
        : typeof left === 'number' && typeof right === 'number' ? left - right
        : String(left).localeCompare(String(right))
      return direction === 'DESC' ? -cmp : cmp
    })
  }

  const limit = Math.max(1, Math.min(Number(definition.limit) || 1000, MAX_ROWS))
  const projected = rows.slice(0, limit).map((row) =>
    Object.fromEntries(requested.map((name) => [name, byName.get(name)!.get(row)])))

  const where = filters.map((filter, index) =>
    `${index ? `${filter.logic} ` : ''}${filter.field.name} ${filter.operator}` +
    (OPERATORS[filter.operator] === 0 ? '' : ' ?')).join(' ')

  return {
    columns: requested.map((name) => {
      const field = byName.get(name)!
      return { name: field.name, label: field.label, type: field.type }
    }),
    rows: projected,
    row_count: projected.length,
    truncated: projected.length >= limit,
    sql: `SELECT ${requested.join(', ')}\nFROM ${definition.source}` +
      (where ? `\nWHERE ${where}` : '') +
      ((definition.sort ?? []).length
        ? `\nORDER BY ${definition.sort.map((s: Row) => `${s.field} ${s.direction || 'ASC'}`).join(', ')}`
        : '') +
      `\nLIMIT ${limit}`,
    parameters: filters
      .filter((filter) => OPERATORS[filter.operator] !== 0)
      .map((filter) => filter.value),
  }
}

function queryCatalogue(): Row {
  const sources = querySources()
  return {
    sources: Object.entries(sources).map(([key, source]) => ({
      key,
      label: source.label,
      description: source.description,
      fields: source.fields.map((field) => ({ name: field.name, label: field.label, type: field.type })),
    })),
    operators: Object.entries(OPERATORS).map(([key, args]) => ({ key, args })),
    max_rows: MAX_ROWS,
  }
}

/* --------------------------------------------------------------- inquiry */

function inquiry(tab: string, filters: Row): Row {
  // Every branch returns; the final call throws.
  const columns = (...pairs: [string, string][]) => pairs.map(([name, label]) => ({ name, label }))
  if (tab === 'balance') {
    return {
      tab,
      rows: balances({
        plant_id: filters.plant_id, location_id: filters.location_id,
        material_id: filters.material_id, as_of: filters.as_of, include_zero: filters.include_zero,
      }),
      columns: columns(
        ['plant_code', 'Plant'], ['location_number', 'Location'], ['location_description', 'Description'],
        ['location_type', 'Type'], ['material_number', 'Material'], ['material_description', 'Material Description'],
        ['balance', 'Balance (lbs)'], ['max_capacity', 'Capacity (lbs)'], ['percent_full', '% Full'],
      ),
    }
  }
  if (tab === 'activity') {
    return {
      tab,
      rows: activity(filters),
      columns: columns(
        ['transaction_id', 'Trans ID'], ['order_id', 'Order Id'], ['transaction_type', 'Type'],
        ['plant_code', 'Plant'], ['user_date', 'User Trans Date'], ['username', 'User'],
        ['from_material_number', 'From Mat'], ['from_location_number', 'From Loc'], ['from_qty', 'From Qty'],
        ['to_material_number', 'To Mat'], ['to_location_number', 'To Loc'], ['to_qty', 'To Qty'],
        ['trailer_number', 'Trailer #'], ['remarks', 'Remarks'],
      ),
    }
  }
  if (tab === 'order') {
    const result = searchOrders({ ...filters, due_from: filters.date_from, due_to: filters.date_to, limit: filters.limit || 500 })
    return {
      tab,
      rows: result.rows,
      columns: columns(
        ['order_id', 'Order Id'], ['order_type', 'Type'], ['order_date', 'Order Date'], ['due_date', 'Due Date'],
        ['plant_code', 'Plant'], ['status', 'Status'], ['material_one_number', 'Mat 1'],
        ['material_one_description', 'Mat 1 Description'], ['material_one_quantity', 'Qty Ordered'],
        ['qty_fulfilled', 'Qty Complete'], ['percent_complete', '% Complete'],
        ['customer_name', 'Customer'], ['vendor_name', 'Vendor'],
      ),
    }
  }
  if (tab === 'qc') {
    let rows = store.qc.filter((record) => record.active).map(hydrateQc)
    if (filters.plant_id) rows = rows.filter((r) => r.plant_id === Number(filters.plant_id))
    if (filters.order_id) rows = rows.filter((r) => r.order_id === Number(filters.order_id))
    if (filters.material_id) rows = rows.filter((r) => r.material_one_id === Number(filters.material_id))
    if (filters.sample_number) {
      const needle = String(filters.sample_number).toLowerCase()
      rows = rows.filter((r) => String(r.sample_number).toLowerCase().includes(needle))
    }
    if (filters.date_from) rows = rows.filter((r) => r.test_date >= filters.date_from)
    if (filters.date_to) rows = rows.filter((r) => r.test_date <= filters.date_to)
    rows = rows
      .map((record): Row => ({
        ...record,
        spec_status: record.spec_summary.status,
        out_of_spec: record.spec_summary.out_of_spec.join(', '),
      }))
      .sort((a, b) => b.test_date.localeCompare(a.test_date) || b.qc_id - a.qc_id)
      .slice(0, Number(filters.limit) || 500)
    if (filters.out_of_spec_only) rows = rows.filter((r) => r.spec_status === 'out_of_spec')
    return {
      tab,
      rows,
      columns: columns(
        ['qc_id', 'QC Id'], ['order_id', 'Order Id'], ['plant_code', 'Plant'], ['test_date', 'Test Date'],
        ['material_number', 'Material'], ['sample_number', 'Sample #'], ['bol_number', 'BOL #'],
        ['moisture', 'Moisture'], ['temp', 'Temp'], ['ph', 'pH'], ['ffa', 'FFA'], ['tfa', 'TFA'],
        ['spintest_fallout', 'Spintest'], ['spec_status', 'Spec'], ['out_of_spec', 'Out of spec'],
        ['performed_by', 'Performed By'],
      ),
    }
  }
  return invalid(`Unknown inquiry ${tab}.`, { tab: 'Choose balance, activity, order or qc.' })
}

export function toCsv(rows: Row[], columns: string[]): string {
  const escape = (value: any) => {
    if (value === null || value === undefined) return ''
    const text = String(value)
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
  }
  return [columns.join(','), ...rows.map((row) => columns.map((c) => escape(row[c])).join(','))].join('\r\n') + '\r\n'
}

/* --------------------------------------------------------------- support */

function dataQuality(plantId?: number | null): Row {
  const all = balances()
  const scoped = (row: Row) => !plantId || row.plant_id === Number(plantId)
  const twoDaysAgo = new Date(Date.now() - 2 * 86_400_000).toISOString()
  const thirtyDaysAgo = new Date(Date.now() - 30 * 86_400_000).toISOString().slice(0, 10)
  const today = todayIso()

  const staleLoads = pendingShipments(plantId ?? null)
    .filter((stage) => stage.loaded_at < twoDaysAgo)
    .map((stage) => ({
      stage_id: stage.stage_id, order_id: stage.order_id, trailer_number: stage.trailer_number,
      quantity: stage.quantity, loaded_at: stage.loaded_at, plant_code: stage.plant_code,
    }))

  const overdue = store.order
    .filter((order) => order.active && !byId.status().get(order.status_id)?.is_terminal
      && order.due_date < today && (!plantId || order.plant_id === Number(plantId)))
    .map((order) => ({
      order_id: order.order_id, due_date: order.due_date,
      plant_code: byId.plant().get(order.plant_id)?.code,
      status: byId.status().get(order.status_id)?.name,
      order_type: byId.orderType().get(order.order_type_id)?.code,
    }))

  const qcRecent = store.qc.filter((record) => record.active && record.test_date >= thirtyDaysAgo)
  const limsSamples = new Set(store.lims_result.map((row) => row.sample_code))
  const describeQc = (record: Row) => {
    const order = byId.order().get(record.order_id)!
    return {
      qc_id: record.qc_id, order_id: record.order_id, test_date: record.test_date,
      sample_number: record.sample_number,
      plant_code: byId.plant().get(order.plant_id)?.code,
      material_number: byId.material().get(order.material_one_id)?.number ?? null,
    }
  }
  const inPlant = (record: Row) =>
    !plantId || byId.order().get(record.order_id)?.plant_id === Number(plantId)

  const withoutSample = qcRecent.filter((r) => inPlant(r) && !String(r.sample_number || '').trim()).map(describeQc)
  const unmatched = qcRecent
    .filter((r) => inPlant(r) && String(r.sample_number || '').trim() && !limsSamples.has(r.sample_number))
    .map(describeQc)

  // Loading before the trailer check is allowed but not invisible. Derived
  // from the records rather than from the remark the client wrote, so it holds
  // however the load was posted.
  const sevenDaysAgo = new Date(Date.now() - 7 * 86_400_000).toISOString()
  const uncheckedLoads = store.inventory_transaction
    .filter((t) => kindOf(t.transaction_type_id) === 'LOAD' && !t.voided && !t.is_reversal
      && t.transaction_date >= sevenDaysAgo
      && (!plantId || t.plant_id === Number(plantId))
      && !store.qa_header.some((h) => h.order_id === t.order_id && !h.voided
        && (h.stage ?? 'post_load') === 'pre_load' && h.date_added <= t.transaction_date))
    .map((t) => ({
      transaction_id: t.transaction_id, order_id: t.order_id, trailer_number: t.trailer_number,
      quantity: t.from_qty, loaded_at: t.transaction_date,
      plant_code: byId.plant().get(t.plant_id)?.code,
      loaded_by: byId.user().get(t.user_id)?.full_name ?? null,
    }))

  const negative = all.filter((row) => scoped(row) && row.balance < -0.01)
  const overCapacity = all.filter((row) => scoped(row) && row.max_capacity && row.balance > row.max_capacity + 0.01)

  const findings = [
    { key: 'negative_balance', label: 'Locations with a negative balance', severity: 'high', rows: negative,
      action: 'Post an adjustment, or void the transaction that overdrew the location.' },
    { key: 'over_capacity', label: 'Locations holding more than their stated capacity', severity: 'medium', rows: overCapacity,
      action: 'Confirm the tank capacity on the location record, or correct the balance.' },
    { key: 'stale_loads', label: 'Trailers loaded more than 2 days ago and never shipped', severity: 'medium', rows: staleLoads,
      action: 'Ship the load in PIMS, or void the load transaction if it never left.' },
    { key: 'overdue_orders', label: 'Open orders past their due date', severity: 'low', rows: overdue,
      action: 'Close, reschedule, or cancel.' },
    { key: 'unchecked_loads', label: 'Trailers loaded before the trailer check was answered', severity: 'medium', rows: uncheckedLoads,
      action: 'Ask the loader to complete the trailer check on the order. If this is routine rather than occasional, the check is being treated as paperwork.' },
    { key: 'qc_without_sample', label: 'QC records saved without a sample number', severity: 'medium', rows: withoutSample,
      action: 'Add the sample number so LIMS results can be matched to the load.' },
    { key: 'unmatched_samples', label: 'QC sample numbers with no LIMS result', severity: 'medium', rows: unmatched,
      action: 'Check the LIMS projection is refreshing and that the sample was logged in LabWare under this code.' },
  ].map((finding) => ({ ...finding, count: finding.rows.length, rows: finding.rows.slice(0, 25) }))

  const total = findings.reduce((sum, finding) => sum + finding.count, 0)
  return {
    status: total === 0 ? 'ok' : 'attention',
    total_findings: total,
    findings,
    plant_id: plantId ?? null,
    generated_at: nowIso(),
  }
}

function diagnostics(): Row {
  const lims = limsFreshness()
  const needsReview = store.material_spec.filter((spec) => spec.needs_review && spec.active).length
  const finished = store.material.filter((m) => m.active && m.material_type_id === 1)
  const withoutSpecs = finished.filter((m) => !store.material_spec.some((s) => s.material_id === m.material_id && s.active))
  const withoutTests = finished.filter((m) => !store.material_test.some((t) => t.material_id === m.material_id))
  const setupParts: string[] = []
  if (needsReview) setupParts.push(`${needsReview} limit(s) flagged for confirmation`)
  if (withoutSpecs.length) setupParts.push(`${withoutSpecs.length} finished product(s) with no limits`)
  if (withoutTests.length) setupParts.push(`${withoutTests.length} finished product(s) with no test list`)

  const checks: Row = {
    database: {
      status: 'ok',
      detail: `${store.order.length.toLocaleString()} orders, ${store.inventory_transaction.length.toLocaleString()} transactions.`,
      path: 'in-browser sandbox (no server)',
      size_mb: 0,
      tables: 28,
      counts: {
        order: store.order.length,
        inventory_transaction: store.inventory_transaction.length,
        qc: store.qc.length,
        material: store.material.length,
        app_user: store.app_user.length,
      },
    },
    lims,
    sessions: {
      status: 'ok',
      detail: `${session ? 1 : 0} active session(s), 0 expired awaiting cleanup.`,
      active: session ? 1 : 0,
      expired: 0,
    },
    errors: {
      status: errorLog.length ? 'degraded' : 'ok',
      detail: `${errorLog.length} error(s) recorded in ${requestCount} request(s) this session.`,
      counters: { requests: requestCount, client_errors: errorLog.length },
      recent: errorLog.slice(-10).reverse(),
      slow_requests: [],
    },
    product_setup: {
      status: (needsReview || withoutSpecs.length || withoutTests.length) ? 'degraded' : 'ok',
      detail: setupParts.join('; ') || 'Every finished product has limits and a test list.',
      needs_review: needsReview,
      materials_without_specs: withoutSpecs.slice(0, 25),
      materials_without_tests: withoutTests.slice(0, 25),
    },
  }
  const order: Record<string, number> = { ok: 0, degraded: 1, failed: 2 }
  const worst = Object.values(checks).reduce(
    (acc: string, check: any) => (order[check.status] > order[acc] ? check.status : acc), 'ok')

  return {
    status: worst,
    service: 'pims',
    version: '1.0.0',
    environment: 'SANDBOX',
    checked_at: nowIso(),
    checks,
    runtime: { started_at: startedAt, uptime_seconds: Math.round((Date.now() - startedMs) / 1000) },
    configuration: {
      database_url: 'in-browser sandbox — no server, no persistence',
      lims_mode: 'local projection (stamped fresh on load)',
      lims_source: 'XLIMSFEEDGROUP',
      lims_warn_hours: 24,
      lims_fail_hours: 72,
      session_hours: 12,
      auto_seed: true,
    },
  }
}

/* ------------------------------------------------------------- job runner */

function autoCloseCandidates(plantId: number | null): Row[] {
  const minPercent = Number(setting('autoclose.min_percent', '99'))
  const requireQc = setting('autoclose.require_qc', 'true') === 'true'
  return searchOrders({ plant_id: plantId, open_only: true, limit: 1000 }).rows.filter((order: Row) => {
    if (order.percent_complete < minPercent) return false
    if (store.pending_shipment.some((stage) => stage.order_id === order.order_id && !stage.shipped)) return false
    if (order.order_type === 'SO' && order.qty_shipped <= 0) return false
    if (requireQc && !store.qc.some((record) => record.order_id === order.order_id && record.active)) return false
    return true
  })
}

function runJob(job: string, dryRun: boolean, plantId: number | null): Row {
  if (job === 'auto-close') {
    const ready = autoCloseCandidates(plantId)
    if (dryRun) {
      return { enabled: true, dry_run: true, candidates: ready.length, would_close: ready.map((o) => o.order_id) }
    }
    const result = closeOrders(ready.map((order) => order.order_id), false)
    recordJobRun('auto_close', { orders_closed: result.closed.length })
    return { enabled: true, candidates: ready.length, ...result }
  }
  if (job === 'alerts') {
    const found = evaluateAlerts(
      plantId,
      () => limsFreshness(),
      (plant, days, limit) => outOfSpec(plant, days, limit),
      (plant) => dataQuality(plant),
      (filters) => balances(filters),
    )
    const result = runAlerts(found, !dryRun)
    if (!dryRun) recordJobRun('alerts', { alerts_new: result.new.length })
    return result
  }
  if (job === 'recurring') {
    // Standing orders are configured on the server; the sandbox has none.
    if (!dryRun) recordJobRun('recurring', { orders_created: 0 })
    return { due: [], created: [], dry_run: dryRun }
  }
  if (job === 'lims-sync') {
    // Same rule as the server's stub adapter: fill gaps only.
    const known = new Set(store.lims_result.map((row) => row.sample_code))
    const gaps = store.qc
      .filter((record) => record.active && String(record.sample_number || '').trim()
        && !known.has(record.sample_number))
      .slice(0, 50)
    const rows: Row[] = []
    for (const gap of gaps) {
      for (const [field, test, component] of [
        ['moisture', 'MOISTURE', '%MOIST'],
        ['ffa', 'FFA (NIR)', 'R-FFA'],
        ['tfa', 'TFA (NIR)', '%TFA 1'],
        ['ph', 'PH', 'PH'],
      ] as [string, string, string][]) {
        if (gap[field] === null || gap[field] === undefined) continue
        rows.push({
          sample_code: gap.sample_number, test_code: test, component,
          value: Math.round(Number(gap[field]) * (0.985 + Math.random() * 0.03) * 100) / 100,
          sampled_at: gap.test_date,
        })
      }
    }
    if (dryRun) return { adapter: 'sandbox', fetched: rows.length, written: 0, dry_run: true }
    const retrieved = nowIso()
    for (const row of rows) {
      store.lims_result.push({
        lims_result_id: nextId('lims_result', 'lims_result_id'),
        sample_code: row.sample_code, test_code: row.test_code, component: row.component,
        value_text: String(row.value), value_num: row.value,
        include_in_report: 1, current_version: 1, sampled_at: row.sampled_at,
        source: 'XLIMSFEEDGROUP', retrieved_at: retrieved,
      })
    }
    recordJobRun('lims_sync', { adapter: 'sandbox', fetched: rows.length, written: rows.length })
    return { adapter: 'sandbox', fetched: rows.length, written: rows.length, freshness: limsFreshness() }
  }
  if (job === 'gp-sync') {
    const counts = { customers: { created: 0, updated: 0, unchanged: 1 }, vendors: { created: 0, updated: 0, unchanged: 1 }, orders: { created: 0, unchanged: 0, skipped: 0 } }
    if (dryRun) return { adapter: 'sandbox', dry_run: true, would_read: { customers: 1, vendors: 1, orders: 0 } }
    recordJobRun('gp_sync', counts)
    return { adapter: 'sandbox', counts }
  }
  if (job === 'daily') {
    const alertsResult = runJob('alerts', dryRun, plantId)
    const closeResult = runJob('auto-close', dryRun, plantId)
    if (!dryRun) recordJobRun('daily', {
      orders_closed: closeResult.closed?.length ?? 0,
      alerts_new: alertsResult.new?.length ?? 0,
    })
    return { alerts: alertsResult, auto_close: closeResult, recurring: { created: [] } }
  }
  return invalid(`Unknown job ${job}.`, { job: 'Not a job the sandbox runs.' })
}

/* ---------------------------------------------------------------- router */

const startedAt = nowIso()
const startedMs = Date.now()
let requestCount = 0
const errorLog: Row[] = []

function correlationId(): string {
  return Math.random().toString(16).slice(2, 14)
}

function match(path: string, pattern: string): string[] | null {
  const pathParts = path.split('?')[0].split('/').filter(Boolean)
  const patternParts = pattern.split('/').filter(Boolean)
  if (pathParts.length !== patternParts.length) return null
  const params: string[] = []
  for (let i = 0; i < patternParts.length; i += 1) {
    if (patternParts[i].startsWith(':')) params.push(decodeURIComponent(pathParts[i]))
    else if (patternParts[i] !== pathParts[i]) return null
  }
  return params
}

function queryParams(path: string): Row {
  const [, search] = path.split('?')
  const params: Row = {}
  if (!search) return params
  for (const [key, value] of new URLSearchParams(search)) {
    params[key] = value === 'true' ? true : value === 'false' ? false : value
  }
  return params
}

/** Dispatch a request the way the FastAPI app would. */
/* Companion preview: open the sandbox with #companion to see PIMS as the
 * read-only mirror of the legacy database would present it. The same rules as
 * the server: writes refused with a pointer to the legacy screen, write
 * permissions held by nobody, the write-only screens hidden. */
function companionPreview(): boolean {
  try {
    // Remembered once chosen; #standalone switches the preview back off.
    if (window.location.hash.includes('standalone')) localStorage.removeItem('pims.companion')
    else if (window.location.hash.includes('companion')) localStorage.setItem('pims.companion', '1')
    return localStorage.getItem('pims.companion') === '1'
  } catch {
    return window.location.hash.includes('companion')
  }
}

const COMPANION_BLOCKED = new Set(['txn.post', 'txn.void', 'order.write', 'order.close', 'qc.write'])

const COMPANION_ALLOWED_WRITES = [
  /^\/api\/auth\/(login|logout|pin)$/,
  /^\/api\/orders\/\d+\/qc\/validate$/,
  /^\/api\/lims\/(matrix|ingest)$/,
  /^\/api\/inquiry\/[^/]+(\/csv)?$/,
  /^\/api\/query\/run(\/csv)?$/,
  /^\/api\/reports\/[^/]+(\/csv)?$/,
  /^\/api\/query\/saved(\/\d+)?$/,
  /^\/api\/alerts\/(run|\d+\/acknowledge)$/,
  /^\/api\/jobs\/[^/]+\/run$/,
  /^\/api\/specs$/,
  /^\/api\/materials\/\d+\/tests$/,
  /^\/api\/legacy\/sync$/,
]

const LEGACY_SCREENS: [RegExp, string][] = [
  [/^\/api\/transactions\/receive/, 'Order Selection Menu → Receive'],
  [/^\/api\/transactions\/produce/, 'Order Selection Menu → Produce'],
  [/^\/api\/transactions\/move/, 'Order Selection Menu → Move'],
  [/^\/api\/transactions\/load/, 'Order Selection Menu → Load Trailer'],
  [/^\/api\/transactions\/shrink/, 'Order Selection Menu → Shrinkage'],
  [/^\/api\/transactions\/\d+\/void/, "the order's transactions in PIMS"],
  [/^\/api\/shipments\//, 'Order Selection Menu → Ship Trailer'],
  [/^\/api\/blend\//, 'Order Selection Menu → Produce'],
  [/^\/api\/orders\/close/, 'Order Edit Menu → Close Selected Orders'],
  [/^\/api\/orders\/\d+\/qc/, 'Quality Control → Regular QC'],
  [/^\/api\/qc\//, 'Quality Control → Regular QC'],
  [/^\/api\/orders\/\d+\/in-process/, 'Quality Control → In Process Testing'],
  [/^\/api\/orders\/\d+\/qa-checklist/, 'QA Checklist'],
  [/^\/api\/orders\/\d+$/, 'Order Edit Menu → Edit Selected Order'],
  [/^\/api\/orders$/, 'Order Edit Menu → Create Order'],
]

const companionSyncedAt = Date.now() - 3 * 60_000

export async function handle(method: string, path: string, body: Row = {}): Promise<any> {
  requestCount += 1
  try {
    const bare = path.split('?')[0]
    if (companionPreview()) {
      if (method === 'GET' && bare === '/api/legacy/status') {
        return {
          mode: 'companion', companion: true, configured: true,
          last_sync: new Date(companionSyncedAt).toISOString(),
          age_minutes: Math.round((Date.now() - companionSyncedAt) / 60_000),
          orphans: {}, unclassified_transaction_types: [],
        }
      }
      if (method !== 'GET' && !COMPANION_ALLOWED_WRITES.some((pattern) => pattern.test(bare))) {
        const screen = LEGACY_SCREENS.find(([pattern]) => pattern.test(bare))?.[1] ?? null
        const where = screen ? ` in the PIMS desktop application (${screen})` : ' in the PIMS desktop application'
        fail(409, 'companion_read_only',
          'This is the read-only companion to PIMS, so it cannot change anything. '
          + `Make this change${where}; it will appear here at the next sync.`,
          { legacy_screen: screen, path: bare })
      }
      const result = await route(method, path, body)
      if (method === 'GET' && bare === '/api/health') return { ...result, mode: 'companion' }
      if (method === 'GET' && bare === '/api/auth/me' && result?.permissions) {
        return { ...result, permissions: companionPermissions(result.permissions) }
      }
      if (method === 'POST' && bare === '/api/auth/login' && result?.user) {
        return { ...result, user: { ...result.user, permissions: companionPermissions(result.user.permissions) } }
      }
      return result
    }
    return await route(method, path, body)
  } catch (error: any) {
    const shaped = error && typeof error === 'object' && 'code' in error
      ? error
      : { status: 500, code: 'unhandled', message: String(error?.message ?? error), detail: {} }
    shaped.correlation_id = correlationId()
    errorLog.push({
      at: nowIso(), method, path: path.split('?')[0], code: shaped.code,
      message: shaped.message, correlation_id: shaped.correlation_id,
      username: session?.username ?? null,
    })
    throw shaped
  }
}

async function route(method: string, path: string, body: Row): Promise<any> {
  const params = queryParams(path)
  const plantId = params.plant_id ? Number(params.plant_id) : null
  let m: string[] | null

  if (method === 'GET' && match(path, '/api/health')) {
    return { status: 'ok', service: 'pims', version: '1.0.0', environment: 'SANDBOX', checked_at: nowIso() }
  }

  // auth — the sandbox accepts any seeded username; roles still apply.
  if (method === 'POST' && match(path, '/api/auth/login')) {
    const user = store.app_user.find((u) => u.username === String(body.username || '').trim())
    if (!user) return fail(401, 'unauthenticated', 'Username or password is incorrect.')
    session = user
    audit('login', 'user', user.user_id, `${user.username} signed in`)
    return {
      token: `sandbox-${user.user_id}`,
      expires_at: new Date(Date.now() + 12 * 3_600_000).toISOString(),
      user: publicUser(user),
    }
  }
  if (method === 'POST' && match(path, '/api/auth/pin')) {
    // The sandbox accepts the seeded PINs (and any 4+ digits) so the kiosk
    // flow can be walked through; the server checks a PBKDF2 hash.
    const user = store.app_user.find((u) => u.username === String(body.username || '').trim())
    if (!user) return fail(401, 'unauthenticated', 'That PIN was not recognised.')
    if (String(body.pin || '').length < 4) {
      return fail(401, 'unauthenticated', 'That PIN was not recognised.')
    }
    session = user
    audit('login.pin', 'user', user.user_id, `${user.username} signed in at a kiosk`)
    return {
      token: `sandbox-${user.user_id}`,
      expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
      session_minutes: 30,
      user: publicUser(user),
    }
  }
  if (method === 'GET' && match(path, '/api/kiosk/plants')) {
    return store.plant.filter((plant) => plant.active)
  }
  if (method === 'GET' && match(path, '/api/auth/kiosk-users')) {
    const plant = Number(params.plant_id ?? 1)
    return store.user_plant_access
      .filter((access) => access.plant_id === plant)
      .map((access) => byId.user().get(access.user_id))
      .filter((user): user is Row => Boolean(user))
      .map((user) => ({
        user_id: user.user_id, username: user.username, full_name: user.full_name, role: user.role,
      }))
      .sort((a, b) => String(a.full_name).localeCompare(String(b.full_name)))
  }
  if (method === 'POST' && match(path, '/api/auth/logout')) { session = null; return { ok: true } }
  if (method === 'GET' && match(path, '/api/auth/me')) return publicUser(requireUser())

  requireUser()

  // ------------------------------------------------- prefill, numbering, scan
  if ((m = match(path, '/api/prefill/qc/:id')) && method === 'GET') {
    return prefillQc(Number(m[0]), (orderId, values) => validateQc(orderId, values))
  }
  if ((m = match(path, '/api/prefill/:operation')) && method === 'GET') {
    return prefillOperation(
      m[0],
      {
        order_id: params.order_id ? Number(params.order_id) : null,
        plant_id: plantId,
        trailer_number: params.trailer_number ?? null,
      },
      (filters) => balances(filters),
      (orderId) => hydrateOrder(byId.order().get(orderId)!),
    )
  }
  if ((m = match(path, '/api/trailers/:trailer/history')) && method === 'GET') {
    return {
      trailer_number: m[0],
      last_material_hauled: trailerHistory(m[0], 1)[0]
        ? `${trailerHistory(m[0], 1)[0].material_number} ${trailerHistory(m[0], 1)[0].material_description}`
        : '',
      history: trailerHistory(m[0]),
    }
  }
  if (method === 'POST' && match(path, '/api/numbering/sample')) {
    const orderId = Number(body.order_id)
    const sample = nextSampleNumber(orderId)
    audit('numbering.sample', 'order', orderId, `Generated sample number ${sample}`)
    return { order_id: orderId, sample_number: sample }
  }
  if (method === 'GET' && match(path, '/api/scan')) {
    return resolveScan(String(params.code ?? ''), plantId)
  }

  // ------------------------------------------------------------------ scale
  if (method === 'POST' && match(path, '/api/scale/readings')) {
    requirePermission(requireUser(), 'txn.post')
    return recordScaleReading(body)
  }
  if (method === 'GET' && match(path, '/api/blend/recipes')) {
    requireUser()
    return store.blend_recipe.filter((r) => r.active).map((r) => ({
      ...r,
      yield_pct: r.yield_pct ?? 100,
      vessel_type: r.vessel_type ?? 'Blend',
      method: r.method ?? 'blend',
      outputs: recipeOutputs(r.recipe_id),
      department_code: byId.department().get(r.department_id)?.code ?? null,
      department: byId.department().get(r.department_id)?.description ?? null,
      material_number: byId.material().get(r.material_id)?.number ?? null,
      material_description: byId.material().get(r.material_id)?.description ?? null,
      components: recipeComponents(r.recipe_id),
    }))
  }
  if (method === 'GET' && match(path, '/api/blend/plan')) return blendPlan(params)
  if (method === 'POST' && match(path, '/api/blend/execute')) return blendExecute(body)
  if ((m = match(path, '/api/blend/batches/:id')) && method === 'GET') return blendBatch(m[0])
  if ((m = match(path, '/api/blend/batches/:id/void')) && method === 'POST') {
    if ((store.process_batch ?? []).some((b) => b.batch_id === m![0])) {
      refuse(`${m[0]} is a staged batch; its charges and its draw-off are undone one at a time from the batch, not all at once.`,
        { rule: 'staged_batch' })
    }
    const batchRows = store.inventory_transaction
      .filter((t) => t.batch_id === m![0] && !t.voided && !t.is_reversal)
    if (!batchRows.length) return notFound(`Batch ${m[0]} has nothing left to void.`)
    for (const row of batchRows) voidTransaction(row.transaction_id, String(body.reason ?? ''))
    return blendBatch(m![0])
  }

  if (method === 'GET' && match(path, '/api/scale/latest')) {
    const plant = Number(params.plant_id ?? plantId ?? 1)
    let reading = latestScaleReading(plant, params.trailer_number ?? null)
    // No agent runs in the sandbox, so mint one weigh-out to make the flow real.
    if (!reading) reading = simulateScaleReading(plant, params.trailer_number ?? '')
    return { reading, recent: store.scale_reading.slice(-10).reverse() }
  }

  // ----------------------------------------------------------------- alerts
  if (method === 'GET' && match(path, '/api/alerts')) {
    return {
      alerts: store.alert_log.slice(-Number(params.limit ?? 50)).reverse(),
      settings: alertSettings(),
    }
  }
  if (method === 'POST' && match(path, '/api/alerts/run')) {
    requirePermission(requireUser(), 'support.read')
    const found = evaluateAlerts(
      body.plant_id ?? null,
      () => limsFreshness(),
      (plant, days, limit) => outOfSpec(plant, days, limit),
      (plant) => dataQuality(plant),
      (filters) => balances(filters),
    )
    const result = runAlerts(found, Boolean(body.send))
    if (body.send) recordJobRun('alerts', { alerts_new: result.new.length })
    return result
  }
  if ((m = match(path, '/api/alerts/:id/acknowledge')) && method === 'POST') {
    requirePermission(requireUser(), 'support.read')
    const alert = store.alert_log.find((row) => row.alert_id === Number(m![0]))
    if (!alert) return notFound(`Alert ${m[0]} was not found.`)
    alert.acknowledged_at = nowIso()
    alert.acknowledged_by = session?.username ?? 'sandbox'
    return alert
  }

  // ------------------------------------------------------------------- jobs
  if (method === 'GET' && match(path, '/api/jobs')) {
    requirePermission(requireUser(), 'support.read')
    return { jobs: jobHealth(), recent: store.job_run.slice(-20).reverse() }
  }
  if ((m = match(path, '/api/jobs/:job/run')) && method === 'POST') {
    requirePermission(requireUser(), 'support.read')
    return runJob(m[0], Boolean(body.dry_run), body.plant_id ?? null)
  }

  // reference
  if (method === 'GET' && match(path, '/api/reference')) return referenceBundle(plantId)
  if (method === 'GET' && match(path, '/api/materials')) return materialsFor(plantId)
  if ((m = match(path, '/api/materials/:id')) && method === 'GET') return materialDetail(Number(m[0]))
  if ((m = match(path, '/api/materials/:id/tests')) && method === 'PUT') {
    const user = requireUser()
    requirePermission(user, 'spec.write')
    const materialId = Number(m[0])
    const analytes: string[] = body.analytes ?? []
    const unknown = analytes.filter((a) => !ANALYTE_LABELS[a])
    if (unknown.length) invalid(`Unknown analytes: ${unknown.join(', ')}.`, { analytes: 'Unknown analyte.' })
    store.material_test = store.material_test.filter((row) => row.material_id !== materialId)
    for (const analyte of analytes) store.material_test.push({ material_id: materialId, analyte, required: 1 })
    audit('spec.tests', 'material', materialId, `Test list set for material ${materialId}`, { analytes })
    return { material_id: materialId, analytes }
  }
  if (method === 'GET' && match(path, '/api/locations')) return locationsFor(plantId)
  if ((m = match(path, '/api/requirements/:type/:id')) && method === 'GET') {
    return requirements(m[0], Number(m[1]))
  }

  // dashboard
  if (method === 'GET' && match(path, '/api/dashboard')) {
    const open = store.order.filter((order) => order.active
      && !byId.status().get(order.status_id)?.is_terminal
      && (!plantId || order.plant_id === plantId))
    const today = todayIso()
    const byType: Record<string, number> = {}
    for (const order of open) {
      const code = byId.orderType().get(order.order_type_id)?.code ?? '?'
      byType[code] = (byType[code] ?? 0) + 1
    }
    const dayAgo = new Date(Date.now() - 86_400_000).toISOString()
    return {
      open_orders: open.length,
      overdue_orders: open.filter((order) => order.due_date < today).length,
      awaiting_shipment: pendingShipments(plantId).length,
      open_by_type: byType,
      transactions_24h: store.inventory_transaction.filter((txn) =>
        !txn.voided && txn.transaction_date >= dayAgo && (!plantId || txn.plant_id === plantId)).length,
      out_of_spec_recent: outOfSpec(plantId, 14, 50).length,
      lims: limsFreshness(),
    }
  }

  // orders
  if (method === 'GET' && match(path, '/api/orders')) return searchOrders(params)
  if (method === 'POST' && match(path, '/api/orders')) {
    const created = createOrders(body)
    return { created, count: created.length }
  }
  if (method === 'POST' && match(path, '/api/orders/close')) {
    return closeOrders(body.order_ids ?? [], Boolean(body.force))
  }
  if ((m = match(path, '/api/orders/:id')) && method === 'GET') return orderDetail(Number(m[0]))
  if ((m = match(path, '/api/orders/:id')) && method === 'PATCH') return updateOrder(Number(m[0]), body)
  if ((m = match(path, '/api/orders/:id/audit')) && method === 'GET') {
    // Everything that happened to the order, not only edits to the order row:
    // its loads, ships, voids and QC are recorded against their own entity,
    // which left this screen permanently empty.
    const id = Number(m[0])
    return store.audit_log
      .filter((entry) => entry.order_id === id
        || (entry.entity === 'order' && entry.entity_id === String(id)))
      .sort((a, b) => b.audit_id - a.audit_id)
  }
  if ((m = match(path, '/api/orders/:id/bol')) && method === 'GET') {
    return billOfLading(Number(m[0]), params.transaction_id ? Number(params.transaction_id) : null)
  }
  if ((m = match(path, '/api/orders/:id/qc')) && method === 'GET') return qcForOrder(Number(m[0]))
  if ((m = match(path, '/api/orders/:id/qc')) && method === 'POST') {
    return saveQc(Number(m[0]), body, null, Boolean(body.acknowledge_warnings))
  }
  if ((m = match(path, '/api/orders/:id/qc/validate')) && method === 'POST') {
    return validateQc(Number(m[0]), body)
  }
  if ((m = match(path, '/api/orders/:id/in-process')) && method === 'GET') return inProcess(Number(m[0]))
  if ((m = match(path, '/api/orders/:id/in-process')) && method === 'POST') {
    return addInProcess(Number(m[0]), body)
  }
  if ((m = match(path, '/api/orders/:id/qa-checklist')) && method === 'GET') return qaChecklists(Number(m[0]))
  if ((m = match(path, '/api/orders/:id/qa-checklist')) && method === 'POST') {
    return saveQaChecklist(Number(m[0]), body, params.stage ?? null)
  }

  // inventory
  if ((m = match(path, '/api/transactions/:operation')) && method === 'POST') {
    return postTransaction(m[0], body)
  }
  if ((m = match(path, '/api/transactions/:id/void')) && method === 'POST') {
    return voidTransaction(Number(m[0]), String(body.reason ?? ''))
  }
  if (method === 'POST' && match(path, '/api/display/token')) {
    requireUser()
    return { token: `sandbox-${Number(body.plant_id)}`, token_id: 1, plant_id: Number(body.plant_id), expires_at: null }
  }
  if (method === 'GET' && match(path, '/api/display/tokens')) {
    requireUser()
    return []      // the sandbox's pop-outs are windows of this tab, not links
  }
  if (method === 'GET' && match(path, '/api/display/tanks')) {
    const fromToken = String(params.token ?? '').match(/^sandbox-(\d+)$/)
    return tankBoard(
      fromToken ? Number(fromToken[1]) : Number(params.plant_id || 1),
      params.department_id ? Number(params.department_id) : null,
    )
  }
  if (method === 'GET' && match(path, '/api/process/vessels')) {
    const user = requireUser()
    requirePlant(user, Number(params.plant_id))
    return processVessels(Number(params.plant_id), params.department_id ? Number(params.department_id) : null)
  }
  if (method === 'GET' && match(path, '/api/process/batches')) {
    const user = requireUser()
    requirePlant(user, Number(params.plant_id))
    return openProcessBatches(Number(params.plant_id), params.department_id ? Number(params.department_id) : null)
  }
  if (method === 'GET' && match(path, '/api/load-blend/plan')) {
    return loadBlendPlan(Number(params.order_id), params.quantity ? Number(params.quantity) : null)
  }
  if ((m = match(path, '/api/load-blend/:id')) && method === 'POST') return loadBlendExecute(Number(m[0]), body)
  if (method === 'GET' && match(path, '/api/process/spur')) {
    const user = requireUser()
    requirePlant(user, Number(params.plant_id))
    return processSpur(Number(params.plant_id))
  }
  if (method === 'POST' && match(path, '/api/process/start')) return processStart(body)
  if ((m = match(path, '/api/process/:id/charge')) && method === 'POST') return processCharge(m[0], body)
  if ((m = match(path, '/api/process/:id/advance')) && method === 'POST') return processAdvance(m[0], String(body.to ?? ''))
  if ((m = match(path, '/api/process/:id/draw')) && method === 'POST') return processDraw(m[0], body)
  if ((m = match(path, '/api/process/:id/cancel')) && method === 'POST') return processCancel(m[0], String(body.reason ?? ''))
  if ((m = match(path, '/api/process/:id')) && method === 'GET') {
    const user = requireUser()
    const batch = processGet(m[0])
    requirePlant(user, batch.plant_id)
    return batch
  }
  if (method === 'GET' && match(path, '/api/departments')) {
    const user = requireUser()
    requirePlant(user, Number(params.plant_id))
    return departmentsForPlant(Number(params.plant_id))
  }
  if (method === 'GET' && match(path, '/api/balances')) {
    return balances({
      plant_id: plantId, location_id: params.location_id ? Number(params.location_id) : null,
      material_id: params.material_id ? Number(params.material_id) : null,
      as_of: params.as_of ?? null, include_zero: Boolean(params.include_zero),
    })
  }
  if (method === 'GET' && match(path, '/api/activity')) return activity(params)
  if (method === 'GET' && match(path, '/api/shipments/pending')) {
    return pendingShipments(plantId, params.order_id ? Number(params.order_id) : null)
  }
  if ((m = match(path, '/api/shipments/:id/ship')) && method === 'POST') {
    return ship(Number(m[0]), body.user_date)
  }

  // QC extras
  if (method === 'GET' && match(path, '/api/qc/out-of-spec')) {
    return outOfSpec(plantId, Number(params.days) || 30, Number(params.limit) || 100)
  }
  if ((m = match(path, '/api/qc/:id')) && method === 'PUT') {
    const record = store.qc.find((q) => q.qc_id === Number(m![0]))
    if (!record) return notFound(`QC record ${m[0]} was not found.`)
    return saveQc(record.order_id, body, record.qc_id, Boolean(body.acknowledge_warnings))
  }

  // specs
  if (method === 'GET' && match(path, '/api/specs')) {
    return listSpecs(params.family ?? null, Boolean(params.needs_review))
  }
  if (method === 'PUT' && match(path, '/api/specs')) return upsertSpec(body)

  // LIMS
  if (method === 'GET' && match(path, '/api/lims/freshness')) return limsFreshness()
  if (method === 'POST' && match(path, '/api/lims/matrix')) {
    return limsMatrix(body.sample_codes ?? [], body.selections ?? null)
  }
  if (method === 'GET' && match(path, '/api/lims/tests')) {
    const grouped = new Map<string, Row>()
    for (const row of store.lims_result.filter(reportable)) {
      const current = grouped.get(row.test_code) ?? { test_code: row.test_code, components: new Set(), results: 0 }
      current.components.add(row.component)
      current.results += 1
      grouped.set(row.test_code, current)
    }
    return [...grouped.values()]
      .map((row) => ({ test_code: row.test_code, components: row.components.size, results: row.results }))
      .sort((a, b) => a.test_code.localeCompare(b.test_code))
  }
  if ((m = match(path, '/api/lims/sample/:code')) && method === 'GET') {
    return {
      sample_code: m[0],
      results: store.lims_result.filter((row) => row.sample_code === m![0] && reportable(row)),
      freshness: limsFreshness(),
    }
  }

  // inquiry
  if ((m = match(path, '/api/inquiry/:tab')) && method === 'POST') {
    const result = inquiry(m[0], body)
    return { ...result, row_count: result.rows.length }
  }
  if ((m = match(path, '/api/inquiry/:tab/csv')) && method === 'POST') {
    const result = inquiry(m[0], body)
    return toCsv(result.rows, result.columns.map((c: Row) => c.name))
  }

  // reports
  if ((m = match(path, '/api/reports/:name')) && method === 'POST') return runReport(m[0], body, requireUser())
  if ((m = match(path, '/api/reports/:name/csv')) && method === 'POST') {
    const table = reportTable(runReport(m[0], body, requireUser()))
    return toCsv(table.rows, table.columns)
  }

  // custom query
  if (method === 'GET' && match(path, '/api/query/catalogue')) return queryCatalogue()
  if (method === 'POST' && match(path, '/api/query/run')) {
    return runQuery(body.definition ?? body, body.prompts ?? {})
  }
  if (method === 'POST' && match(path, '/api/query/run/csv')) {
    const result = runQuery(body.definition ?? body, body.prompts ?? {})
    return toCsv(result.rows, result.columns.map((c: Row) => c.name))
  }
  if (method === 'GET' && match(path, '/api/query/saved')) {
    return store.saved_query
      .filter((query) => query.active)
      .map(({ query_id, name, description, added_by, date_added, active }) =>
        ({ query_id, name, description, added_by, date_added, active }))
  }
  if (method === 'POST' && match(path, '/api/query/saved')) {
    const user = requireUser()
    requirePermission(user, 'query.run')
    if (!String(body.name ?? '').trim()) invalid('Give the query a name.', { name: 'Required.' })
    const queryId = nextId('saved_query', 'query_id')
    const row = {
      query_id: queryId, name: body.name.trim(), description: body.description ?? '',
      definition: JSON.stringify(body.definition), shared: 1, active: 1,
      added_by: user.username, date_added: nowIso(),
    }
    store.saved_query.push(row)
    audit('query.save', 'saved_query', queryId, `Saved query '${row.name}'`)
    return { ...row, definition: body.definition, prompts: [] }
  }
  if ((m = match(path, '/api/query/saved/:id')) && method === 'GET') {
    const row = store.saved_query.find((query) => query.query_id === Number(m![0]))
    if (!row) return notFound(`Saved query ${m[0]} was not found.`)
    const definition = typeof row.definition === 'string' ? JSON.parse(row.definition) : row.definition
    return {
      ...row,
      definition,
      prompts: (definition.filters ?? [])
        .filter((filter: Row) => filter.prompt)
        .map((filter: Row) => ({ key: filter.prompt_key || filter.field, field: filter.field })),
    }
  }
  if ((m = match(path, '/api/query/saved/:id')) && method === 'DELETE') {
    const row = store.saved_query.find((query) => query.query_id === Number(m![0]))
    if (row) row.active = 0
    return { ok: true }
  }

  // support
  if (method === 'GET' && match(path, '/api/support/diagnostics')) {
    requirePermission(requireUser(), 'support.read')
    return diagnostics()
  }
  if (method === 'GET' && match(path, '/api/support/data-quality')) {
    requirePermission(requireUser(), 'support.read')
    return dataQuality(plantId)
  }
  if (method === 'GET' && match(path, '/api/support/audit')) {
    requirePermission(requireUser(), 'support.read')
    let rows = store.audit_log.slice().sort((a, b) => b.audit_id - a.audit_id)
    if (params.username) rows = rows.filter((entry) => entry.username === params.username)
    return rows.slice(0, Number(params.limit) || 100)
  }
  if (method === 'GET' && match(path, '/api/support/errors')) {
    requirePermission(requireUser(), 'support.read')
    return {
      errors: errorLog.slice(-Number(params.limit || 50)).reverse(),
      slow_requests: [],
      counters: { requests: requestCount, client_errors: errorLog.length },
    }
  }

  fail(404, 'not_found', `No handler for ${method} ${path.split('?')[0]} in the sandbox build.`)
}
