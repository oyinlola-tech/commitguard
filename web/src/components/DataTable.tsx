import type { ReactNode } from "react";
import { Link } from "react-router";

export interface Column<T> {
  key: string;
  header: string;
  cell: (row: T) => ReactNode;
  /** Shown first on mobile cards and linked to the row's detail page. */
  primary?: boolean;
  align?: "start" | "end";
  hideOnMobile?: boolean;
  width?: string;
}

/**
 * A semantic table on wide screens that becomes a list of cards on narrow
 * screens (CSS only, same DOM). The primary cell holds a real link, stretched
 * over the row, so rows are keyboard- and screen-reader-navigable.
 */
export function DataTable<T>({
  caption,
  columns,
  rows,
  rowKey,
  rowHref,
}: {
  caption: string;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  rowHref?: (row: T) => string;
}) {
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col" className={column.align === "end" ? "align-end" : undefined} style={column.width ? { width: column.width } : undefined}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const href = rowHref?.(row);
            return (
              <tr key={rowKey(row)} className={href ? "table__row--link" : undefined}>
                {columns.map((column) => (
                  <td
                    key={column.key}
                    data-label={column.header}
                    className={[
                      column.align === "end" ? "align-end" : "",
                      column.primary ? "table__primary" : "",
                      column.hideOnMobile ? "hide-mobile" : "",
                    ]
                      .filter(Boolean)
                      .join(" ") || undefined}
                  >
                    {column.primary && href ? (
                      <Link to={href} className="row-link">
                        {column.cell(row)}
                      </Link>
                    ) : (
                      column.cell(row)
                    )}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
