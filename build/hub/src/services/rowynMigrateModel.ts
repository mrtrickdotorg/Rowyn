export interface WindowsInstall {
  device: string;
  mounted: boolean;
  mountpoint: string | null;
  profiles: string[];
}

export interface CategoryReport {
  category: string;
  copied_files: number;
  copied_bytes: number;
  skipped: number;
  failed: number;
  reasons: string[];
}

export interface ImportReport {
  profile: string;
  categories: CategoryReport[];
  detail: string;
}

export interface MigrateCategory {
  key: string;
  label: string;
  detail: string;
}

/** The fixed category set the backend accepts — nothing else is offered. */
export const MIGRATE_CATEGORIES: MigrateCategory[] = [
  { key: 'documents', label: 'Documents', detail: 'Into Documents → From Windows' },
  { key: 'pictures', label: 'Pictures', detail: 'Into Pictures → From Windows' },
  { key: 'desktop', label: 'Desktop', detail: 'Into Desktop → From Windows' },
  { key: 'downloads', label: 'Downloads', detail: 'Into Downloads → From Windows' },
  { key: 'bookmarks', label: 'Browser bookmarks', detail: 'Chrome/Edge bookmarks converted to an importable HTML file in Documents → Bookmarks; Firefox bookmark files copied as-is' },
  { key: 'wallpaper', label: 'Wallpaper', detail: 'The current Windows wallpaper, copied to Pictures and applied when possible' },
];

const CATEGORY_LABELS: Record<string, string> = Object.fromEntries(
  MIGRATE_CATEGORIES.map((category) => [category.key, category.label]),
);

export function categoryLabel(key: string): string {
  return CATEGORY_LABELS[key] ?? key;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function categorySummary(report: CategoryReport): string {
  const parts = [
    `${categoryLabel(report.category)}: ${report.copied_files} file(s) copied (${formatBytes(report.copied_bytes)})`,
  ];
  if (report.skipped) parts.push(`${report.skipped} skipped`);
  if (report.failed) parts.push(`${report.failed} failed`);
  return parts.join(', ');
}
