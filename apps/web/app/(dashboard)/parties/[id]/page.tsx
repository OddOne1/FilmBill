'use client'

import * as React from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { AlertTriangle, Check, Loader2, ShieldQuestion } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { usePermissions } from '@/hooks/use-permissions'
import type {
  Party,
  PartyVatCheck,
  ResolvedField,
  ResolvedRecipient,
  TaxProposal,
} from '@/types'
import { VAT_INVALID, VAT_NOT_APPLICABLE, VAT_VALID } from '@/types'

/**
 * One party: Basics · Addresses · Contacts · Hierarchy · Tax · Banking.
 *
 * **Inherited values are greyed and named.** Every field in
 * `GET /parties/{id}/resolved` carries the level it came from, and this
 * screen shows that rather than deriving it: "from ORF — organisation" is
 * the difference between a user understanding why the address is what it is
 * and a user editing the wrong record to change it.
 *
 * **Nothing here computes tax or decides a VAT status.** The tax panel calls
 * `POST /parties/{id}/tax-preview`, which runs the one engine with the buyer
 * side built from the resolved recipient; the VIES chip shows the status the
 * server recorded. The failure this prevents is a screen that says "verified"
 * because it saw a check row, when the check was of a different number.
 */

type Tab = 'basics' | 'addresses' | 'contacts' | 'hierarchy' | 'tax' | 'banking'

const TABS: [Tab, string][] = [
  ['basics', 'Basics'],
  ['addresses', 'Addresses'],
  ['contacts', 'Contacts'],
  ['hierarchy', 'Hierarchy'],
  ['tax', 'Tax'],
  ['banking', 'Banking'],
]

/** The sentence beside each derivation reason, in the server's own terms. */
/** What each kind of identifier is called, in words. */
const TAX_ID_LABELS: Record<string, string> = {
  vat: 'VAT identification number',
  uid_ch: 'Swiss UID',
  abn: 'Australian Business Number',
  ein: 'US EIN',
  tax_number: 'tax number',
  company_register: 'company register number',
  other: 'identifier',
}

const BUYER_REASONS: Record<string, string> = {
  explicit: 'set by hand on this party',
  organisation: 'an organisation, or somebody inside one',
  person_with_vat_id: 'a person with a VAT ID — a sole trader',
  explicit_without_tax_id:
    'set explicitly as a business, with no tax identifier on file',
  person: 'a person with no VAT ID',
}

