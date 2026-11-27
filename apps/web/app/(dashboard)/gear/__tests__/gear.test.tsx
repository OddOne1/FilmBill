import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "@testing-library/react";

const push = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    upload: vi.fn(),
    download: vi.fn(),
  },
  setActiveCompanyId: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
      this.detail = detail;
    }
  },
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/gear",
  useParams: () => ({ id: "d1" }),
  useRouter: () => ({ push }),
}));

import { api } from "@/lib/api";
import GearPage from "../page";
import DevicePage from "../[id]/page";
import { useCompanyStore } from "@/stores/company-store";

/**
 * The gear screens. What is under test is what they SEND — the quantity
 * question on retire and reactivate, ticked by default; a pasted list split
 * into serial numbers; a German price turned into the API's dot — and that
 * a role without `budgets.view` is shown no price and no way to edit.
 */

const OWNER = [
  "devices.view",
  "devices.edit",
  "budgets.view",
  "catalog.view",
  "parties.view",
];
const STAFF = ["devices.view", "projects.view", "catalog.view"];

function seed(permissions: string[]) {
  useCompanyStore.setState({
    companies: [
      {
        id: "c1",
        legal_name: "YON Studio OG",
        trading_name: null,
        display_name: "YON Studio OG",
        default_currency: "EUR",
        role: "owner",
        permissions,
        expires_at: null,
      },
    ],
    activeCompanyId: "c1",
    loaded: true,
    error: null,
  });
}

const counts = {
  owned: 3,
  recorded: 2,
  active: 2,
  needs_check: 0,
  in_repair: 0,
  in_hand: 2,
  lost: 0,
  retired: 0,
  sold: 0,
  removed: 0,
  not_yet_recorded: 1,
  beyond_quantity: 0,
};

function device(overrides: Record<string, unknown> = {}) {
  return {
    id: "d1",
    name: "Alexa 35",
    category: "Camera",
    quantity_owned: 3,
    purchase_price: "65000.00",
    price_visible: true,
    purchase_date: null,
    catalog_item_id: null,
    catalog_item_name: null,
    notes: null,
    is_active: true,
    counts,
    created_at: "2026-11-26T10:00:00Z",
    updated_at: "2026-11-26T10:00:00Z",
    rental: rental(),
    units: [
      unit({ id: "u1", serial_number: "AX-1" }),
      unit({ id: "u2", serial_number: "AX-2" }),
    ],
    ...overrides,
  };
}

function rental(overrides: Record<string, unknown> = {}) {
  return {
    linked: false,
    item_id: null,
    item_name: null,
    item_is_active: null,
    rates_visible: true,
    price_per_day: null,
    cost_per_day: null,
    rental_factor_group_id: null,
    rental_factor_group_name: null,
    can_edit: true,
    edit_blocked_reason: null,
    existing_item: null,
    ...overrides,
  };
}

const LINKED = {
  linked: true,
  item_id: "i1",
  item_name: "Alexa 35",
  item_is_active: true,
  price_per_day: "1200.00",
  cost_per_day: "300.00",
  rental_factor_group_id: "g1",
  rental_factor_group_name: "Standard",
};

function unit(overrides: Record<string, unknown> = {}) {
  return {
    id: "u1",
    device_id: "d1",
    serial_number: "AX-1",
    status: "active",
    acquired_on: null,
    purchase_visible: true,
    purchase_price: null,
    seller_party_id: null,
    seller_party_name: null,
    seller_name: null,
    purchase_reference: null,
    removed_on: null,
    removed_reason: null,
    notes: null,
    created_at: "2026-11-26T10:00:00Z",
    updated_at: "2026-11-26T10:00:00Z",
    ...overrides,
  };
}

function routeGets(detail = device()) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path.startsWith("/devices/d1")) return detail;
    if (path.startsWith("/devices")) {
      const { units: _units, ...row } = detail;
      return { devices: [row], total: 1, categories: ["Camera", "Light"] };
    }
    if (path.startsWith("/catalog/items"))
      return { items: [], total: 0, limit: 200, offset: 0 };
    if (path.startsWith("/catalog/rental-factor-groups")) {
      return [
        {
          id: "g1",
          name: "Standard",
          notes: null,
          is_active: true,
          factors: [],
        },
        {
          id: "g2",
          name: "Langzeit",
          notes: null,
          is_active: true,
          factors: [],
        },
      ];
    }
    if (path.startsWith("/parties")) {
      return {
        parties: [{ id: "s1", name: "ARRI Rental Wien" }],
        total: 1,
        limit: 200,
        offset: 0,
      };
    }
    throw new Error(`unexpected GET ${path}`);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  seed(OWNER);
  routeGets();
});

