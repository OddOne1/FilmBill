import { redirect } from 'next/navigation'

// Profile, not Admin: /settings is reachable by every user, and the one page
// in this section that everyone can open is their own profile.
export default function SettingsPage() {
  redirect('/settings/profile')
}