export default function PartyDetailPage() {
  const params = useParams<{ id: string }>()
  const partyId = params?.id
  const { can, loaded } = usePermissions()
  const canEdit = can('parties.edit')

  const [tab, setTab] = React.useState<Tab>('basics')
  const [party, setParty] = React.useState<Party | null>(null)
  const [resolved, setResolved] = React.useState<ResolvedRecipient | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [loading, setLoading] = React.useState(true)

  const refresh = React.useCallback(
    async (spinner = false) => {
      if (!partyId) return
      if (spinner) setLoading(true)
      try {
        const [loadedParty, loadedResolved] = await Promise.all([
          api.get<Party>(`/parties/${partyId}`),
          api.get<ResolvedRecipient>(`/parties/${partyId}/resolved`),
        ])
        setParty(loadedParty)
        setResolved(loadedResolved)
        setError(null)
      } catch (err) {
        setError(
          err instanceof ApiError ? err.detail : 'Could not load this party',
        )
      } finally {
        setLoading(false)
      }
    },
    [partyId],
  )

  React.useEffect(() => {
    if (!loaded) return
    void refresh(true)
  }, [loaded, refresh])

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-text-tertiary">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading…
      </div>
    )
  }

  if (error || !party || !resolved) {
    return (
      <div className="p-8">
        <p
          role="alert"
          className="rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
        >
          {error ?? 'Could not load this party'}
        </p>
        <Link href="/parties" className="mt-4 inline-block text-sm text-accent">
          Back to parties
        </Link>
      </div>
    )
  }

  return (
    <div className="max-w-5xl p-8">
      <Link href="/parties" className="text-xs text-text-tertiary hover:text-text-primary">
        ← Parties
      </Link>
      <div className="mt-2 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">
            {party.name}
          </h1>
          <p className="mt-1 text-sm text-text-secondary">
            {party.type}
            {party.party_number && ` · ${party.party_number}`}
            {!party.is_active && ' · archived'}
          </p>
        </div>
        {canEdit && (
          <ArchiveButton party={party} onDone={refresh} />
        )}
      </div>

      {!canEdit && (
        <p className="mt-4 rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary">
          You can read this party but not change it — editing needs the
          parties permission.
        </p>
      )}

      {party.duplicates.length > 0 && (
        <ul className="mt-4 space-y-1">
          {party.duplicates.map((entry) => (
            <li
              key={entry.party_id}
              data-testid={`party-duplicate-${entry.reason}`}
              className="rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2 text-xs text-status-warning"
            >
              {entry.detail}{' '}
              <Link href={`/parties/${entry.party_id}`} className="underline">
                Open it
              </Link>
            </li>
          ))}
        </ul>
      )}

      <div role="tablist" aria-label="Party" className="mt-5 flex gap-1 border-b border-border">
        {TABS.map(([value, label]) => (
          <button
            key={value}
            role="tab"
            type="button"
            aria-selected={tab === value}
            onClick={() => setTab(value)}
            className={
              tab === value
                ? 'border-b-2 border-accent px-3 py-2 text-sm font-medium text-text-primary'
                : 'border-b-2 border-transparent px-3 py-2 text-sm text-text-secondary hover:text-text-primary'
            }
          >
            {label}
          </button>
        ))}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[2fr_1fr]">
        <div>
          {tab === 'basics' && (
            <BasicsTab party={party} resolved={resolved} canEdit={canEdit} onSaved={refresh} />
          )}
          {tab === 'addresses' && (
            <AddressesTab party={party} resolved={resolved} canEdit={canEdit} onSaved={refresh} />
          )}
          {tab === 'contacts' && (
            <ContactsTab party={party} canEdit={canEdit} onSaved={refresh} />
          )}
          {tab === 'hierarchy' && <HierarchyTab party={party} />}
          {tab === 'tax' && (
            <TaxTab party={party} resolved={resolved} canEdit={canEdit} onSaved={refresh} />
          )}
          {tab === 'banking' && (
            <BankingTab party={party} canEdit={canEdit} onSaved={refresh} />
          )}
        </div>
        <ResolvedPanel party={party} resolved={resolved} />
      </div>
    </div>
  )
}

/** A value with its origin. Greyed and labelled when it is not this party's.
 *
 *  The label is the whole point: a user who cannot see that an address came
 *  from the organisation edits the person to change it, and nothing happens. */
function Inherited({
  field,
  ownLevel,
  names,
  children,
}: {
  field: ResolvedField
  ownLevel: string
  names: Record<string, unknown>
  children?: React.ReactNode
}) {
  const inherited = field.source !== null && field.source !== ownLevel
  if (!inherited) return <>{children}</>
  const from = field.source ? String(names[field.source] ?? field.source) : ''
  return (
    <span
      data-testid={`inherited-from-${field.source}`}
      className="text-text-tertiary"
    >
      {children}{' '}
      <span className="text-[11px]">
        (from {from} — {field.source})
      </span>
    </span>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4 border-b border-border py-2 text-sm last:border-0">
      <span className="w-40 shrink-0 text-text-tertiary">{label}</span>
      <span className="flex-1 text-text-primary">{children}</span>
    </div>
  )
}

