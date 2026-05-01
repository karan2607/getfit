import { createContext, useContext } from 'react'
import type { User } from '../lib/api'

export interface AuthContextValue {
  user: User | null
  isLoading: boolean
  login: (token: string, user: User) => void
  logout: () => void
  setUser: (user: User) => void
}

export const AuthContext = createContext<AuthContextValue>({
  user: null,
  isLoading: true,
  login: () => {},
  logout: () => {},
  setUser: () => {},
})

export function useAuth() {
  return useContext(AuthContext)
}
