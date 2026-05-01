import { ApiError } from './api'

export function getErrorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Something went wrong.'
}