describe("the gear list", () => {
  it("puts the API counts into words on every row, with the price for a role that may see it", async () => {
    render(<GearPage />);
    const row = (await screen.findAllByTestId("gear-row"))[0];
    expect(within(row).getByTestId("gear-counts")).toHaveTextContent(
      "3 owned · 2 serial numbers recorded · 1 not yet recorded",
    );
    expect(within(row).getByRole("link", { name: "Alexa 35" })).toHaveAttribute(
      "href",
      "/gear/d1",
    );
    expect(row).toHaveTextContent("65,000.00");
  });

  it("shows staff the gear and the counts, no price column, and why there is nothing to edit", async () => {
    seed(STAFF);
    routeGets(device({ purchase_price: null, price_visible: false }));
    render(<GearPage />);
    const row = (await screen.findAllByTestId("gear-row"))[0];
    expect(within(row).getByTestId("gear-counts")).toHaveTextContent("3 owned");
    expect(screen.queryByText("Purchase price")).not.toBeInTheDocument();
    expect(screen.getByTestId("gear-read-only")).toHaveTextContent(
      "gear edit permission",
    );
    expect(screen.queryByTestId("gear-add-open")).not.toBeInTheDocument();
  });

  it("adding a device sends a German price as the API wants it, and refuses an ambiguous one", async () => {
    vi.mocked(api.post).mockResolvedValue(device({ id: "new" }));
    render(<GearPage />);
    fireEvent.click(await screen.findByTestId("gear-add-open"));
    expect(screen.getByTestId("gear-add-why")).toHaveTextContent("Type a name");
    fireEvent.change(screen.getByTestId("gear-add-name"), {
      target: { value: "Alexa 35" },
    });
    fireEvent.change(screen.getByTestId("gear-add-quantity"), {
      target: { value: "3" },
    });
    fireEvent.change(screen.getByTestId("gear-add-price"), {
      target: { value: "1.500" },
    });
    expect(screen.getByTestId("gear-add-save")).toBeDisabled();
    expect(screen.getByTestId("gear-add-why")).toHaveTextContent(
      "1.500 could mean either",
    );

    fireEvent.change(screen.getByTestId("gear-add-price"), {
      target: { value: "65.000,00" },
    });
    fireEvent.click(screen.getByTestId("gear-add-save"));
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith("/devices", {
        name: "Alexa 35",
        category: null,
        quantity_owned: 3,
        purchase_price: "65000.00",
        purchase_date: null,
        notes: null,
      }),
    );
    await waitFor(() => expect(push).toHaveBeenCalledWith("/gear/new"));
  });

  it("tells somebody without devices.view why the page is closed", () => {
    seed(["parties.view"]);
    render(<GearPage />);
    expect(screen.getByTestId("gear-no-access")).toHaveTextContent("gear list");
    expect(vi.mocked(api.get)).not.toHaveBeenCalled();
  });
});

async function renderUnits() {
  render(<DevicePage />);
  fireEvent.click(await screen.findByTestId("device-tab-units"));
}

