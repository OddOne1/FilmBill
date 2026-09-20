"use client";

import * as React from "react";
import { Command } from "cmdk";
import * as Dialog from "@radix-ui/react-dialog";
import { useRouter } from "next/navigation";
import {
  LayoutDashboard,
  Settings,
  Bell,
  User,
  Palette,
  Brush,
  Search,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface CommandItem {
  id: string;
  label: string;
  icon: React.ElementType;
  href?: string;
  action?: () => void;
  shortcut?: string;
  group: "navigation" | "settings";
}

/**
 * Jump-to navigation, nothing else.
 *
 * FreeFrame's palette also searched projects, folders and assets live. It
 * will search parties, items and documents here — but only once those exist
 * and have a search endpoint to call. A palette that looks like it searches
 * and silently finds nothing is worse than one that plainly offers
 * navigation.
 */
const STATIC_ITEMS: CommandItem[] = [
  {
    id: "home",
    label: "Dashboard",
    icon: LayoutDashboard,
    href: "/",
    group: "navigation",
  },
  {
    id: "notifications",
    label: "Notifications",
    icon: Bell,
    href: "/notifications",
    group: "navigation",
  },
  {
    id: "settings",
    label: "Settings",
    icon: Settings,
    href: "/settings",
    group: "navigation",
  },
  {
    id: "settings-profile",
    label: "Profile",
    icon: User,
    href: "/settings/profile",
    group: "settings",
  },
  {
    id: "settings-appearance",
    label: "Appearance",
    icon: Palette,
    href: "/settings/appearance",
    group: "settings",
  },
  {
    id: "settings-branding",
    label: "Branding",
    icon: Brush,
    href: "/settings/branding",
    group: "settings",
  },
];

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const router = useRouter();
  const [query, setQuery] = React.useState("");

  // Reset query when dialog closes
  React.useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  function handleSelect(item: CommandItem) {
    onOpenChange(false);
    if (item.action) {
      item.action();
    } else if (item.href) {
      router.push(item.href);
    }
  }

  const navItems = STATIC_ITEMS.filter((i) => i.group === "navigation");
  const settingsItems = STATIC_ITEMS.filter((i) => i.group === "settings");

  const groupHeadingClass =
    "[&>[cmdk-group-heading]]:px-2 [&>[cmdk-group-heading]]:py-1.5 [&>[cmdk-group-heading]]:text-2xs [&>[cmdk-group-heading]]:font-medium [&>[cmdk-group-heading]]:text-text-tertiary [&>[cmdk-group-heading]]:uppercase [&>[cmdk-group-heading]]:tracking-wider";

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:duration-200" />
        <Dialog.Content className="fixed left-1/2 top-[20%] z-50 w-full max-w-lg -translate-x-1/2 -translate-y-0 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:duration-200">
          <Dialog.Title className="sr-only">Command Palette</Dialog.Title>
          <Command
            className="overflow-hidden rounded-xl border border-border bg-bg-elevated shadow-2xl"
            loop
            shouldFilter={true}
          >
            <div className="flex items-center border-b border-border px-3 gap-2">
              <Search className="h-4 w-4 text-text-tertiary shrink-0" />
              <Command.Input
                placeholder="Jump to..."
                value={query}
                onValueChange={setQuery}
                className="h-12 w-full bg-transparent text-sm text-text-primary placeholder:text-text-tertiary focus:outline-none"
              />
            </div>
            <Command.List className="max-h-80 overflow-y-auto p-1.5">
              <Command.Empty className="py-8 text-center text-sm text-text-tertiary">
                No results found
              </Command.Empty>

              <Command.Group heading="Navigation" className={groupHeadingClass}>
                {navItems.map((item) => (
                  <CommandItemRow
                    key={item.id}
                    item={item}
                    onSelect={() => handleSelect(item)}
                  />
                ))}
              </Command.Group>

              <Command.Separator className="my-1 h-px bg-border-secondary" />

              <Command.Group heading="Settings" className={groupHeadingClass}>
                {settingsItems.map((item) => (
                  <CommandItemRow
                    key={item.id}
                    item={item}
                    onSelect={() => handleSelect(item)}
                  />
                ))}
              </Command.Group>
            </Command.List>

            <div className="border-t border-border px-3 py-2">
              <p className="text-2xs text-text-tertiary">
                <kbd className="rounded border border-border px-1 py-0.5 font-mono text-2xs">
                  ↑↓
                </kbd>{" "}
                navigate{" "}
                <kbd className="rounded border border-border px-1 py-0.5 font-mono text-2xs">
                  ↵
                </kbd>{" "}
                select{" "}
                <kbd className="rounded border border-border px-1 py-0.5 font-mono text-2xs">
                  esc
                </kbd>{" "}
                close
              </p>
            </div>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function CommandItemRow({
  item,
  onSelect,
}: {
  item: CommandItem;
  onSelect: () => void;
}) {
  const Icon = item.icon;
  return (
    <Command.Item
      value={item.label}
      onSelect={onSelect}
      className={cn(
        "flex cursor-pointer items-center gap-3 rounded-md px-2 py-2 text-sm text-text-secondary",
        "data-[selected=true]:bg-bg-hover data-[selected=true]:text-text-primary",
        "transition-colors",
      )}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="flex-1">{item.label}</span>
      {item.shortcut && (
        <span className="text-2xs text-text-tertiary">{item.shortcut}</span>
      )}
    </Command.Item>
  );
}
