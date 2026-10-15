/**
 * Single source of truth for weight units and per-measurement-type set fields.
 *
 * Why this exists
 * ---------------
 * `KG_PER_LB` was previously declared three separate times in Workouts.tsx, and
 * the reverse conversion was written two different ways (`* 2.20462` in one place,
 * `/ KG_PER_LB` in another). Worse, the write path converted by FIELD NAME --
 * anything called `weight_kg` got multiplied by KG_PER_LB -- so duration
 * exercises (which stored seconds in `weight_kg`) had their seconds silently
 * scaled, while the read path skipped the conversion. A 60-second plank was
 * persisted as 27.2 and read back as 27.2.
 *
 * Rules:
 *   - The wire format and the database are ALWAYS kilograms. Never send lb.
 *   - Convert only at the display edge, via toDisplayWeight / toStorageWeight.
 *   - Decide which field to read/write from the measurement type, never the name.
 *
 * Mirrors backend/api/exercise_types.py -- keep the two in step.
 */

import type { MeasurementType } from './api'

export const KG_PER_LB = 0.453592

export type WeightUnit = 'kg' | 'lb'

/** Storage (kg) -> the unit the user reads. */
export function toDisplayWeight(kg: number | null | undefined, unit: WeightUnit): number | null {
  if (kg === null || kg === undefined) return null
  const value = unit === 'lb' ? kg / KG_PER_LB : kg
  return Math.round(value * 10) / 10
}

/** What the user typed -> storage (kg). */
export function toStorageWeight(value: number | null | undefined, unit: WeightUnit): number | null {
  if (value === null || value === undefined) return null
  return unit === 'lb' ? value * KG_PER_LB : value
}

/** Human-readable duration: 90 -> "1:30", 45 -> "45s". */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 60) return `${seconds}s`
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return secs === 0 ? `${mins}:00` : `${mins}:${String(secs).padStart(2, '0')}`
}

// ---------------------------------------------------------------------------
// Measurement-type contract (mirrors exercise_types.FIELD_CONTRACT)
// ---------------------------------------------------------------------------

export type SetField = 'weight_kg' | 'reps_completed' | 'duration_seconds'

const FIELD_CONTRACT: Record<MeasurementType, readonly SetField[]> = {
  weight_reps: ['weight_kg', 'reps_completed'],
  weight_duration: ['weight_kg', 'duration_seconds'],
  duration: ['duration_seconds'],
  bodyweight_reps: ['reps_completed'],
  warmup: [],
}

/** Which set fields carry meaning for this measurement type. */
export function fieldsFor(measurementType: MeasurementType): readonly SetField[] {
  return FIELD_CONTRACT[measurementType] ?? FIELD_CONTRACT.weight_reps
}

/** True if this field should be rendered/sent for this measurement type. */
export function usesField(measurementType: MeasurementType, field: SetField): boolean {
  return fieldsFor(measurementType).includes(field)
}

/**
 * Column label for a set input.
 * `weight_kg` is the only field whose label depends on the user's unit.
 */
export function fieldLabel(
  field: SetField,
  unit: WeightUnit,
  exerciseName = '',
): string {
  if (field === 'reps_completed') return 'Reps'
  if (field === 'duration_seconds') return 'Time (sec)'
  const n = exerciseName.toLowerCase()
  if (/dumbbell|db /.test(n)) return `Each (${unit})`
  if (/barbell|bb |squat|deadlift|bench press|overhead press|ohp/.test(n)) return `Bar (${unit})`
  if (/cable|machine/.test(n)) return `Stack (${unit})`
  return `Weight (${unit})`
}

/**
 * Convert a stored set value into what the input should show.
 * Only weights are unit-converted; reps and seconds pass through untouched.
 */
export function toDisplayField(
  field: SetField,
  value: number | null | undefined,
  unit: WeightUnit,
): number | null {
  if (value === null || value === undefined) return null
  return field === 'weight_kg' ? toDisplayWeight(value, unit) : value
}

/** Convert a typed-in set value into storage form. */
export function toStorageField(
  field: SetField,
  value: number | null | undefined,
  unit: WeightUnit,
): number | null {
  if (value === null || value === undefined) return null
  if (field === 'weight_kg') return toStorageWeight(value, unit)
  return Math.max(0, Math.round(value))
}

// ---------------------------------------------------------------------------
// Chart contract (mirrors exercise_types.CHART_CONTRACT)
// ---------------------------------------------------------------------------

export type ChartUnit = 'mass' | 'seconds' | 'reps' | null

export interface ChartSpec {
  metric: SetField | null
  unit: ChartUnit
}

const CHART_CONTRACT: Record<MeasurementType, ChartSpec> = {
  weight_reps: { metric: 'weight_kg', unit: 'mass' },
  weight_duration: { metric: 'weight_kg', unit: 'mass' },
  duration: { metric: 'duration_seconds', unit: 'seconds' },
  bodyweight_reps: { metric: 'reps_completed', unit: 'reps' },
  warmup: { metric: null, unit: null },
}

export function chartSpecFor(measurementType: MeasurementType): ChartSpec {
  return CHART_CONTRACT[measurementType] ?? CHART_CONTRACT.weight_reps
}

/**
 * Is the plan's free-text prescription enterable given the measurement type?
 *
 * Mirrors exercise_types.prescription_matches_type. A rep-typed exercise
 * prescribed in minutes ("Burpees: 10 min"), or a timed one prescribed in yards
 * ("Farmer's Carry: 40 yards"), cannot be entered into the fields on screen —
 * so say so rather than leaving the user to guess which box takes which number.
 *
 * Returns null when consistent, or the unit the inputs actually expect.
 */
export function prescriptionMismatch(
  measurementType: MeasurementType,
  reps: string | null | undefined,
): 'reps' | 'time' | null {
  if (!reps || measurementType === 'warmup') return null
  const hasTime = /\b(sec|secs|second|seconds|min|mins|minute|minutes|hour|hours)\b/i.test(reps)
  const hasDistance = /\b(yard|yards|yd|feet|ft|metre|metres|meter|meters|mile|miles|km)\b/i.test(reps)
  const hasNumber = /\d/.test(reps)

  if (measurementType === 'weight_reps' || measurementType === 'bodyweight_reps') {
    return hasTime || hasDistance ? 'reps' : null
  }
  if (measurementType === 'duration' || measurementType === 'weight_duration') {
    if (hasDistance) return 'time'
    if (hasNumber && !hasTime) return 'time'
    return null
  }
  return null
}

/** Axis/heading label for a progress chart, e.g. "Max weight per session (lb)". */
export function chartLabel(measurementType: MeasurementType, unit: WeightUnit): string {
  const spec = chartSpecFor(measurementType)
  if (spec.unit === 'mass') return `Max weight per session (${unit})`
  if (spec.unit === 'seconds') return 'Best hold per session (sec)'
  if (spec.unit === 'reps') return 'Best reps per session'
  return ''
}

/** Format one chart value with its unit, for tooltips and personal bests. */
export function formatChartValue(
  measurementType: MeasurementType,
  value: number | null,
  unit: WeightUnit,
): string {
  if (value === null || value === undefined) return '—'
  const spec = chartSpecFor(measurementType)
  if (spec.unit === 'mass') return `${toDisplayWeight(value, unit)} ${unit}`
  if (spec.unit === 'seconds') return formatDuration(value)
  if (spec.unit === 'reps') return `${value} reps`
  return String(value)
}