describe("one device", () => {
  function changeResult(before: number, after: number, status = "retired") {
    return {
      device: device({ quantity_owned: after }),
      unit: unit({ status }),
      quantity_before: before,
      quantity_after: after,
    };
  }

  it("removing needs what happened in words, asks to reduce the quantity (ticked) and sends both", async () => {
    vi.mocked(api.post).mockResolvedValue(changeResult(3, 2));
    await renderUnits();
    const rows = await screen.findAllByTestId("unit-row");
    fireEvent.click(within(rows[0]).getByTestId("unit-remove"));
    expect(screen.getByTestId("remove-reduce")).toBeChecked();
    expect(screen.getByTestId("remove-panel")).toHaveTextContent("from 3 to 2");
    // No reason, no removal — and the screen says why.
    expect(screen.getByTestId("remove-confirm")).toBeDisabled();
    expect(screen.getByTestId("remove-why")).toHaveTextContent(
      "Say what happened",
    );
    fireEvent.click(screen.getByTestId("remove-status-lost"));
    fireEvent.change(screen.getByTestId("remove-reason"), {
      target: { value: "Left in a taxi in Kaprun" },
    });
    fireEvent.click(screen.getByTestId("remove-confirm"));
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        "/devices/d1/units/u1/remove",
        {
          status: "lost",
          reason: "Left in a taxi in Kaprun",
          removed_on: null,
          reduce_quantity: true,
        },
      ),
    );
    expect(await screen.findByTestId("device-notice")).toHaveTextContent(
      "went from 3 to 2",
    );
  });

  it("removing with the box unticked says the quantity stays", async () => {
    vi.mocked(api.post).mockResolvedValue(changeResult(3, 3));
    await renderUnits();
    const rows = await screen.findAllByTestId("unit-row");
    fireEvent.click(within(rows[1]).getByTestId("unit-remove"));
    fireEvent.click(screen.getByTestId("remove-reduce"));
    fireEvent.change(screen.getByTestId("remove-reason"), {
      target: { value: "Kept as a museum piece" },
    });
    fireEvent.click(screen.getByTestId("remove-confirm"));
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        "/devices/d1/units/u2/remove",
        expect.objectContaining({ status: "retired", reduce_quantity: false }),
      ),
    );
    expect(await screen.findByTestId("device-notice")).toHaveTextContent(
      "The quantity owned stays 3.",
    );
  });

  it("a removed unit shows what happened, and bringing it back asks to raise the quantity (ticked)", async () => {
    routeGets(
      device({
        units: [
          unit({
            status: "sold",
            removed_on: "2026-11-01",
            removed_reason: "Sold to Studio X",
          }),
        ],
      }),
    );
    vi.mocked(api.post).mockResolvedValue(changeResult(3, 4, "active"));
    await renderUnits();
    // Removed units are behind the toggle.
    expect(await screen.findByTestId("units-empty")).toHaveTextContent(
      "removed ones are hidden",
    );
    fireEvent.click(screen.getByTestId("units-show-removed"));
    const row = (await screen.findAllByTestId("unit-row"))[0];
    expect(within(row).getByTestId("unit-removed-reason")).toHaveTextContent(
      "2026-11-01: Sold to Studio X",
    );
    fireEvent.click(within(row).getByTestId("unit-reactivate"));
    expect(screen.getByTestId("reactivate-raise")).toBeChecked();
    fireEvent.click(screen.getByTestId("reactivate-confirm"));
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        "/devices/d1/units/u1/reactivate",
        {
          raise_quantity: true,
        },
      ),
    );
  });

  it("needs check and in repair are one click and never ask about the quantity", async () => {
    vi.mocked(api.patch).mockResolvedValue(changeResult(3, 3, "needs_check"));
    await renderUnits();
    const rows = await screen.findAllByTestId("unit-row");
    fireEvent.click(within(rows[0]).getByTestId("unit-needs-check"));
    await waitFor(() =>
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith(
        "/devices/d1/units/u1",
        { status: "needs_check" },
      ),
    );
  });

  it("the purchase of one unit can be corrected with a seller who is not on file", async () => {
    vi.mocked(api.patch).mockResolvedValue(changeResult(3, 3, "active"));
    await renderUnits();
    const rows = await screen.findAllByTestId("unit-row");
    expect(rows[0]).toHaveTextContent("as device");
    fireEvent.click(within(rows[0]).getByTestId("unit-purchase"));
    const panel = screen.getByTestId("purchase-panel");
    fireEvent.change(within(panel).getByTestId("purchase-seller-name"), {
      target: { value: "Ebay, cam_shop_graz" },
    });
    fireEvent.click(within(panel).getByTestId("purchase-save"));
    await waitFor(() =>
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith(
        "/devices/d1/units/u1",
        {
          purchase_price: null,
          seller_party_id: null,
          seller_name: "Ebay, cam_shop_graz",
          purchase_reference: null,
          acquired_on: null,
        },
      ),
    );
  });

  it("setting the quantity by hand sends only what changed", async () => {
    vi.mocked(api.patch).mockResolvedValue(device({ quantity_owned: 5 }));
    render(<DevicePage />);
    fireEvent.change(await screen.findByTestId("device-quantity"), {
      target: { value: "5" },
    });
    fireEvent.click(screen.getByTestId("device-save"));
    await waitFor(() =>
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith("/devices/d1", {
        quantity_owned: 5,
      }),
    );
  });

  it("staff read the device without a price, a form or unit actions", async () => {
    seed(STAFF);
    routeGets(
      device({
        purchase_price: null,
        price_visible: false,
        units: [unit({ purchase_visible: false })],
      }),
    );
    render(<DevicePage />);
    expect(await screen.findByTestId("device-counts")).toHaveTextContent(
      "3 owned",
    );
    expect(screen.queryByTestId("device-form")).not.toBeInTheDocument();
    expect(screen.queryByText("Purchase price")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("device-tab-units"));
    await screen.findAllByTestId("unit-row");
    expect(screen.queryByText("Seller")).not.toBeInTheDocument();
    expect(screen.queryByTestId("unit-remove")).not.toBeInTheDocument();
    expect(screen.queryByTestId("units-add-open")).not.toBeInTheDocument();
    expect(screen.getByTestId("gear-read-only")).toBeInTheDocument();
  });
});

describe("rental prices on the device", () => {
  it('has no "Rented out as" picker; without an item it offers Set up rental price', async () => {
    vi.mocked(api.post).mockResolvedValue(device({ rental: rental(LINKED) }));
    render(<DevicePage />);
    const setup = await screen.findByTestId("rental-setup");
    expect(setup).toHaveTextContent("Set up rental price");
    expect(screen.queryByText(/Rented out as/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("device-catalog-item")).not.toBeInTheDocument();
    fireEvent.click(setup);
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        "/devices/d1/rental/setup",
        {},
      ),
    );
    expect(await screen.findByTestId("rental-catalog-name")).toHaveTextContent(
      "Priced in the catalog as ‘Alexa 35’",
    );
    expect(screen.getByRole("link", { name: /Alexa 35/ })).toHaveAttribute(
      "href",
      "/catalog/i1",
    );
  });

  it("offers the unlinked catalog item of the same name instead of a duplicate", async () => {
    routeGets(
      device({
        rental: rental({ existing_item: { id: "i9", name: "Alexa 35" } }),
      }),
    );
    render(<DevicePage />);
    expect(await screen.findByTestId("rental-setup")).toHaveTextContent(
      "Use the existing 'Alexa 35'",
    );
  });

  it("saving sends only what changed, a German price as the API wants it", async () => {
    routeGets(device({ rental: rental(LINKED) }));
    vi.mocked(api.put).mockResolvedValue(
      device({ rental: rental({ ...LINKED, price_per_day: "1450.00" }) }),
    );
    render(<DevicePage />);
    const price = await screen.findByTestId("rental-price");
    expect(price).toHaveValue("1200,00");
    expect(screen.getByTestId("rental-cost")).toHaveValue("300,00");
    expect(screen.getByTestId("rental-save")).toBeDisabled();
    expect(screen.getByTestId("rental-save-why")).toHaveTextContent(
      "Nothing changed yet.",
    );
    fireEvent.change(price, { target: { value: "1.450,00" } });
    await waitFor(() =>
      expect(
        within(screen.getByTestId("rental-group")).getByText("Langzeit"),
      ).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByTestId("rental-group"), {
      target: { value: "g2" },
    });
    fireEvent.click(screen.getByTestId("rental-save"));
    await waitFor(() =>
      expect(vi.mocked(api.put)).toHaveBeenCalledWith("/devices/d1/rental", {
        price_per_day: "1450.00",
        rental_factor_group_id: "g2",
      }),
    );
    expect(await screen.findByTestId("device-notice")).toHaveTextContent(
      "the catalog item has the same numbers",
    );
  });

  it("without the catalog edit permission the prices are read-only and the page says why", async () => {
    routeGets(
      device({
        rental: rental({
          ...LINKED,
          can_edit: false,
          edit_blocked_reason:
            "Your role can change gear but not the price list.",
        }),
      }),
    );
    render(<DevicePage />);
    expect(await screen.findByTestId("rental-price-read")).toHaveTextContent(
      "1,200.00",
    );
    expect(screen.queryByTestId("rental-form")).not.toBeInTheDocument();
    expect(screen.getByTestId("rental-why")).toHaveTextContent(
      "not the price list",
    );
  });

  it("staff see the catalog name and no price or cost", async () => {
    seed(STAFF);
    routeGets(
      device({
        purchase_price: null,
        price_visible: false,
        rental: rental({
          ...LINKED,
          price_per_day: null,
          cost_per_day: null,
          rates_visible: false,
          can_edit: false,
          edit_blocked_reason:
            "You can see the rental prices but not change them.",
        }),
      }),
    );
    render(<DevicePage />);
    expect(await screen.findByTestId("rental-catalog-name")).toHaveTextContent(
      "Alexa 35",
    );
    expect(screen.queryByText("Price per day (net)")).not.toBeInTheDocument();
    expect(screen.queryByText("Cost per day (net)")).not.toBeInTheDocument();
    expect(screen.getByTestId("rental-why")).toHaveTextContent(
      "only shown to people who can see budgets",
    );
  });
});

