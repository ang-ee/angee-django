import * as React from "react";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import { tabsVariants } from "../ui/tabs";

type NavigateHandler = (to: string) => void;

export const sectionNavVariants = tv({
  slots: {
    root: "shrink-0",
    list: "flex gap-1",
    link:
      "min-w-0 disabled:cursor-not-allowed disabled:opacity-60 aria-disabled:cursor-not-allowed aria-disabled:opacity-60",
  },
  variants: {
    orientation: {
      horizontal: {
        root: "min-w-0",
        list: "min-w-0 flex-row flex-wrap items-center",
        link: "",
      },
      vertical: {
        list: "flex-col",
        link:
          "inline-flex h-8 cursor-pointer items-center gap-1 rounded-6 px-3 text-13 font-medium text-fg-muted outline-none transition-colors hover:text-fg focus-visible:focus-ring data-[active]:bg-brand-soft data-[active]:text-brand-soft-text data-[active]:hover:bg-brand-soft data-[active]:hover:text-brand-soft-text",
      },
    },
  },
  defaultVariants: {
    orientation: "horizontal",
  },
});

type SectionNavRecipeProps = VariantProps<typeof sectionNavVariants>;

export interface SectionNavItem {
  id: string;
  label: React.ReactNode;
  href?: string;
  active?: boolean;
  disabled?: boolean;
  onSelect?: () => void;
}

export type SectionNavProps = Omit<
  React.HTMLAttributes<HTMLElement>,
  "className"
> &
  Pick<SectionNavRecipeProps, "orientation"> & {
    className?: string;
    items?: readonly SectionNavItem[];
    label?: string;
    onNavigate?: NavigateHandler;
  };

export const SectionNav = React.forwardRef<HTMLElement, SectionNavProps>(
  function SectionNav(
    {
      children,
      className,
      items,
      label = "Sections",
      onNavigate,
      orientation = "horizontal",
      ...props
    },
    ref,
  ) {
    const styles = sectionNavVariants({ orientation });
    const pageTabStyles = tabsVariants({ variant: "page" });
    const horizontalLinkClassName = pageTabStyles.tab();

    return (
      <nav
        ref={ref}
        aria-label={label}
        className={styles.root({ className })}
        {...props}
      >
        {children ?? (
          <div className={styles.list()}>
            {(items ?? []).map((item) => {
              const active = item.active ?? false;
              const linkClassName = cn(
                orientation === "horizontal" ? horizontalLinkClassName : null,
                styles.link(),
              );
              const ariaCurrent = getAriaCurrent(item, active);

              if (item.href) {
                return (
                  <a
                    key={item.id}
                    aria-current={ariaCurrent}
                    aria-disabled={item.disabled ? true : undefined}
                    className={linkClassName}
                    data-active={active ? true : undefined}
                    href={item.disabled ? undefined : item.href}
                    tabIndex={item.disabled ? -1 : undefined}
                    onClick={(event) =>
                      handleSectionLinkClick(event, item, onNavigate)
                    }
                  >
                    {item.label}
                  </a>
                );
              }

              return (
                <button
                  key={item.id}
                  type="button"
                  aria-current={ariaCurrent}
                  className={cn(linkClassName, "cursor-pointer")}
                  data-active={active ? true : undefined}
                  disabled={item.disabled}
                  onClick={item.onSelect}
                >
                  {item.label}
                </button>
              );
            })}
          </div>
        )}
      </nav>
    );
  },
);
SectionNav.displayName = "SectionNav";

function getAriaCurrent(
  item: SectionNavItem,
  active: boolean,
): React.AriaAttributes["aria-current"] {
  if (!active) return undefined;
  if (!item.href) return true;
  return item.href.startsWith("#") ? true : "page";
}

function handleSectionLinkClick(
  event: React.MouseEvent<HTMLAnchorElement>,
  item: SectionNavItem,
  onNavigate: NavigateHandler | undefined,
): void {
  if (item.disabled) {
    event.preventDefault();
    return;
  }

  item.onSelect?.();

  if (!item.href || !onNavigate || item.href.startsWith("#")) return;
  if (event.defaultPrevented || event.button !== 0) return;
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;

  const target = event.currentTarget.getAttribute("target");
  if (target && target !== "_self") return;

  event.preventDefault();
  onNavigate(item.href);
}