function BasicsTab({
  party,
  resolved,
  canEdit,
  onSaved,
}: {
  party: Party
  resolved: ResolvedRecipient
  canEdit: boolean
  onSaved: () => Promise<void>
}) {
  const [draft, setDraft] = React.useState({
    name: party.name,
    trading_name: party.trading_name ?? '',
    legal_form: party.legal_form ?? '',
    position: party.position ?? '',
    is_customer: party.is_customer,
    is_supplier: party.is_supplier,
    is_partner: party.is_partner,
    party_number: party.party_number ?? '',
    notes: party.notes ?? '',
  })
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    setDraft({
      name: party.name,
      trading_name: party.trading_name ?? '',
      legal_form: party.legal_form ?? '',
      position: party.position ?? '',
      is_customer: party.is_customer,
      is_supplier: party.is_supplier,
      is_partner: party.is_partner,
      party_number: party.party_number ?? '',
      notes: party.notes ?? '',
    })
  }, [party])

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await api.patch(`/parties/${party.id}`, {
        name: draft.name,
        trading_name: draft.trading_name || null,
        legal_form: draft.legal_form || null,
        position: draft.position || null,
        is_customer: draft.is_customer,
        is_supplier: draft.is_supplier,
        is_partner: draft.is_partner,
        party_number: draft.party_number || null,
        notes: draft.notes || null,
      })
      await onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not save')
    } finally {
      setSaving(false)
    }
  }

  if (!canEdit) {
    return (
      <div>
        <Row label="Name">{party.name}</Row>
        <Row label="Trading name">{party.trading_name ?? '—'}</Row>
        <Row label="Roles">
          {[
            party.is_customer && 'customer',
            party.is_supplier && 'supplier',
            party.is_partner && 'partner',
          ]
            .filter(Boolean)
            .join(', ') || '—'}
        </Row>
        <Row label="Number">{party.party_number ?? '—'}</Row>
        <Row label="Payment terms">
          <Inherited
            field={resolved.payment_terms_days}
            ownLevel={resolved.own_level}
            names={resolved.names as Record<string, unknown>}
          >
            {resolved.payment_terms_days.value
              ? `${resolved.payment_terms_days.value} days`
              : '—'}
          </Inherited>
        </Row>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <label className="block text-xs font-medium text-text-secondary">
        Name
        <Input
          aria-label="Name"
          value={draft.name}
          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          className="mt-1"
        />
      </label>
      <label className="block text-xs font-medium text-text-secondary">
        Trading name
        <Input
          aria-label="Trading name"
          value={draft.trading_name}
          onChange={(event) =>
            setDraft({ ...draft, trading_name: event.target.value })
          }
          className="mt-1"
        />
      </label>
      {party.type === 'person' && (
        <label className="block text-xs font-medium text-text-secondary">
          Position
          <Input
            aria-label="Position"
            value={draft.position}
            onChange={(event) =>
              setDraft({ ...draft, position: event.target.value })
            }
            className="mt-1"
          />
        </label>
      )}
      <fieldset className="space-y-1">
        <legend className="text-xs font-medium text-text-secondary">
          Roles — a party can be several at once
        </legend>
        {(
          [
            ['is_customer', 'Customer'],
            ['is_supplier', 'Supplier'],
            ['is_partner', 'Partner'],
          ] as ['is_customer' | 'is_supplier' | 'is_partner', string][]
        ).map(([key, label]) => (
          <label key={key} className="flex items-center gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              checked={draft[key]}
              onChange={(event) => setDraft({ ...draft, [key]: event.target.checked })}
            />
            {label}
          </label>
        ))}
      </fieldset>
      <label className="block text-xs font-medium text-text-secondary">
        Number
        <Input
          aria-label="Number"
          value={draft.party_number}
          placeholder="Allocated when you make this a customer or supplier"
          onChange={(event) =>
            setDraft({ ...draft, party_number: event.target.value })
          }
          className="mt-1"
        />
      </label>

      {error && (
        <p
          role="alert"
          data-testid="party-basics-error"
          className="rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
        >
          {error}
        </p>
      )}
      <button
        type="button"
        data-testid="party-basics-save"
        onClick={() => void save()}
        disabled={saving}
        className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
      >
        {saving ? 'Saving…' : 'Save'}
      </button>
    </div>
  )
}