describe("the Add new item(s) popup", () => {
  function paste(input: HTMLElement, text: string) {
    fireEvent.paste(input, { clipboardData: { getData: () => text } });
  }

  async function openPopup() {
    await renderUnits();
    fireEvent.click(await screen.findByTestId("units-add-open"));
    return screen.findByTestId("units-add-dialog");
  }

  it("the units tab lists the current units with the counts line, and the text box is gone", async () => {
    await renderUnits();
    const rows = await screen.findAllByTestId("unit-row");
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("AX-1"),
      expect.stringContaining("AX-2"),
    ]);
    expect(screen.getByTestId("units-counts")).toHaveTextContent(
      "3 owned · 2 serial numbers recorded · 1 not yet recorded",
    );
    expect(screen.queryByTestId("add-serials-text")).not.toBeInTheDocument();
    expect(screen.getByTestId("units-add-open")).toHaveTextContent(
      "Add new item(s)",
    );
  });

  it("rows are added and removed; Enter adds a row and moves the cursor into it", async () => {
    const dialog = await openPopup();
    expect(within(dialog).getAllByTestId("units-add-row")).toHaveLength(1);
    expect(
      within(dialog).queryByTestId("units-add-remove-row"),
    ).not.toBeInTheDocument();
    expect(within(dialog).getByTestId("units-add-save")).toBeDisabled();
    expect(within(dialog).getByTestId("units-add-why")).toHaveTextContent(
      "Type at least one serial number.",
    );
    fireEvent.click(within(dialog).getByTestId("units-add-another"));
    expect(within(dialog).getAllByTestId("units-add-row")).toHaveLength(2);
    const inputs = within(dialog).getAllByTestId("units-add-serial");
    fireEvent.keyDown(inputs[1], { key: "Enter" });
    const three = within(dialog).getAllByTestId("units-add-serial");
    expect(three).toHaveLength(3);
    expect(document.activeElement).toBe(three[2]);
    fireEvent.click(within(dialog).getAllByTestId("units-add-remove-row")[0]);
    expect(within(dialog).getAllByTestId("units-add-row")).toHaveLength(2);
  });

  it("pasting several lines into a row spreads them over rows", async () => {
    const dialog = await openPopup();
    const first = within(dialog).getByTestId("units-add-serial");
    fireEvent.change(first, { target: { value: "AX-3" } });
    fireEvent.click(within(dialog).getByTestId("units-add-another"));
    paste(
      within(dialog).getAllByTestId("units-add-serial")[1],
      "AX-4\r\n\nAX-5\nAX-6\n",
    );
    const values = within(dialog)
      .getAllByTestId("units-add-serial")
      .map((input) => (input as HTMLInputElement).value);
    expect(values).toEqual(["AX-3", "AX-4", "AX-5", "AX-6"]);
    expect(within(dialog).getByTestId("units-add-save")).toHaveTextContent(
      "Add 4 units",
    );
  });

  it("a serial the device already has is flagged in its row before saving, and the skip is said after", async () => {
    vi.mocked(api.post).mockResolvedValue({
      device: device({
        units: [
          unit({ id: "u1", serial_number: "AX-1" }),
          unit({ id: "u2", serial_number: "AX-2" }),
          unit({ id: "u3", serial_number: "AX-3" }),
        ],
      }),
      created: [unit({ id: "u3", serial_number: "AX-3" })],
      skipped: [
        {
          serial_number: "ax-1",
          reason: "already_recorded",
          detail: "AX-1 is already recorded on Alexa 35.",
        },
      ],
    });
    const dialog = await openPopup();
    paste(within(dialog).getByTestId("units-add-serial"), "AX-3\n ax-1 ");
    const flags = within(dialog).getAllByTestId("units-add-flag");
    expect(flags).toHaveLength(1);
    expect(flags[0]).toHaveTextContent(
      "AX-1 is already recorded on Alexa 35; it will be skipped.",
    );
    expect(within(dialog).getByTestId("units-add-save")).toHaveTextContent(
      "Add 1 unit",
    );
    fireEvent.click(within(dialog).getByTestId("units-add-save"));
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        "/devices/d1/units",
        expect.objectContaining({ serial_numbers: ["AX-3", "ax-1"] }),
      ),
    );
    expect(await screen.findByTestId("units-skipped")).toHaveTextContent(
      "Skipped: AX-1 is already recorded on Alexa 35.",
    );
    expect(screen.getByTestId("device-notice")).toHaveTextContent(
      "1 unit added. The quantity owned is still 3.",
    );
    expect(screen.queryByTestId("units-add-dialog")).not.toBeInTheDocument();
    // The list reflects the new unit.
    expect(screen.getAllByTestId("unit-row")).toHaveLength(3);
  });

  it("the shared purchase fields are typed once and sent once for every serial, in one request", async () => {
    vi.mocked(api.post).mockResolvedValue({
      device: device(),
      created: [unit({ id: "u3" })],
      skipped: [],
    });
    const dialog = await openPopup();
    paste(within(dialog).getByTestId("units-add-serial"), "AX-3\nAX-4\nAX-5");
    fireEvent.change(within(dialog).getByTestId("purchase-price"), {
      target: { value: "61.750,00" },
    });
    await waitFor(() =>
      expect(
        within(within(dialog).getByTestId("purchase-seller-party")).getByText(
          "ARRI Rental Wien",
        ),
      ).toBeInTheDocument(),
    );
    fireEvent.change(within(dialog).getByTestId("purchase-seller-party"), {
      target: { value: "s1" },
    });
    fireEvent.change(within(dialog).getByTestId("purchase-reference"), {
      target: { value: "RE-2026-0311" },
    });
    fireEvent.change(within(dialog).getByTestId("units-add-date"), {
      target: { value: "2026-11-20" },
    });
    fireEvent.click(within(dialog).getByTestId("units-add-save"));
    await waitFor(() => expect(vi.mocked(api.post)).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.post)).toHaveBeenCalledWith("/devices/d1/units", {
      serial_numbers: ["AX-3", "AX-4", "AX-5"],
      acquired_on: "2026-11-20",
      purchase_price: "61750.00",
      seller_party_id: "s1",
      seller_name: null,
      purchase_reference: "RE-2026-0311",
    });
  });

  it("an ambiguous price keeps Save disabled and says why; without budgets.view no purchase fields", async () => {
    const dialog = await openPopup();
    fireEvent.change(within(dialog).getByTestId("units-add-serial"), {
      target: { value: "AX-3" },
    });
    fireEvent.change(within(dialog).getByTestId("purchase-price"), {
      target: { value: "1.500" },
    });
    expect(within(dialog).getByTestId("units-add-save")).toBeDisabled();
    expect(within(dialog).getByTestId("units-add-why")).toHaveTextContent(
      "1.500 could mean either",
    );
  });

  it("Escape closes the popup without saving", async () => {
    const dialog = await openPopup();
    fireEvent.change(within(dialog).getByTestId("units-add-serial"), {
      target: { value: "AX-3" },
    });
    fireEvent.keyDown(within(dialog).getByTestId("units-add-serial"), {
      key: "Escape",
    });
    await waitFor(() =>
      expect(screen.queryByTestId("units-add-dialog")).not.toBeInTheDocument(),
    );
    expect(vi.mocked(api.post)).not.toHaveBeenCalled();
  });

  it("a role without budgets.view gets serials and a date only", async () => {
    seed(["devices.view", "devices.edit"]);
    routeGets(device({ purchase_price: null, price_visible: false }));
    vi.mocked(api.post).mockResolvedValue({
      device: device(),
      created: [unit({ id: "u3" })],
      skipped: [],
    });
    const dialog = await openPopup();
    expect(
      within(dialog).queryByTestId("purchase-price"),
    ).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByTestId("units-add-serial"), {
      target: { value: "AX-3" },
    });
    fireEvent.click(within(dialog).getByTestId("units-add-save"));
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith("/devices/d1/units", {
        serial_numbers: ["AX-3"],
        acquired_on: null,
      }),
    );
  });
});

