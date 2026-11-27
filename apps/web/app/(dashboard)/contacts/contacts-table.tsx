'use client'

import { TeamChip } from '@/components/shared/team-chip'
import * as React from 'react'
import Link from 'next/link'
import { Columns3 } from 'lucide-react'
import { countryLabel } from '@/lib/countries'
import type { Party } from '@/types'

/**
 * The Contacts list as a table that uses the whole window (P1b-4 part 4).
 *
 * **Columns drop as the window narrows, in a fixed order**: tags and city
 * first, then phone, then email (and the number, country and the internal
 * columns with them). Name, type and the role chips never drop. The drop is
 * CSS breakpoints on each column (`DROP`), so it needs no measuring.
 *
 * **Role chips have a fixed height, never wrap and never balloon.** At most
 * `MAX_CHIPS` show; the rest become "+N", with every role in its tooltip.
 *
 * **Internal columns** — VAT check state, payment terms, tax status, last
 * check date — exist only for somebody holding `parties.edit` [Guessing,
 * SCOPE]. The API withholds those values from a list served to anybody else,
 * so this is not the only thing keeping them off the screen.
 */

export type ColumnKey =
  | 'number'
  | 'name'
  | 'type'
  | 'roles'
  | 'country'
  | 'city'
  | 'email'
  | 'phone'
  | 'tags'
  | 'vat'
  | 'payment'
  | 'tax_status'
  | 'last_check'

export interface Column {
  key: ColumnKey
  label: string
  /** Always shown, and not in the picker. */
  fixed?: boolean
  /** Only for `parties.edit`. */
  restricted?: boolean
}

export const COLUMNS: Column[] = [
  { key: 'number', label: 'Number' },
  { key: 'name', label: 'Name', fixed: true },
  { key: 'type', label: 'Type', fixed: true },
  { key: 'roles', label: 'Roles', fixed: true },
  { key: 'country', label: 'Country' },
  { key: 'city', label: 'City' },
  { key: 'email', label: 'Email' },
  { key: 'phone', label: 'Phone' },
  { key: 'tags', label: 'Tags' },
  { key: 'vat', label: 'VAT status', restricted: true },
  { key: 'payment', label: 'Payment terms', restricted: true },
  { key: 'tax_status', label: 'Tax status', restricted: true },
  { key: 'last_check', label: 'Last check', restricted: true },
]

/** The breakpoint below which a column is dropped. Tags and city go first
 *  (below 2xl), then phone (below xl), then email (below lg). */
export const DROP: Record<ColumnKey, string> = {
  name: '',
  type: '',
  roles: '',
  number: 'hidden md:table-cell',
  country: 'hidden lg:table-cell',
  email: 'hidden lg:table-cell',
  phone: 'hidden xl:table-cell',
  city: 'hidden 2xl:table-cell',
  tags: 'hidden 2xl:table-cell',
  vat: 'hidden md:table-cell',
  payment: 'hidden xl:table-cell',
  tax_status: 'hidden xl:table-cell',
  last_check: 'hidden 2xl:table-cell',
}

export const MAX_CHIPS = 2

const STORAGE_KEY = 'filmbill.contacts.hidden-columns'

/** The columns this browser hid. A per-viewer convenience only: storage
 *  that is full, disabled or private-mode-blocked must never break the list,
 *  so every access is wrapped. */
export function loadHiddenColumns(): ColumnKey[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? (parsed.filter((k) => typeof k === 'string') as ColumnKey[]) : []
  } catch {
    return []
  }
}

function saveHiddenColumns(hidden: ColumnKey[]) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(hidden))
  } catch {
    // Not remembered; the choice still applies until the page is left.
  }
}

const TONES = {
  neutral: 'border-border text-text-tertiary',
  success: 'border-status-success/40 text-status-success',
  warning: 'border-status-warning/40 text-status-warning',
  error: 'border-status-error/40 text-status-error',
}