function AddressesTab({
  party,
  resolved,
  canEdit,
  onSaved,
}: {
  party: Party
  resolved: ResolvedRecipient
  canEdit: boolean
  onSaved: () => Promise<void>
}) {
  const [draft, setDraft] = React.useState({
    kind: 'billing',
    street: '',
    zip: '',
    city: '',
    country: '',
    is_default: true,
  })
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  async function add() {
    setSaving(true)
    setError(null)
    try {
      await api.post(`/parties/${party.id}/addresses`, {
        ...draft,
        street: draft.street || null,
        zip: draft.zip || null,
        city: draft.city || null,
        country: draft.country || null,
      })
      setDraft({ ...draft, street: '', zip: '', city: '' })
      await onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not add the address')
    } finally {
      setSaving(false)
    }
  }

  const billing = resolved.billing_address

  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-text-tertiary">
          Billing address a document would use
        </p>
        <p className="mt-1 text-sm" data-testid="party-billing-resolved">
          <Inherited
            field={billing}
            ownLevel={resolved.own_level}
            names={resolved.names as Record<string, unknown>}
          >
            {billing.value
              ? [
                  (billing.value as Record<string, string>).street,
                  (billing.value as Record<string, string>).zip,
                  (billing.value as Record<string, string>).city,
                  (billing.value as Record<string, string>).country,
                ]
                  .filter(Boolean)
                  .join(', ')
              : 'None anywhere in the hierarchy'}
          </Inherited>
        </p>
      </div>

      {party.addresses.length > 0 && (
        <div>
          {party.addresses.map((address) => (
            <Row key={address.id} label={address.kind}>
              {[address.street, address.zip, address.city, address.country]
                .filter(Boolean)
                .join(', ')}
              {address.is_default && (
                <span className="ml-2 text-[11px] text-text-tertiary">default</span>
              )}
            </Row>
          ))}
        </div>
      )}

      {canEdit && (
        <div className="space-y-2 rounded-md border border-border p-3">
          <p className="text-xs font-medium text-text-secondary">Add an address</p>
          <select
            aria-label="Address kind"
            value={draft.kind}
            onChange={(event) => setDraft({ ...draft, kind: event.target.value })}
            className="w-full rounded-md border border-border bg-bg-primary px-3 py-2 text-sm"
          >
            <option value="billing">Billing</option>
            <option value="delivery">Delivery</option>
            <option value="postal">Postal</option>
          </select>
          <Input
            aria-label="Street"
            placeholder="Street"
            value={draft.street}
            onChange={(event) => setDraft({ ...draft, street: event.target.value })}
          />
          <div className="flex gap-2">
            <Input
              aria-label="Postcode"
              placeholder="Postcode"
              value={draft.zip}
              onChange={(event) => setDraft({ ...draft, zip: event.target.value })}
            />
            <Input
              aria-label="City"
              placeholder="City"
              value={draft.city}
              onChange={(event) => setDraft({ ...draft, city: event.target.value })}
            />
            <Input
              aria-label="Country"
              placeholder="AT"
              maxLength={2}
              value={draft.country}
              onChange={(event) =>
                setDraft({ ...draft, country: event.target.value.toUpperCase() })
              }
            />
          </div>
          <label className="flex items-center gap-2 text-xs text-text-secondary">
            <input
              type="checkbox"
              checked={draft.is_default}
              onChange={(event) =>
                setDraft({ ...draft, is_default: event.target.checked })
              }
            />
            Use this one by default
          </label>
          {error && (
            <p
              role="alert"
              data-testid="party-address-error"
              className="rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
            >
              {error}
            </p>
          )}
          <button
            type="button"
            data-testid="party-address-add"
            onClick={() => void add()}
            disabled={saving}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
          >
            {saving ? 'Adding…' : 'Add address'}
          </button>
        </div>
      )}
    </div>
  )
}

