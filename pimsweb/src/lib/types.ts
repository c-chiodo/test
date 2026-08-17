/* Shapes the API returns. Kept deliberately loose where the server sends a
 * wide row (grids render whatever columns they are given) and precise where a
 * screen depends on the field. */

export interface Plant { plant_id: number; code: string; name: string }
export interface NamedId { [key: string]: any }

export interface User {
  user_id: number
  username: string
  full_name: string
  email: string
  role: string
  plants: Plant[]
  permissions: string[]
}

export interface Material {
  material_id: number
  number: string
  description: string
  family: string
  material_type?: string
  density?: number
}

export interface Location {
  location_id: number
  plant_id: number
  plant_code?: string
  number: string
  description: string
  location_type: string
  max_capacity: number | null
  bol_required?: number
}

export interface Reference {
  plants: Plant[]
  companies: { company_id: number; name: string }[]
  departments: { department_id: number; code: string; description: string }[]
  order_types: { order_type_id: number; code: string; description: string }[]
  statuses: { status_id: number; name: string; is_terminal: number }[]
  transaction_types: { transaction_type_id: number; code: string; description: string }[]
  materials: Material[]
  locations: Location[]
  customers: { customer_id: number; name: string; gp_custnmbr: string }[]
  vendors: { vendor_id: number; name: string; gp_vendorid: string }[]
  test_points: { test_point_id: number; plant_id: number; name: string }[]
  qa_questions: {
    question_id: number
    question: string
    answer_type: string
    /** 'pre_load' questions inspect an empty trailer and are asked first. */
    stage: string
  }[]
  analytes: { key: string; label: string; unit: string }[]
}

export interface Order {
  order_id: number
  order_type: string
  order_type_id: number
  order_date: string
  due_date: string
  plant_id: number
  plant_code: string
  department_code: string | null
  status: string
  status_id: number
  is_terminal: number
  material_one_id: number | null
  material_one_number: string | null
  material_one_description: string | null
  material_one_quantity: number
  customer_name: string | null
  vendor_name: string | null
  customer_id: number | null
  vendor_id: number | null
  order_reference: string
  blend_serial_number: string
  trailer_number: string
  ship_method: string
  comments: string
  qty_fulfilled: number
  qty_shipped: number
  percent_complete: number
  added_by: string
  date_added: string
  [key: string]: any
}

export interface Evaluation {
  analyte: string
  label: string
  unit: string
  value: number | null
  min_value: number | null
  max_value: number | null
  required: boolean
  verdict: 'in_spec' | 'out_of_spec' | 'missing' | 'not_run' | 'no_spec'
  needs_review: boolean
  note: string
}

export interface QcWarning {
  field: string | null
  analyte: string | null
  severity: 'warning' | 'out_of_spec'
  message: string
}

export interface QcValidation {
  order_id: number
  material: { number: string; description: string; family: string }
  required_tests: string[]
  not_tested: string[]
  evaluations: Evaluation[]
  warnings: QcWarning[]
  summary: { status: string; out_of_spec: string[]; missing_required: string[] }
}

export interface QcRecord {
  qc_id: number
  order_id: number
  test_date: string
  sample_number: string
  bol_number: string
  moisture: number | null
  temp: number | null
  ph: number | null
  ffa: number | null
  tfa: number | null
  spintest_fallout: number | null
  flash_pf: string | null
  steam_on: number
  seal_number: string
  last_material_hauled: string
  comments: string
  performed_by: string
  evaluations: Evaluation[]
  spec_summary: { status: string; out_of_spec: string[]; missing_required: string[] }
  warnings?: QcWarning[]
  [key: string]: any
}

export interface Balance {
  location_id: number
  location_number: string
  location_description: string
  location_type: string
  plant_code: string
  plant_id: number
  material_id: number
  material_number: string
  material_description: string
  balance: number
  max_capacity: number | null
  percent_full: number | null
}

export interface Transaction {
  transaction_id: number
  transaction_type: string
  order_id: number | null
  plant_code: string
  user_date: string
  transaction_date: string
  username: string
  from_material_number: string | null
  from_location_number: string | null
  from_qty: number
  to_material_number: string | null
  to_location_number: string | null
  to_qty: number
  trailer_number: string
  remarks: string
  voided: number
  is_reversal: number
  /** Whether the signed-in user may reverse this row, and why not if they cannot. */
  can_void?: boolean
  void_blocked?: string
  [key: string]: any
}

export interface PendingShipment {
  stage_id: number
  order_id: number
  trailer_number: string
  quantity: number
  customer_name: string | null
  bol_number: string
  transaction_id: number
  /** What is actually on the trailer, read from the load. */
  material_number: string | null
  material_description: string | null
  /** What the order header says, so a disagreement can be shown. */
  order_material_number: string | null
  loaded_by: string | null
  plant_code: string
  loaded_at: string
}

export interface Diagnostics {
  status: 'ok' | 'degraded' | 'failed'
  version: string
  environment: string
  checked_at: string
  checks: Record<string, any>
  runtime: { started_at: string; uptime_seconds: number }
  configuration: Record<string, unknown>
}

export interface DataQuality {
  status: string
  total_findings: number
  generated_at: string
  findings: {
    key: string
    label: string
    severity: string
    count: number
    rows: Record<string, any>[]
    action: string
  }[]
}

export interface AuditEntry {
  audit_id: number
  occurred_at: string
  username: string
  action: string
  entity: string
  entity_id: string
  summary: string
  detail: Record<string, any>
}

export interface QueryColumn { name: string; label: string; type: string }

export interface QueryResult {
  columns: QueryColumn[]
  rows: Record<string, any>[]
  row_count: number
  truncated: boolean
  sql: string
  parameters: unknown[]
}
