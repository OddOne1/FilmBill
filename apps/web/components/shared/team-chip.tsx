'use client'

/**
 * R2. "Team": this contact is linked to a login of the company — a team
 * member who also works as crew. Only a mark: rates, roles, availability
 * and booking are the same as for any freelancer.
 */
export function TeamChip({ title }: { title?: string }) {
  return (
    <span
      data-testid="team-chip"
      title={title ?? 'A team member: this contact has a login'}
      className="ml-2 rounded bg-accent/15 px-1.5 py-0.5 text-2xs font-medium text-accent"
    >
      Team
    </span>
  )
}
