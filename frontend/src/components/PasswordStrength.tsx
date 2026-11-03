/**
 * Password requirements, stated before they are violated.
 *
 * Signup enforced an 8-character minimum but only said so AFTER a failed
 * submit. Rules you discover by breaking them are worse than no rules: the
 * user has already chosen a password, been refused, and has to start again
 * without knowing what else might be wrong.
 *
 * Deliberately NOT a score out of four. A meter that says "weak" without
 * saying what would fix it is decoration; a checklist is actionable.
 */

export interface PasswordRule {
  label: string
  met: boolean
}

export function rulesFor(password: string): PasswordRule[] {
  return [
    { label: 'At least 8 characters', met: password.length >= 8 },
    { label: 'A letter', met: /[a-zA-Z]/.test(password) },
    { label: 'A number or symbol', met: /[^a-zA-Z]/.test(password) },
  ]
}

export default function PasswordStrength({ password }: { password: string }) {
  const rules = rulesFor(password)
  const met = rules.filter((r) => r.met).length

  return (
    <ul className="mt-2 space-y-1" aria-live="polite">
      {rules.map((rule) => (
        <li
          key={rule.label}
          className={`text-xs flex items-center gap-1.5 ${
            rule.met ? 'text-accent-600' : 'text-gray-400'
          }`}
        >
          <span aria-hidden>{rule.met ? '✓' : '○'}</span>
          {rule.label}
        </li>
      ))}
      <li className="sr-only">
        {met} of {rules.length} password requirements met
      </li>
    </ul>
  )
}
