import { SlidersHorizontal, X } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

/** Filters collapse behind a toggle on small screens and stay open on wide ones. */
export function FilterBar({ children, active, onClear }: { children: ReactNode; active: number; onClear: () => void }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="filters">
      <div className="filters__toggle">
        <button type="button" className="button button--secondary" aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)}>
          <SlidersHorizontal size={14} aria-hidden="true" /> Filters{active ? ` (${active})` : ""}
        </button>
      </div>
      <div id={id} className={open ? "filters__fields filters__fields--open" : "filters__fields"}>
        {children}
        {active ? (
          <button type="button" className="button button--ghost" onClick={onClear}>
            <X size={14} aria-hidden="true" /> Clear filters
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function SelectField({
  label,
  value,
  onChange,
  options,
  anyLabel = "Any",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: readonly (string | [string, string])[];
  anyLabel?: string;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{anyLabel}</option>
        {options.map((option) => {
          const [optionValue, optionLabel] = Array.isArray(option) ? option : [option, option];
          return (
            <option key={optionValue} value={optionValue}>
              {optionLabel}
            </option>
          );
        })}
      </select>
    </div>
  );
}

export function SearchField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const id = useId();
  const [draft, setDraft] = useState(value);
  return (
    <form
      className="field field--search"
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        onChange(draft.trim().slice(0, 100));
      }}
    >
      <label htmlFor={id}>{label}</label>
      <input id={id} type="search" value={draft} maxLength={100} placeholder={placeholder} onChange={(e) => setDraft(e.target.value)} onBlur={() => draft !== value && onChange(draft.trim().slice(0, 100))} />
    </form>
  );
}

export function DateField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} type="date" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