function ContactsTab({
  party,
  canEdit,
  onSaved,
}: {
  party: Party
  canEdit: boolean
  onSaved: () => Promise<void>
}) {
  const [draft, setDraft] = React.useState({
    kind: 'email',
    value: '',
    purpose: 'general',
    is_primary: true,
  })
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  async function add() {
    setSaving(true)
    setError(null)
    try {
      await api.post(`/parties/${party.id}/contact-points`, draft)
      setDraft({ ...draft, value: '' })
      await onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not add the contact')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      {party.contact_points.map((contact) => (
        <Row key={contact.id} label={`${contact.kind} · ${contact.purpose}`}>
          {contact.value}
          {contact.is_primary && (
            <span className="ml-2 text-[11px] text-text-tertiary">primary</span>
          )}
        </Row>
      ))}
      {party.contact_points.length === 0 && (
        <p className="text-sm text-text-tertiary">No contact details yet.</p>
      )}

      {canEdit && (
        <div className="space-y-2 rounded-md border border-border p-3">
          <p className="text-xs font-medium text-text-secondary">Add a contact</p>
          <div className="flex gap-2">
            <select
              aria-label="Contact kind"
              value={draft.kind}
              onChange={(event) => setDraft({ ...draft, kind: event.target.value })}
              className="rounded-md border border-border bg-bg-primary px-3 py-2 text-sm"
            >
              <option value="email">Email</option>
              <option value="phone">Phone</option>
            </select>
            <select
              aria-label="Contact purpose"
              value={draft.purpose}
              onChange={(event) =>
                setDraft({ ...draft, purpose: event.target.value })
              }
              className="rounded-md border border-border bg-bg-primary px-3 py-2 text-sm"
            >
              <option value="general">General</option>
              <option value="invoice">Invoices</option>
            </select>
            <Input
              aria-label="Contact value"
              value={draft.value}
              onChange={(event) => setDraft({ ...draft, value: event.target.value })}
            />
          </div>
          {error && (
            <p role="alert" className="text-sm text-status-error">
              {error}
            </p>
          )}
          <button
            type="button"
            data-testid="party-contact-add"
            onClick={() => void add()}
            disabled={saving || !draft.value.trim()}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
          >
            {saving ? 'Adding…' : 'Add contact'}
          </button>
        </div>
      )}
    </div>
  )
}