/** Fixed height, one line, never shrinks into a taller shape. */
export const CHIP_CLASS =
  'inline-flex h-5 shrink-0 items-center whitespace-nowrap rounded-full border px-2 text-[11px] leading-none'

export function Chip({
  children,
  tone = 'neutral',
  testId,
  title,
}: {
  children: React.ReactNode
  tone?: keyof typeof TONES
  testId?: string
  title?: string
}) {
  return (
    <span data-testid={testId} title={title} className={`${CHIP_CLASS} ${TONES[tone]}`}>
      {children}
    </span>
  )
}

function roleNames(party: Party): string[] {
  return [
    party.is_customer && 'Customer',
    party.is_supplier && 'Supplier',
    party.is_partner && 'Partner',
    party.is_crew && 'Crew',
  ].filter(Boolean) as string[]
}

/** The role chips: at most `MAX_CHIPS`, then "+N" with the full list. */
export function RoleChips({ party }: { party: Party }) {
  const roles = roleNames(party)
  const shown = roles.slice(0, MAX_CHIPS)
  const rest = roles.length - shown.length
  return (
    <span
      data-testid={`party-roles-${party.id}`}
      className="flex flex-nowrap items-center gap-1 overflow-hidden"
    >
      {shown.map((role) => (
        <Chip key={role}>{role}</Chip>
      ))}
      {rest > 0 && (
        <Chip testId={`party-roles-more-${party.id}`} title={roles.join(', ')}>
          +{rest}
        </Chip>
      )}
    </span>
  )
}

const TYPE_LABELS: Record<string, string> = {
  organisation: 'Organisation',
  department: 'Department',
  person: 'Person',
}

const CHECK_LABELS: Record<string, [string, keyof typeof TONES]> = {
  valid: ['Confirmed', 'success'],
  invalid: ['Not valid', 'error'],
  unavailable: ['No answer', 'warning'],
  not_configured: ['Not set up', 'neutral'],
  not_checkable: ['Not checkable', 'neutral'],
}

const TAX_STATUS: Record<string, string> = {
  business: 'Business',
  private: 'Private',
}

function cell(party: Party, key: ColumnKey): React.ReactNode {
  switch (key) {
    case 'number':
      return <span className="font-mono text-xs text-text-tertiary">{party.party_number ?? '—'}</span>
    case 'name':
      return (
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <Link
              href={`/contacts/${party.id}`}
              data-testid={`party-row-${party.id}`}
              className="truncate text-sm font-medium text-text-primary hover:underline"
            >
              {party.name}
            </Link>
            {party.is_team_member && <TeamChip />}
            {party.trading_name && (
              <span className="truncate text-xs text-text-tertiary">({party.trading_name})</span>
            )}
            {!party.is_active && (
              <Chip tone="warning" testId={`party-chip-archived-${party.id}`}>
                Archived
              </Chip>
            )}
            {/* A reason somebody should look — a registry's answer changed,
                or a name was never split. Shown on every tab, not only the
                worklist. */}
            {(party.needs_attention_reasons ?? []).length > 0 && (
              <Chip tone="warning" testId={`party-chip-attention-${party.id}`}>
                {party.name_not_split && (party.needs_attention_reasons ?? []).length === 1
                  ? 'Name not split'
                  : 'Needs attention'}
              </Chip>
            )}
          </div>
          {party.name_addition && (
            <div
              data-testid={`party-name-addition-${party.id}`}
              className="truncate text-xs text-text-tertiary"
            >
              {party.name_addition}
            </div>
          )}
        </div>
      )
    case 'type':
      return <span className="whitespace-nowrap text-xs text-text-tertiary">{TYPE_LABELS[party.type] ?? party.type}</span>
    case 'roles':
      return <RoleChips party={party} />
    case 'country':
      return <span className="whitespace-nowrap text-xs">{party.country ? countryLabel(party.country) : '—'}</span>
    case 'city':
      return <span className="text-xs">{party.city ?? '—'}</span>
    case 'email':
      return <span className="block max-w-[16rem] truncate text-xs">{party.primary_email ?? '—'}</span>
    case 'phone':
      return <span className="whitespace-nowrap text-xs">{party.primary_phone ?? '—'}</span>
    case 'tags':
      return (
        <span className="block max-w-[12rem] truncate text-xs text-text-tertiary" title={(party.tags ?? []).join(', ')}>
          {(party.tags ?? []).join(', ') || '—'}
        </span>
      )
    case 'vat': {
      if (party.vat_verified) {
        return (
          <Chip tone="success" testId={`party-chip-verified-${party.id}`}>
            VAT checked
          </Chip>
        )
      }
      const status = party.latest_vat_check?.status
      if (!status) return <span className="text-xs text-text-tertiary">—</span>
      const [label, tone] = CHECK_LABELS[status] ?? [status, 'neutral']
      return <Chip tone={tone}>{label}</Chip>
    }
    case 'payment':
      return (
        <span className="whitespace-nowrap text-xs">
          {party.payment_terms_days != null ? `${party.payment_terms_days} days` : '—'}
        </span>
      )
    case 'tax_status':
      return <span className="text-xs">{TAX_STATUS[party.buyer_type ?? ''] ?? '—'}</span>
    case 'last_check':
      return (
        <span className="whitespace-nowrap text-xs">
          {party.last_check_at ? new Date(party.last_check_at).toLocaleDateString() : '—'}
        </span>
      )
  }
}

