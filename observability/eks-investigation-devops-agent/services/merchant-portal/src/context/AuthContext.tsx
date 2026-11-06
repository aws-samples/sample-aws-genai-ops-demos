import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { 
  signIn, 
  signOut, 
  getCurrentUser, 
  fetchAuthSession,
  AuthUser
} from 'aws-amplify/auth'

interface AuthContextType {
  user: AuthUser | null
  isAuthenticated: boolean
  isLoading: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  getAccessToken: () => Promise<string | null>
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    checkAuth()
  }, [])

  async function checkAuth() {
    try {
      const currentUser = await getCurrentUser()
      setUser(currentUser)
    } catch {
      setUser(null)
    } finally {
      setIsLoading(false)
    }
  }

  async function login(username: string, password: string) {
    const result = await signIn({ username, password })
    if (result.isSignedIn) {
      const currentUser = await getCurrentUser()
      setUser(currentUser)
    } else {
      throw new Error(`Sign in requires another step: ${result.nextStep.signInStep}`)
    }
  }

  async function logout() {
    await signOut()
    setUser(null)
  }

  async function getAccessToken(): Promise<string | null> {
    try {
      const session = await fetchAuthSession()
      return session.tokens?.accessToken?.toString() || null
    } catch {
      return null
    }
  }

  const value: AuthContextType = {
    user,
    isAuthenticated: !!user,
    isLoading,
    login,
    logout,
    getAccessToken,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