describe("the Dependencies tab", () => {
  const ref = (id: string, name: string) => ({
    id,
    name,
    category: null,
    is_active: true,
    thumbnail_url: null,
  });
  const link = (overrides: Record<string, unknown> = {}) => ({
    id: "a1",
    parent: ref("d1", "Alexa 35"),
    associated: ref("b1", "Battery V-Mount 150 Wh"),
    quantity_per_parent: 2,
    booking_mode: "always",
    pricing: "included",
    show_on_documents: false,
    note: null,
    origin_association_id: null,
    origin_device_name: null,
    created_at: "2026-11-26T10:00:00Z",
    updated_at: "2026-11-26T10:00:00Z",
    ...overrides,
  });

  function routeDependencies(dependencies: unknown, hint?: unknown) {
    const base = vi.mocked(api.get).getMockImplementation()!;
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.startsWith("/devices/d1/dependencies/hint")) return hint;
      if (path === "/devices/d1/dependencies") return dependencies;
      if (path.startsWith("/devices?")) {
        return {
          devices: [
            { ...device(), units: undefined },
            {
              ...device({ id: "b1", name: "Battery V-Mount 150 Wh" }),
              units: undefined,
            },
            { ...device({ id: "c1", name: "Charger" }), units: undefined },
          ],
          total: 3,
          categories: [],
        };
      }
      return base(path);
    });
  }

  async function openTab() {
    render(<DevicePage />);
    fireEvent.click(await screen.findByTestId("device-tab-dependencies"));
  }

  it("lists what the device needs and what needs it, with the origin of a hinted link", async () => {
    routeDependencies({
      needs: [
        link(),
        link({
          id: "a2",
          associated: ref("c1", "Charger"),
          origin_association_id: "x",
          origin_device_name: "Battery V-Mount 150 Wh",
        }),
      ],
      used_by: [
        link({
          id: "a3",
          parent: ref("k1", "Podcast kit cam"),
          associated: ref("d1", "Alexa 35"),
          quantity_per_parent: 1,
        }),
      ],
    });
    await openTab();
    const rows = await screen.findAllByTestId("dependency-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByTestId("dependency-quantity")).toHaveValue("2");
    expect(within(rows[1]).getByTestId("dependency-origin")).toHaveTextContent(
      "added via Battery V-Mount 150 Wh",
    );
    expect(screen.getByTestId("used-by")).toHaveTextContent(
      /Podcast kit cam\s*needs 1 × Alexa 35/,
    );
    // Without a picture, the neutral placeholder.
    expect(
      within(rows[0]).getByTestId("device-thumb-placeholder"),
    ).toBeInTheDocument();
  });

  it("the setup hint shows the pre-multiplied quantity and sends the ticked rows with the link", async () => {
    routeDependencies(
      { needs: [], used_by: [] },
      {
        associated: ref("b1", "Battery V-Mount 150 Wh"),
        rows: [
          {
            origin_association_id: "o1",
            device: ref("c1", "Charger"),
            quantity_per_associated: 1,
            suggested_quantity: 2,
            booking_mode: "always",
            pricing: "included",
            blocked_reason: null,
          },
        ],
      },
    );
    vi.mocked(api.post).mockResolvedValue({ needs: [link()], used_by: [] });
    await openTab();
    fireEvent.click(await screen.findByTestId("dependency-add-open"));
    const dialog = await screen.findByTestId("dependency-dialog");
    expect(within(dialog).getByTestId("dependency-add-why")).toHaveTextContent(
      "Pick the device it needs.",
    );
    const options = await within(dialog).findAllByTestId(
      "device-picker-option",
    );
    expect(options.map((option) => option.textContent)).not.toContain(
      expect.stringContaining("Alexa 35"),
    );
    fireEvent.click(
      within(dialog).getByRole("option", { name: /Battery V-Mount/ }),
    );
    fireEvent.change(within(dialog).getByTestId("dependency-add-quantity"), {
      target: { value: "2" },
    });
    const hint = await within(dialog).findByTestId("dependency-hint");
    expect(hint).toHaveTextContent(
      "Battery V-Mount 150 Wh has its own dependencies: Charger (1 per Battery V-Mount 150 Wh). They will not be booked through Alexa 35 unless you add them here.",
    );
    await waitFor(() =>
      expect(vi.mocked(api.get)).toHaveBeenCalledWith(
        "/devices/d1/dependencies/hint?associated_device_id=b1&quantity_per_parent=2",
      ),
    );
    expect(within(dialog).getByTestId("dependency-hint-quantity")).toHaveValue(
      "2",
    );
    expect(within(dialog).getByTestId("dependency-hint-tick")).toBeChecked();
    fireEvent.click(within(dialog).getByTestId("dependency-add-save"));
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        "/devices/d1/dependencies",
        {
          associated_device_id: "b1",
          quantity_per_parent: 2,
          booking_mode: "always",
          pricing: "included",
          show_on_documents: false,
          note: null,
          from_hint: [{ origin_association_id: "o1", quantity_per_parent: 2 }],
        },
      ),
    );
  });

  it("a loop refused by the API is shown in its own words, and the dialog stays open", async () => {
    const { ApiError } = await import("@/lib/api");
    routeDependencies(
      { needs: [], used_by: [] },
      { associated: ref("b1", "Battery V-Mount 150 Wh"), rows: [] },
    );
    vi.mocked(api.post).mockRejectedValue(
      new ApiError(
        409,
        "Battery V-Mount 150 Wh already depends on Alexa 35 (…), so Alexa 35 cannot depend on Battery V-Mount 150 Wh as well: that would be a loop. Remove one of the links first.",
      ),
    );
    await openTab();
    fireEvent.click(await screen.findByTestId("dependency-add-open"));
    const dialog = await screen.findByTestId("dependency-dialog");
    fireEvent.click(
      (
        await within(dialog).findAllByRole("option", {
          name: /Battery V-Mount/,
        })
      )[0],
    );
    fireEvent.click(within(dialog).getByTestId("dependency-add-save"));
    expect(
      await within(dialog).findByTestId("dependency-add-error"),
    ).toHaveTextContent("that would be a loop");
  });

  it("changing a row sends only that field; removing posts remove", async () => {
    routeDependencies({ needs: [link()], used_by: [] });
    vi.mocked(api.patch).mockResolvedValue({
      needs: [link({ booking_mode: "ask" })],
      used_by: [],
    });
    vi.mocked(api.post).mockResolvedValue({ needs: [], used_by: [] });
    await openTab();
    const row = (await screen.findAllByTestId("dependency-row"))[0];
    fireEvent.change(within(row).getByTestId("dependency-mode"), {
      target: { value: "ask" },
    });
    await waitFor(() =>
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith(
        "/devices/d1/dependencies/a1",
        { booking_mode: "ask" },
      ),
    );
    fireEvent.click(
      within(screen.getAllByTestId("dependency-row")[0]).getByTestId(
        "dependency-remove",
      ),
    );
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        "/devices/d1/dependencies/a1/remove",
        {},
      ),
    );
    expect(await screen.findByTestId("dependencies-empty")).toBeInTheDocument();
  });

  it("staff see the dependencies and cannot change them", async () => {
    seed(STAFF);
    routeGets(device({ purchase_price: null, price_visible: false }));
    routeDependencies({ needs: [link()], used_by: [] });
    await openTab();
    const row = (await screen.findAllByTestId("dependency-row"))[0];
    expect(row).toHaveTextContent("Battery V-Mount 150 Wh");
    expect(row).toHaveTextContent("Always");
    expect(
      within(row).queryByTestId("dependency-mode"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("dependency-add-open")).not.toBeInTheDocument();
  });
});

