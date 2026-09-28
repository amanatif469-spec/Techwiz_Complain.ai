import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api } from './api'
import type { Complaint, Metrics, Role, User } from './types'

interface AppState {
  user: User | null
  role: Role | null
  authenticated: boolean
  loading: boolean
  complaints: Complaint[]
  metrics: Metrics | null
  sidebarOpen: boolean
  setSidebarOpen: (value: boolean) => void
  login: (email: string, password: string) => Promise<void>
  restore: () => Promise<void>
  logout: () => void
  loadComplaints: (query?: string) => Promise<void>
  loadMetrics: () => Promise<void>
}

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      user: null,
      // Role always comes from the server (/auth/me), never from browser storage.
      role: null,
      authenticated: Boolean(localStorage.getItem('supportnova_token')),
      loading: false,
      complaints: [],
      metrics: null,
      sidebarOpen: false,
      setSidebarOpen: (sidebarOpen) => set({ sidebarOpen }),
      login: async (email, password) => {
        set({ loading: true })
        try {
          await api.login(email, password)
          const user = await api.me()
          set({ user, role: user.role, authenticated: true })
        } finally {
          set({ loading: false })
        }
      },
      restore: async () => {
        if (!localStorage.getItem('supportnova_token')) {
          if (get().authenticated) get().logout()
          return
        }
        try {
          const user = await api.me()
          set({ user, role: user.role, authenticated: true })
        } catch {
          get().logout()
        }
      },
      logout: () => {
        localStorage.removeItem('supportnova_token')
        localStorage.removeItem('supportnova_role')
        set({ user: null, role: null, authenticated: false, complaints: [], metrics: null })
      },
      loadComplaints: async (query = '') => {
        set({ loading: true })
        try {
          set({ complaints: await api.complaints(query) })
        } finally {
          set({ loading: false })
        }
      },
      loadMetrics: async () => {
        const role = get().role
        // Admins use the admin dashboard; managers get the same metrics from analytics.
        set({ metrics: role === 'administrator' ? await api.adminMetrics() : await api.analytics() })
      },
    }),
    {
      name: 'supportnova-ui',
      partialize: (state) => ({ authenticated: state.authenticated }),
    },
  ),
)