/** The columns a viewer may see at all, before their own choice. */
export function availableColumns(canEdit: boolean): Column[] {
  return COLUMNS.filter((column) => canEdit || !column.restricted)
}

export function ColumnPicker({
  columns,
  hidden,
  onChange,
}: {
  columns: Column[]
  hidden: ColumnKey[]
  onChange: (hidden: ColumnKey[]) => void
}) {
  const [open, setOpen] = React.useState(false)
  return (
    <div className="relative">
      <button
        type="button"
        data-testid="contacts-columns"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1.5 text-xs text-text-secondary hover:bg-bg-hover"
      >
        <Columns3 className="h-3.5 w-3.5" /> Columns
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Columns"
          data-testid="contacts-columns-panel"
          className="absolute right-0 z-20 mt-1 w-56 rounded-md border border-border bg-bg-primary p-2 shadow-lg"
        >
          <p className="px-1 pb-1 text-[11px] text-text-tertiary">
            Remembered in this browser. Name, type and roles always show.
          </p>
          {columns
            .filter((column) => !column.fixed)
            .map((column) => (
              <label
                key={column.key}
                className="flex items-center gap-2 rounded px-1 py-1 text-sm text-text-secondary hover:bg-bg-hover"
              >
                <input
                  type="checkbox"
                  data-testid={`contacts-column-${column.key}`}
                  checked={!hidden.includes(column.key)}
                  onChange={(event) => {
                    const next = event.target.checked
                      ? hidden.filter((key) => key !== column.key)
                      : [...hidden, column.key]
                    saveHiddenColumns(next)
                    onChange(next)
                  }}
                />
                {column.label}
              </label>
            ))}
        </div>
      )}
    </div>
  )
}

export function ContactsTable({
  parties,
  columns,
}: {
  parties: Party[]
  columns: Column[]
}) {
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table data-testid="contacts-table" className="w-full table-auto text-left text-sm">
        <thead className="bg-bg-secondary text-xs text-text-tertiary">
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                data-testid={`contacts-th-${column.key}`}
                className={`whitespace-nowrap px-3 py-2 font-medium ${DROP[column.key]}`}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {parties.map((party) => (
            <tr
              key={party.id}
              data-testid={`contacts-tr-${party.id}`}
              className="border-t border-border align-middle hover:bg-bg-hover/50"
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  data-testid={`contacts-td-${column.key}-${party.id}`}
                  className={`px-3 py-2 ${column.key === 'name' ? 'max-w-[28rem]' : ''} ${DROP[column.key]}`}
                >
                  {cell(party, column.key)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