describe("the Alternatives tab", () => {
  const ref = (id: string, name: string) => ({
    id,
    name,
    category: null,
    is_active: true,
    thumbnail_url: null,
  });
  const row = (
    id: string,
    name: string,
    position: number,
    extra: Record<string, unknown> = {},
  ) => ({
    id,
    device: ref("d1", "Alexa 35"),
    alternative: ref(`dev-${id}`, name),
    position,
    note: null,
    price_hint: null,
    created_at: "2026-11-26T10:00:00Z",
    updated_at: "2026-11-26T10:00:00Z",
    ...extra,
  });

  function routeAlternatives(alternatives: unknown) {
    const base = vi.mocked(api.get).getMockImplementation()!;
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === "/devices/d1/alternatives") return alternatives;
      if (path.startsWith("/devices?")) {
        return {
          devices: [
            { ...device(), units: undefined },
            {
              ...device({ id: "m1", name: "Alexa Mini LF" }),
              units: undefined,
            },
          ],
          total: 2,
          categories: [],
        };
      }
      return base(path);
    });
  }

  async function openTab() {
    render(<DevicePage />);
    fireEvent.click(await screen.findByTestId("device-tab-alternatives"));
  }

  it("shows the list in order, moves send the direction, and the first cannot go up", async () => {
    routeAlternatives({
      alternatives: [
        row("r1", "Alexa Mini LF", 0, { note: "needs a different mount" }),
        row("r2", "Sony Venice 2", 1),
      ],
      offered_by: [
        row("r9", "Alexa 35", 0, { device: ref("v1", "Sony Venice 2") }),
      ],
      reverse_added: null,
    });
    vi.mocked(api.post).mockResolvedValue({
      alternatives: [
        row("r2", "Sony Venice 2", 0),
        row("r1", "Alexa Mini LF", 1),
      ],
      offered_by: [],
      reverse_added: null,
    });
    await openTab();
    const rows = await screen.findAllByTestId("alternative-row");
    expect(rows.map((entry) => entry.textContent)).toEqual([
      expect.stringContaining("Alexa Mini LF"),
      expect.stringContaining("Sony Venice 2"),
    ]);
    expect(within(rows[0]).getByTestId("alternative-note")).toHaveValue(
      "needs a different mount",
    );
    expect(within(rows[0]).getByTestId("alternative-up")).toBeDisabled();
    expect(within(rows[1]).getByTestId("alternative-down")).toBeDisabled();
    expect(screen.getByTestId("offered-by")).toHaveTextContent("Sony Venice 2");
    fireEvent.click(within(rows[1]).getByTestId("alternative-up"));
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        "/devices/d1/alternatives/r2/move",
        { direction: "up" },
      ),
    );
    await waitFor(() =>
      expect(screen.getAllByTestId("alternative-row")[0]).toHaveTextContent(
        "Sony Venice 2",
      ),
    );
  });

  it("adding sends one direction unless the reverse is ticked, and says what happened", async () => {
    routeAlternatives({
      alternatives: [],
      offered_by: [],
      reverse_added: null,
    });
    vi.mocked(api.post).mockResolvedValue({
      alternatives: [row("r1", "Alexa Mini LF", 0)],
      offered_by: [],
      reverse_added: true,
    });
    await openTab();
    fireEvent.click(await screen.findByTestId("alternative-add-open"));
    const dialog = await screen.findByTestId("alternative-dialog");
    expect(
      within(dialog).getByTestId("alternative-add-reverse"),
    ).not.toBeChecked();
    fireEvent.click(
      await within(dialog).findByRole("option", { name: /Alexa Mini LF/ }),
    );
    expect(
      within(dialog).getByTestId("alternative-add-reverse").parentElement,
    ).toHaveTextContent(
      "Also add the reverse — Alexa 35 as an alternative for Alexa Mini LF",
    );
    fireEvent.click(within(dialog).getByTestId("alternative-add-reverse"));
    fireEvent.change(within(dialog).getByTestId("alternative-add-hint"), {
      target: { value: "about -200 per day" },
    });
    fireEvent.click(within(dialog).getByTestId("alternative-add-save"));
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        "/devices/d1/alternatives",
        {
          alternative_device_id: "m1",
          note: null,
          price_hint: "about -200 per day",
          also_reverse: true,
        },
      ),
    );
    expect(await screen.findByTestId("alternatives-notice")).toHaveTextContent(
      "Alexa 35 is now an alternative for Alexa Mini LF too.",
    );
  });

  it("staff see the alternatives and cannot change them", async () => {
    seed(STAFF);
    routeGets(device({ purchase_price: null, price_visible: false }));
    routeAlternatives({
      alternatives: [row("r1", "Alexa Mini LF", 0, { note: "PL mount" })],
      offered_by: [],
      reverse_added: null,
    });
    await openTab();
    const entry = (await screen.findAllByTestId("alternative-row"))[0];
    expect(entry).toHaveTextContent("PL mount");
    expect(
      within(entry).queryByTestId("alternative-up"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("alternative-add-open"),
    ).not.toBeInTheDocument();
  });
});

