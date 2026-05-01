import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { api } from '../lib/api'

const schema = z.object({
  email: z.string().email('Enter a valid email'),
})
type FormData = z.infer<typeof schema>

export default function ForgotPassword() {
  const [submitted, setSubmitted] = useState(false)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormData>({ resolver: zodResolver(schema) })

  async function onSubmit(data: FormData) {
    await api.auth.forgotPassword(data.email)
    setSubmitted(true)
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-gradient-to-br from-gray-900 via-gray-800 to-gray-900 px-4">
      <div className="w-full max-w-sm bg-white/95 backdrop-blur rounded-2xl shadow-2xl p-8">
        <h1 className="text-2xl font-bold text-gray-900 mb-1">Reset password</h1>

        {submitted ? (
          <>
            <p className="text-sm text-gray-600 mt-2">
              If an account exists for that email, we've sent a password reset link. Check your inbox.
            </p>
            <Link
              to="/login"
              className="block text-center text-sm text-emerald-600 hover:underline font-medium mt-6"
            >
              Back to sign in
            </Link>
          </>
        ) : (
          <>
            <p className="text-sm text-gray-500 mb-6">
              Enter your email and we'll send you a reset link.
            </p>

            <form onSubmit={handleSubmit(onSubmit)} noValidate className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
                <input
                  {...register('email')}
                  type="email"
                  autoFocus
                  placeholder="you@example.com"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                />
                {errors.email && <p className="text-xs text-red-500 mt-1">{errors.email.message}</p>}
              </div>

              <button
                type="submit"
                disabled={isSubmitting}
                className="w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-medium rounded-lg py-2 text-sm transition-colors"
              >
                {isSubmitting ? 'Sending...' : 'Send reset link'}
              </button>
            </form>

            <p className="text-sm text-gray-500 text-center mt-6">
              <Link to="/login" className="text-emerald-600 hover:underline font-medium">
                Back to sign in
              </Link>
            </p>
          </>
        )}
      </div>
    </div>
  )
}
