import { Link } from "react-router-dom";
import type { ReactNode } from "react";

/**
 * Breadcrumb page header matching the mockup pattern:
 *   Employee / Aarav Mehta        <- h1 with "List / Detail" crumbs
 *   Optional caption under it
 *   Right side: optional action buttons (EDIT / VALIDATE / ...)
 */
export function PageHeader({
  title,
  subtitle,
  actions,
  backTo,
  backLabel,
}: {
  title: ReactNode;
  subtitle?: string;
  actions?: ReactNode;
  backTo?: string;
  backLabel?: string;
}) {
  return (
    <div className="page-head">
      <div>
        {backTo && (
          <Link className="back-link" to={backTo}>
            ← {backLabel ?? "Back to list"}
          </Link>
        )}
        <h2>{title}</h2>
        {subtitle && <div className="page-sub">{subtitle}</div>}
      </div>
      {actions && <div className="row page-head-actions">{actions}</div>}
    </div>
  );
}
