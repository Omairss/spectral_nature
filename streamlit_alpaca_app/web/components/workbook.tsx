"use client";

import type { ReactNode } from "react";

export type TableColumn<Row extends Record<string, unknown>> = {
  key: keyof Row & string;
  label: string;
  align?: "left" | "right";
  render?: (value: unknown, row: Row) => ReactNode;
};

export function MetricStrip({ items }: { items: Array<{ label: string; value: ReactNode; tone?: string }> }) {
  return (
    <dl className="workbook-metrics">
      {items.map((item) => (
        <div key={item.label}>
          <dt>{item.label}</dt>
          <dd className={item.tone || ""}>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function DataTable<Row extends Record<string, unknown>>({
  columns,
  rows,
  rowKey,
  onRowClick,
  selectedKey,
}: {
  columns: Array<TableColumn<Row>>;
  rows: Row[];
  rowKey: (row: Row, index: number) => string;
  onRowClick?: (row: Row) => void;
  selectedKey?: string;
}) {
  return (
    <div className="data-table-frame">
      <table className="data-table">
        <thead><tr>{columns.map((column) => <th className={column.align === "right" ? "numeric" : ""} key={column.key}>{column.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => {
            const key = rowKey(row, index);
            return (
              <tr
                className={`${onRowClick ? "selectable" : ""} ${selectedKey === key ? "selected" : ""}`.trim()}
                key={key}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
              >
                {columns.map((column) => (
                  <td className={column.align === "right" ? "numeric" : ""} key={column.key}>
                    {column.render ? column.render(row[column.key], row) : String(row[column.key] ?? "—")}
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

export function WorkbookTabs({
  items,
  active,
  onChange,
  label,
}: {
  items: Array<{ key: string; label: string }>;
  active: string;
  onChange: (key: string) => void;
  label: string;
}) {
  return (
    <nav aria-label={label} className="workbook-tabs">
      {items.map((item) => (
        <button aria-current={active === item.key ? "page" : undefined} className={active === item.key ? "active" : ""} key={item.key} onClick={() => onChange(item.key)} type="button">
          {item.label}
        </button>
      ))}
    </nav>
  );
}
