"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui";
import { SEVERITY_STYLES, VIEWS, type ViewId } from "@/lib/config";
import type { Bundle, DefectView } from "@/lib/types";

export interface CommandItem {
  id: string;
  title: string;
  subtitle?: string;
  category: "Views" | "Defects" | "Metrics" | "Tables" | "Actions" | "Questions";
  icon: string;
  badge?: { text: string; tone?: "accent" | "ok" | "warn" | "bad" | "mono" | "neutral" };
  keywords?: string[];
  onSelect: () => void;
}

interface Props {
  isOpen: boolean;
  onClose: () => void;
  bundle: Bundle;
  defects: DefectView[];
  onNavigateView: (view: ViewId, param?: string) => void;
  onNavigateDefect: (code: string) => void;
  onOpenEvaluatorGuide: () => void;
  onOpenExportCenter: () => void;
}

export default function CommandPalette({
  isOpen,
  onClose,
  bundle,
  defects,
  onNavigateView,
  onNavigateDefect,
  onOpenEvaluatorGuide,
  onOpenExportCenter,
}: Props) {
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Focus input on open
  useEffect(() => {
    if (isOpen) {
      setQuery("");
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  // Build searchable items catalogue
  const allItems = useMemo<CommandItem[]>(() => {
    const items: CommandItem[] = [];

    // 1. Quick Actions & Tools
    items.push({
      id: "action-evaluator-guide",
      title: "Interviewer / Evaluator Guide",
      subtitle: "Open 60-second summary and architectural rationale",
      category: "Actions",
      icon: "🎯",
      badge: { text: "Guide", tone: "accent" },
      keywords: ["guide", "interview", "rubric", "evaluator", "scoring", "rationale"],
      onSelect: onOpenEvaluatorGuide,
    });

    items.push({
      id: "action-export-center",
      title: "Export Artifacts & Clean CSVs",
      subtitle: "Download warehouse datasets, quarantine records, or catalog JSON",
      category: "Actions",
      icon: "📥",
      badge: { text: "Download", tone: "accent" },
      keywords: ["export", "download", "csv", "json", "quarantine", "markdown", "raw", "clean"],
      onSelect: onOpenExportCenter,
    });

    items.push({
      id: "action-sql-sandbox",
      title: "Interactive In-Browser SQL Sandbox",
      subtitle: "Run live SQL queries against virtual Star Schema warehouse tables",
      category: "Actions",
      icon: "⚡",
      badge: { text: "SQL", tone: "accent" },
      keywords: ["sql", "query", "select", "sandbox", "playground", "database", "tables"],
      onSelect: () => onNavigateView("sql"),
    });

    // 2. Navigation Views
    for (const view of VIEWS) {
      items.push({
        id: `view-${view.id}`,
        title: `Navigate to ${view.label}`,
        subtitle: `Jump to ${view.label} (${view.group} view)`,
        category: "Views",
        icon: "🧭",
        badge: { text: "Page", tone: "neutral" },
        keywords: ["go", "jump", "page", "tab", view.id, view.label],
        onSelect: () => onNavigateView(view.id),
      });
    }

    // 3. Defect Classes (17 total)
    for (const defect of defects) {
      const countText = defect.detected_count ?? defect.expected_count ?? 0;
      items.push({
        id: `defect-${defect.code}`,
        title: `${defect.code}: ${defect.title}`,
        subtitle: `${defect.dataset.toUpperCase()} • ${defect.decision} (${countText} affected)`,
        category: "Defects",
        icon: "🐛",
        badge: {
          text: defect.severity.toUpperCase(),
          tone:
            defect.severity === "critical"
              ? "bad"
              : defect.severity === "high"
              ? "warn"
              : "neutral",
        },
        keywords: [
          defect.code,
          defect.title,
          defect.dataset,
          defect.decision,
          defect.audit?.action ?? "",
          "defect",
          "bug",
          "anomaly",
        ],
        onSelect: () => onNavigateDefect(defect.code),
      });
    }

    // 4. Analytics Metrics
    if (bundle.analytics?.metrics) {
      for (const [key, metric] of Object.entries(bundle.analytics.metrics)) {
        items.push({
          id: `metric-${key}`,
          title: metric.title || key,
          subtitle: metric.description || `SQL Metric: ${key}`,
          category: "Metrics",
          icon: "📊",
          badge: { text: `${metric.rows?.length || 0} rows`, tone: "neutral" },
          keywords: [key, metric.title || "", "metric", "kpi", "analytics", "sql", "chart"],
          onSelect: () => onNavigateView("analytics", `metric:${key}`),
        });
      }
    }

    // 5. Data Profiling & Tables
    const tables = ["stores", "products", "transactions", "fact_sales", "dim_store", "dim_product", "dim_customer", "dim_date", "quarantine"];
    for (const tbl of tables) {
      items.push({
        id: `table-${tbl}`,
        title: `Table / Dataset: ${tbl}`,
        subtitle: `Inspect data schema and profiling for ${tbl}`,
        category: "Tables",
        icon: "🗄️",
        badge: { text: "Schema", tone: "neutral" },
        keywords: [tbl, "table", "schema", "column", "dataset", "profile"],
        onSelect: () => {
          if (["stores", "products", "transactions"].includes(tbl)) {
            onNavigateView("profile", `dataset:${tbl}`);
          } else {
            onNavigateView("schema");
          }
        },
      });
    }

    // 6. Interview Questions
    const questions = [
      "Why preserve reported total_amount rather than recomputing quantity * unit_price?",
      "Why quarantine orphaned foreign keys rather than dropping or creating placeholders?",
      "Why are TX-10 return rows kept with negative quantities instead of dropped?",
      "How does the star schema isolate analytical queries from source data anomalies?",
      "What is the financial tie-out reconciliation delta ($158,044.29)?",
      "Which store breached the 10% return rate SLA and why?",
    ];

    questions.forEach((q, idx) => {
      items.push({
        id: `question-${idx}`,
        title: q,
        subtitle: "Staff Data Architect & Executive Copilot Answer",
        category: "Questions",
        icon: "💬",
        badge: { text: "Copilot", tone: "ok" },
        keywords: [q, "interview", "question", "rationale", "copilot", "answer"],
        onSelect: () => onNavigateView("assistant"),
      });
    });

    return items;
  }, [bundle, defects, onNavigateView, onNavigateDefect, onOpenEvaluatorGuide, onOpenExportCenter]);

  // Filter items by query
  const filteredItems = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allItems;

    return allItems.filter((item) => {
      const matchTitle = item.title.toLowerCase().includes(q);
      const matchSub = item.subtitle?.toLowerCase().includes(q) ?? false;
      const matchCat = item.category.toLowerCase().includes(q);
      const matchKey = item.keywords?.some((k) => k.toLowerCase().includes(q)) ?? false;
      return matchTitle || matchSub || matchCat || matchKey;
    });
  }, [allItems, query]);

  // Handle keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!isOpen) return;

      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev < filteredItems.length - 1 ? prev + 1 : 0));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev > 0 ? prev - 1 : Math.max(0, filteredItems.length - 1)));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (filteredItems[selectedIndex]) {
          filteredItems[selectedIndex].onSelect();
          onClose();
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, filteredItems, selectedIndex, onClose]);

  // Scroll active item into view
  useEffect(() => {
    if (listRef.current) {
      const activeEl = listRef.current.children[selectedIndex] as HTMLElement | undefined;
      if (activeEl) {
        activeEl.scrollIntoView({ block: "nearest" });
      }
    }
  }, [selectedIndex]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/75 p-4 pt-16 backdrop-blur-md transition-opacity duration-150 animate-in fade-in"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-2xl overflow-hidden rounded-2xl border border-accent/40 bg-[#0d1017] shadow-2xl ring-1 ring-white/10"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search Header */}
        <div className="flex items-center border-b border-line px-4 py-3.5 bg-panel">
          <span className="text-lg text-ink-dim mr-3">🔍</span>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
            placeholder="Type a command, defect (TX-03), table, metric, or question..."
            className="w-full bg-transparent text-sm text-ink placeholder-ink-faint focus:outline-none"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              className="ml-2 rounded px-1.5 py-0.5 text-xs text-ink-dim hover:text-ink"
            >
              Clear
            </button>
          )}
          <kbd className="ml-2 rounded border border-line bg-raised px-2 py-0.5 font-mono text-2xs text-ink-faint">
            ESC
          </kbd>
        </div>

        {/* Categories Bar */}
        <div className="flex items-center gap-1.5 border-b border-line bg-[#090b0f] px-4 py-2 overflow-x-auto text-2xs text-ink-dim scrollbar-none">
          <span className="font-semibold text-ink-faint uppercase tracking-wider text-[10px]">Filter:</span>
          {["All", "Actions", "Defects", "Views", "Metrics", "Tables", "Questions"].map((cat) => {
            const isSelected = (!query && cat === "All") || query.toLowerCase() === cat.toLowerCase();
            return (
              <button
                key={cat}
                type="button"
                onClick={() => setQuery(cat === "All" ? "" : cat)}
                className={`rounded-md px-2 py-1 transition-colors ${
                  isSelected
                    ? "bg-accent/20 text-accent font-semibold"
                    : "bg-raised/60 hover:bg-raised hover:text-ink"
                }`}
              >
                {cat}
              </button>
            );
          })}
        </div>

        {/* Results List */}
        <div ref={listRef} className="max-h-[60vh] overflow-y-auto p-2 divide-y divide-line/30 space-y-1">
          {filteredItems.length === 0 ? (
            <div className="py-12 text-center text-xs text-ink-faint">
              No matching commands or defects found for &ldquo;{query}&rdquo;.
            </div>
          ) : (
            filteredItems.map((item, idx) => {
              const isSelected = idx === selectedIndex;
              return (
                <div
                  key={item.id}
                  onClick={() => {
                    item.onSelect();
                    onClose();
                  }}
                  onMouseEnter={() => setSelectedIndex(idx)}
                  className={`group flex cursor-pointer items-center justify-between gap-3 rounded-xl px-3 py-2.5 transition-colors ${
                    isSelected
                      ? "bg-accent/15 border border-accent/30 text-ink shadow-sm"
                      : "text-ink-dim hover:bg-raised/50"
                  }`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-base flex-shrink-0">{item.icon}</span>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className={`text-xs font-semibold truncate ${isSelected ? "text-accent" : "text-ink"}`}>
                          {item.title}
                        </span>
                        <span className="rounded bg-raised px-1.5 py-0.2 font-mono text-[10px] text-ink-faint">
                          {item.category}
                        </span>
                      </div>
                      {item.subtitle && (
                        <p className="truncate text-2xs text-ink-dim mt-0.5">{item.subtitle}</p>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-2 flex-shrink-0">
                    {item.badge && (
                      <Badge tone={item.badge.tone || "neutral"} className="text-[10px] py-0.5">
                        {item.badge.text}
                      </Badge>
                    )}
                    {isSelected && (
                      <span className="text-accent text-xs font-mono">↵</span>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Footer info bar */}
        <div className="flex items-center justify-between border-t border-line bg-panel px-4 py-2 text-2xs text-ink-faint">
          <div className="flex items-center gap-3">
            <span><kbd className="font-mono bg-raised px-1.5 py-0.5 rounded border border-line">↑</kbd> <kbd className="font-mono bg-raised px-1.5 py-0.5 rounded border border-line">↓</kbd> navigate</span>
            <span><kbd className="font-mono bg-raised px-1.5 py-0.5 rounded border border-line">↵</kbd> select</span>
          </div>
          <span>{filteredItems.length} available items</span>
        </div>
      </div>
    </div>
  );
}
