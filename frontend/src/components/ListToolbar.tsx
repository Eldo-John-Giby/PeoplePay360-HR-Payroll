import type { ReactNode } from "react";

/**
 * List-view toolbar matching the spec: leading search input with icon,
 * utility children (filters / view toggles) to its right, NEW primary
 * button on the far left.
 */
export function ListToolbar({
  newLabel = "NEW",
  onNew,
  newDisabled,
  search,
  onSearch,
  searchPlaceholder,
  children,
}: {
  newLabel?: string;
  onNew?: () => void;
  newDisabled?: boolean;
  search: string;
  onSearch: (v: string) => void;
  searchPlaceholder: string;
  children?: ReactNode;
}) {
  return (
    <div className="toolbar">
      {onNew && (
        <button
          type="button"
          className="btn btn-primary btn-sm"
          disabled={newDisabled}
          onClick={onNew}
        >
          {newLabel}
        </button>
      )}
      <div className="row" style={{ gap: 0, flexWrap: "nowrap", position: "relative" }}>
        <span
          aria-hidden="true"
          style={{
            position: "absolute",
            left: 10,
            top: "50%",
            transform: "translateY(-50%)",
            display: "grid",
            color: "var(--muted-2)",
            pointerEvents: "none",
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8" />
            <path d="m21 21-4.35-4.35" />
          </svg>
        </span>
        <input
          className="toolbar-search"
          type="text"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder={searchPlaceholder}
          style={{ paddingLeft: 30 }}
        />
      </div>
      {children}
    </div>
  );
}
