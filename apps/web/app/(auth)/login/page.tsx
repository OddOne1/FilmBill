'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/api'
import { getAccessToken } from '@/lib/auth'
import { LoginForm } from '@/components/auth/login-form'
import type { SetupStatus } from '@/types'

export default function LoginPage() {
  const router = useRouter()

  useEffect(() => {
    // Redirect to setup if first-time setup is needed
    async function checkSetup() {
      try {
        const status = await api.get<SetupStatus>('/setup/status')
        if (status.needs_setup) {
          router.replace('/setup')
        }
      } catch {
        // ignore — proceed to show login
      }
    }

    // If already authenticated, set cookie and redirect to dashboard
    const token = getAccessToken()
    if (token) {
      document.cookie = `ff_access_token=${token}; path=/; max-age=${60 * 60 * 24 * 7}; SameSite=Lax`
      // Check the 'from' param for redirect target
      const params = new URLSearchParams(window.location.search)
      const from = params.get('from')
      router.replace(from || '/projects')
      return
    }

    checkSetup()
  }, [router])

  return <LoginForm />
}