describe("size, weight and the picture", () => {
  const pictured = {
    image_url: "/files/object/abc.webp?token=t",
    thumbnail_url: "/files/object/abc-thumb.webp?token=t",
  };

  it("weight and size are sent in metric with a dot, only what changed", async () => {
    vi.mocked(api.patch).mockResolvedValue(device({ weight_kg: "12.345" }));
    render(<DevicePage />);
    fireEvent.change(await screen.findByTestId("device-weight"), {
      target: { value: "12,345" },
    });
    fireEvent.change(screen.getByTestId("device-length"), {
      target: { value: "60,5" },
    });
    fireEvent.click(screen.getByTestId("device-save"));
    await waitFor(() =>
      expect(vi.mocked(api.patch)).toHaveBeenCalledWith("/devices/d1", {
        weight_kg: "12.345",
        length_cm: "60.5",
      }),
    );
    expect(screen.getByText("Weight (kg)")).toBeInTheDocument();
    expect(
      screen.getByText("Size: length × width × height (cm)"),
    ).toBeInTheDocument();
  });

  it("without a picture a neutral placeholder is shown, in the header and the Details tab", async () => {
    render(<DevicePage />);
    expect(
      await screen.findByTestId("device-picture-placeholder"),
    ).toHaveTextContent("No picture yet");
    expect(
      screen.getAllByTestId("device-thumb-placeholder").length,
    ).toBeGreaterThan(0);
    expect(screen.queryByTestId("device-thumb")).not.toBeInTheDocument();
  });

  it("a picture shows in the header and the Details tab; uploading sends the file, a refusal is said", async () => {
    const { ApiError } = await import("@/lib/api");
    routeGets(device(pictured));
    render(<DevicePage />);
    expect(await screen.findByTestId("device-picture-image")).toHaveAttribute(
      "src",
      expect.stringContaining("/files/object/abc.webp"),
    );
    expect(screen.getByTestId("device-thumb")).toHaveAttribute(
      "src",
      expect.stringContaining("abc-thumb.webp"),
    );

    vi.mocked(api.upload).mockRejectedValueOnce(
      new ApiError(
        422,
        "That file is not a JPEG, PNG or WebP picture. Choose a photo of the device.",
      ),
    );
    const file = new File(["not a picture"], "camera.jpg", {
      type: "image/jpeg",
    });
    fireEvent.change(screen.getByTestId("device-picture-input"), {
      target: { files: [file] },
    });
    await waitFor(() => expect(vi.mocked(api.upload)).toHaveBeenCalledTimes(1));
    const [path, form] = vi.mocked(api.upload).mock.calls[0];
    expect(path).toBe("/devices/d1/image");
    expect((form as FormData).get("file")).toBe(file);
    expect(await screen.findByTestId("device-picture-error")).toHaveTextContent(
      "not a JPEG, PNG or WebP picture",
    );

    vi.mocked(api.post).mockResolvedValue(device());
    fireEvent.click(screen.getByTestId("device-picture-remove"));
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        "/devices/d1/image/remove",
        {},
      ),
    );
    expect(
      await screen.findByTestId("device-picture-placeholder"),
    ).toBeInTheDocument();
  });

  it("the gear list shows the thumbnail on the row", async () => {
    routeGets(device(pictured));
    render(<GearPage />);
    const row = (await screen.findAllByTestId("gear-row"))[0];
    expect(within(row).getByTestId("device-thumb")).toHaveAttribute(
      "src",
      expect.stringContaining("abc-thumb.webp"),
    );
  });

  it("staff see the picture, the weight and the size, and no way to change them", async () => {
    seed(STAFF);
    routeGets(
      device({
        ...pictured,
        purchase_price: null,
        price_visible: false,
        weight_kg: "3.900",
        length_cm: "60.0",
      }),
    );
    render(<DevicePage />);
    expect(
      await screen.findByTestId("device-picture-image"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("device-picture-input"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("device-weight-read")).toHaveTextContent("3.900");
    expect(screen.getByTestId("device-size-read")).toHaveTextContent(
      "60.0 × ? × ?",
    );
  });
});
