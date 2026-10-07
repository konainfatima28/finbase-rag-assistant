"use client";

import { Landmark, Moon, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSyncExternalStore } from "react";

const NAV = [
  { href: "/", label: "Assistant" },
  { href: "/eval", label: "Evaluation" },
  { href: "/about", label: "About" },
];

function subscribe(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

const isDark = () => document.documentElement.classList.contains("dark");

function ThemeToggle() {
  // The <html> class (set before paint by the layout script) is the source of truth.
  const dark = useSyncExternalStore(subscribe, isDark, () => false);
  const toggle = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try {
      window.localStorage.setItem("finbase.theme", next ? "dark" : "light");
    } catch {
      /* ignore */
    }
  };
  return (
    <button type="button" onClick={toggle} aria-label={dark ? "Switch to light mode" : "Switch to dark mode"} className="rounded-lg p-2 text-ink-2 hover:bg-surface-2">
      {dark ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  );
}

export function SiteHeader() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-surface/90 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-3 px-3 sm:px-4">
        <Link href="/" className="flex items-center gap-2 font-semibold text-ink">
          <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-brand text-brand-ink">
            <Landmark size={18} aria-hidden="true" />
          </span>
          <span className="hidden sm:inline">FinBase Support</span>
        </Link>
        <nav aria-label="Main" className="ml-2 flex gap-1">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={pathname === item.href ? "page" : undefined}
              className={`rounded-lg px-2.5 py-1.5 text-sm ${pathname === item.href ? "bg-brand-soft font-semibold text-brand" : "text-ink-2 hover:bg-surface-2"}`}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <span className="ml-auto" />
        <ThemeToggle />
      </div>
    </header>
  );
}