function HierarchyTab({ party }: { party: Party }) {
  return (
    <div className="space-y-3">
      {party.parent_id && (
        <p className="text-sm text-text-secondary">
          Belongs to{' '}
          <Link href={`/parties/${party.parent_id}`} className="text-accent underline">
            its parent
          </Link>
          .
        </p>
      )}
      {party.children.length === 0 ? (
        <p data-testid="party-no-children" className="text-sm text-text-tertiary">
          Nothing below this one.
          {party.type === 'organisation' &&
            ' Add a department or a person from the Parties list.'}
        </p>
      ) : (
        <ul className="space-y-2">
          {party.children.map((child) => (
            <li key={child.id}>
              <Link
                href={`/parties/${child.id}`}
                data-testid={`party-child-${child.id}`}
                className="flex items-center gap-3 rounded-md border border-border px-3 py-2 text-sm hover:bg-bg-hover/50"
              >
                <span className="w-24 text-xs text-text-tertiary">{child.type}</span>
                <span className="flex-1 text-text-primary">{child.name}</span>
                {!child.is_active && (
                  <span className="text-[11px] text-status-warning">archived</span>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function TaxTab({
  party,
  resolved,
  canEdit,
  onSaved,
}: {
  party: Party
  resolved: ResolvedRecipient
  canEdit: boolean
  onSaved: () => Promise<void>
}) {
  const [taxCountry, setTaxCountry] = React.useState(party.tax_country ?? '')
  const [businessStatus, setBusinessStatus] = React.useState(party.business_status)
  const [statusNote, setStatusNote] = React.useState(party.tax_status_note ?? '')
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [check, setCheck] = React.useState<PartyVatCheck | null>(
    party.latest_vat_check ?? null,
  )
  const [preview, setPreview] = React.useState<TaxProposal | null>(null)

  React.useEffect(() => {
    setTaxCountry(party.tax_country ?? '')
    setBusinessStatus(party.business_status)
    setStatusNote(party.tax_status_note ?? '')
    setCheck(party.latest_vat_check ?? null)
  }, [party])

  React.useEffect(() => {
    void (async () => {
      try {
        setPreview(
          await api.post<TaxProposal>(`/parties/${party.id}/tax-preview`, {
            supply_kind: 'services',
          }),
        )
      } catch {
        setPreview(null)
      }
    })()
  }, [party])

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await api.patch(`/parties/${party.id}`, {
        tax_country: taxCountry || null,
        business_status: businessStatus,
        tax_status_note: statusNote || null,
      })
      await onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not save')
    } finally {
      setSaving(false)
    }
  }



  const traderName = check?.trader_name ?? null
  const traderAddress = check?.trader_address ?? null

  return (
    <div className="space-y-5">
      <div className="space-y-3">
        <div>
          <p className="text-xs font-medium text-text-secondary">
            Tax identifier a document would use
          </p>
          <p className="mt-1 text-sm" data-testid="party-resolved-tax-id">
            <Inherited
              field={resolved.vat_id}
              ownLevel={resolved.own_level}
              names={resolved.names as Record<string, unknown>}
            >
              {resolved.vat_id.value ? (
                <>
                  <span className="font-mono">
                    {String(resolved.vat_id.value)}
                  </span>
                  {resolved.tax_id_kind.value ? (
                    <span className="ml-2 text-xs text-text-tertiary">
                      {TAX_ID_LABELS[String(resolved.tax_id_kind.value)] ??
                        String(resolved.tax_id_kind.value)}
                    </span>
                  ) : null}
                </>
              ) : (
                'None anywhere in the hierarchy'
              )}
            </Inherited>
          </p>
        </div>
        <label className="block text-xs font-medium text-text-secondary">
          Country for tax
          <Input
            aria-label="Country for tax"
            value={taxCountry}
            maxLength={2}
            disabled={!canEdit}
            onChange={(event) => setTaxCountry(event.target.value.toUpperCase())}
            className="mt-1"
          />
        </label>
        <label className="block text-xs font-medium text-text-secondary">
          Business or private
          <select
            aria-label="Business or private"
            value={businessStatus}
            disabled={!canEdit}
            onChange={(event) => setBusinessStatus(event.target.value)}
            className="mt-1 w-full rounded-md border border-border bg-bg-primary px-3 py-2 text-sm"
          >
            <option value="auto">Work it out automatically</option>
            <option value="business">Business</option>
            <option value="private">Private</option>
          </select>
        </label>
        <p data-testid="party-buyer-type" className="text-xs text-text-tertiary">
          Treated as <strong>{party.buyer_type}</strong> —{' '}
          {BUYER_REASONS[party.buyer_type_reason ?? ''] ??
            party.buyer_type_reason}
          .
        </p>
        <label className="block text-xs font-medium text-text-secondary">
          Why there is no number, if there is none
          <Input
            aria-label="Tax status note"
            value={statusNote}
            disabled={!canEdit}
            placeholder="Kleinunternehmer, keine UID"
            onChange={(event) => setStatusNote(event.target.value)}
            className="mt-1"
          />
          <span className="mt-1 block text-[11px] font-normal text-text-tertiary">
            A business below the small-business threshold holds no VAT ID.
            Saying so here is better than leaving an empty field that looks
            like something failed to save.
          </span>
        </label>
        {canEdit && (
          <button
            type="button"
            data-testid="party-tax-save"
            onClick={() => void save()}
            disabled={saving}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        )}
      </div>

      {error && (
        <p
          role="alert"
          data-testid="party-tax-error"
          className="rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-sm text-status-error"
        >
          {error}
        </p>
      )}

      <div className="space-y-2 rounded-md border border-border p-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs font-medium text-text-secondary">
            Registry check
          </p>
        </div>

        {check ? (
          <div className="space-y-1">
            <p data-testid={`party-vat-status-${check.status}`} className="text-sm">
              <VatChip status={check.status} />{' '}
              <span className="text-text-tertiary">
                {new Date(check.checked_at).toLocaleDateString()} · {check.vat_id}
              </span>
            </p>
            {check.detail && (
              <p className="text-xs text-text-secondary">{check.detail}</p>
            )}
            {traderName && (
              <p className="text-xs text-text-secondary">
                VIES has: <strong>{traderName}</strong>
                {party.name !== traderName && (
                  <span
                    data-testid="party-vies-name-differs"
                    className="ml-2 text-status-warning"
                  >
                    differs from {party.name}
                  </span>
                )}
              </p>
            )}
            {traderAddress && (
              <p className="text-xs text-text-secondary">
                VIES address: {traderAddress}
              </p>
            )}
            {check.consultation_ref && (
              <p className="text-xs text-text-tertiary">
                Consultation reference {check.consultation_ref}
              </p>
            )}
          </div>
        ) : (
          <p className="text-xs text-text-tertiary">
            Not checked yet. A check is never required to save a party.
          </p>
        )}

        <p
          data-testid="party-vies-disclaimer"
          className="text-[11px] text-text-tertiary"
        >
          This is a VIES lookup of record. It is{' '}
          <strong>not</strong> the qualified confirmation procedure of a tax
          authority, and the European Commission&rsquo;s own documentation says
          a result does not in itself grant an exemption. Ask your tax advisor
          what your reverse-charge documentation needs.
        </p>
      </div>

      {preview && (
        <div
          data-testid="party-tax-preview"
          className="rounded-md border border-border p-3"
        >
          <p className="text-xs font-medium uppercase tracking-wide text-text-tertiary">
            What a services invoice would carry today
          </p>
          {preview.blocking ? (
            <p className="mt-1 flex items-start gap-2 text-sm text-status-error">
              <ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0" />
              Nothing can be proposed yet.
            </p>
          ) : (
            <p className="mt-1 text-sm text-text-primary">
              <span className="font-mono">{preview.code}</span> at{' '}
              {preview.rate} % — {preview.treatment_label}
            </p>
          )}
          {preview.warnings.map((warning) => (
            <p
              key={warning.code}
              data-testid={`party-tax-warning-${warning.code}`}
              className="mt-2 rounded-md border border-status-warning/40 bg-status-warning/10 px-2 py-1 text-xs text-status-warning"
            >
              {warning.text}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

function VatChip({ status }: { status: string }) {
  if (status === VAT_VALID) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-status-success/40 px-2 py-0.5 text-[11px] text-status-success">
        <Check className="h-3 w-3" /> Valid
      </span>
    )
  }
  if (status === VAT_INVALID) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-status-error/40 px-2 py-0.5 text-[11px] text-status-error">
        <AlertTriangle className="h-3 w-3" /> Not valid
      </span>
    )
  }
  if (status === VAT_NOT_APPLICABLE) {
    return (
      <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-text-tertiary">
        Not checkable in VIES
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-status-warning/40 px-2 py-0.5 text-[11px] text-status-warning">
      <AlertTriangle className="h-3 w-3" /> No answer
    </span>
  )
}

function BankingTab({
  party,
  canEdit,
  onSaved,
}: {
  party: Party
  canEdit: boolean
  onSaved: () => Promise<void>
}) {
  const [iban, setIban] = React.useState('')
  const [label, setLabel] = React.useState('')
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  async function add() {
    setSaving(true)
    setError(null)
    try {
      await api.post(`/parties/${party.id}/bank-accounts`, {
        iban,
        label: label || null,
        is_default: party.bank_accounts.length === 0,
      })
      setIban('')
      setLabel('')
      await onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not add the account')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      {party.bank_accounts.map((account) => (
        <Row key={account.id} label={account.label ?? 'Account'}>
          <span className="font-mono">{account.iban}</span>
          {account.is_default && (
            <span className="ml-2 text-[11px] text-text-tertiary">default</span>
          )}
        </Row>
      ))}
      {party.bank_accounts.length === 0 && (
        <p className="text-sm text-text-tertiary">No account on file.</p>
      )}

      {canEdit && (
        <div className="space-y-2 rounded-md border border-border p-3">
          <p className="text-xs font-medium text-text-secondary">Add an account</p>
          <Input
            aria-label="IBAN"
            placeholder="AT61 1904 3002 3457 3201"
            value={iban}
            onChange={(event) => setIban(event.target.value)}
          />
          <Input
            aria-label="Account label"
            placeholder="What to call it"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
          {error && (
            <p role="alert" className="text-sm text-status-error">
              {error}
            </p>
          )}
          <button
            type="button"
            data-testid="party-bank-add"
            onClick={() => void add()}
            disabled={saving || !iban.trim()}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
          >
            {saving ? 'Adding…' : 'Add account'}
          </button>
        </div>
      )}
    </div>
  )
}

/** Exactly what a document would use, with every source named.
 *
 *  Its own panel rather than scattered through the tabs, because "what will
 *  the invoice actually say" is one question and answering it in six places
 *  is six places for it to disagree with itself. */
function ResolvedPanel({
  party,
  resolved,
}: {
  party: Party
  resolved: ResolvedRecipient
}) {
  const address = resolved.billing_address.value as Record<string, string> | null

  return (
    <aside
      data-testid="party-resolved-panel"
      className="h-fit rounded-md border border-border bg-bg-secondary p-4"
    >
      <h2 className="text-xs font-semibold uppercase tracking-wide text-text-tertiary">
        What a document would use
      </h2>
      <dl className="mt-3 space-y-3 text-sm">
        <Resolved label="Made out to" field={resolved.invoice_name} resolved={resolved} />
        <Resolved label="Attention of" field={resolved.contact_name} resolved={resolved} />
        <div>
          <dt className="text-xs text-text-tertiary">Billing address</dt>
          <dd className="text-text-primary">
            <Inherited
              field={resolved.billing_address}
              ownLevel={resolved.own_level}
              names={resolved.names as Record<string, unknown>}
            >
              {address
                ? [address.street, address.zip, address.city, address.country]
                    .filter(Boolean)
                    .join(', ')
                : '—'}
            </Inherited>
          </dd>
        </div>
        <Resolved label="VAT ID" field={resolved.vat_id} resolved={resolved} />
        <Resolved label="Country for tax" field={resolved.tax_country} resolved={resolved} />
        <Resolved label="Email" field={resolved.email} resolved={resolved} />
        <div>
          <dt className="text-xs text-text-tertiary">Treated as</dt>
          <dd className="text-text-primary">{resolved.buyer_type}</dd>
        </div>
      </dl>
      <p className="mt-3 text-[11px] text-text-tertiary">
        A finalised document keeps a copy of all of this, so changing it later
        never restates an invoice already issued.
      </p>
    </aside>
  )
}

function Resolved({
  label,
  field,
  resolved,
}: {
  label: string
  field: ResolvedField
  resolved: ResolvedRecipient
}) {
  return (
    <div>
      <dt className="text-xs text-text-tertiary">{label}</dt>
      <dd className="text-text-primary">
        <Inherited
          field={field}
          ownLevel={resolved.own_level}
          names={resolved.names as Record<string, unknown>}
        >
          {field.value === null || field.value === undefined
            ? '—'
            : String(field.value)}
        </Inherited>
      </dd>
    </div>
  )
}

function ArchiveButton({
  party,
  onDone,
}: {
  party: Party
  onDone: () => Promise<void>
}) {
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  async function go() {
    setBusy(true)
    setError(null)
    try {
      await api.post(
        `/parties/${party.id}/${party.is_active ? 'archive' : 'reactivate'}`,
        {},
      )
      await onDone()
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : 'Could not change whether this party is active',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="text-right">
      <button
        type="button"
        data-testid="party-archive"
        onClick={() => void go()}
        disabled={busy}
        className="rounded-md border border-border px-2.5 py-1.5 text-sm text-text-primary hover:bg-bg-hover disabled:opacity-50"
      >
        {busy ? '…' : party.is_active ? 'Archive' : 'Reactivate'}
      </button>
      {error && (
        <p
          role="alert"
          data-testid="party-archive-error"
          className="mt-2 max-w-sm rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2 text-left text-xs text-status-error"
        >
          {error}
        </p>
      )}
    </div>
  )
}
