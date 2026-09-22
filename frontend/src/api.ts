export type Recording = {
  id: string; filename: string; path: string; camera_id: string; camera_name: string; camera_color: string;
  size: number; mtime: number; start: number | null; end: number | null; duration: number | null;
  width: number | null; height: number | null; fps: number | null; video_codec: string | null;
  audio_codec: string | null; time_source: string; time_warning: string | null; stream: string | null;
  triggers: string[]; triggers_known: boolean; poster: boolean; proxy: boolean; status: string;
  note: string; bookmarked: boolean; error: string | null;
  sprite: { width: number; height: number; columns: number; rows: number; timestamps: number[] } | null;
  probe?: unknown; trigger_sources?: {kind: string; source: string}[];
  event_match?: {start: number; end: number; captured_at: number; offset_seconds: number; device_filename: string} | null;
  event_recovery_status?: string;
  jobs?: {kind: string; status: string; error: string | null}[];
};
export type Camera = {
  id: string; name: string; device_host: string | null; folder: string | null; color: string;
  status: string; recordings: number; bytes: number; last_seen: number | null;
  metadata: { captured_at?: number; coverage?: Record<string,string>;
    host?: {GetDevInfo?: {DevInfo?: {model?: string; firmVer?: string; hardVer?: string}}};
    channel?: Record<string,unknown>;
  };
};
export type Status = {
  archive: string; timezone: string; read_only: boolean;
  counts: {recordings: number; bytes: number; seconds: number; previews: number; errors: number; pending: number; cameras: number; first_day: string | null; last_day: string | null; event_tagged: number};
  event_recovery?: {searched_days: number; total_days: number; failed_days: number; empty_days: number};
  source: {available: boolean | null; error?: string};
  scan: {status: string; seen?: number; added?: number; error?: string};
  worker: {online: boolean}; cache: {bytes: number; budget: number};
  collector: {updated_at?: number; error?: string};
};
export type Page = {items: Recording[]; next_cursor: string | null};
export type Timeline = {start: number; end: number; bin_seconds: number; lanes: {camera: string; color: string; bins: number[]; events: number[]; first: (string | null)[]}[]};

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { ...options, headers: {'Content-Type': 'application/json', ...options?.headers} });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}
export const media = (recording: Recording, kind: string) => `/api/media/${recording.id}/${kind}?v=${recording.mtime}`;
export function bytes(value: number) {
  if (value < 1000) return `${value} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let amount = value / 1000; let index = 0;
  while (amount >= 1000 && index < units.length - 1) { amount /= 1000; index++; }
  return `${amount.toFixed(amount >= 100 ? 0 : 1)} ${units[index]}`;
}
export function duration(seconds: number | null) {
  if (seconds === null) return '—';
  const value = Math.max(0, Math.round(seconds));
  return value >= 3600 ? `${Math.floor(value / 3600)}:${String(Math.floor(value / 60) % 60).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`
    : `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}
export function clock(stamp: number | null, timezone: string, seconds = false) {
  return stamp ? new Intl.DateTimeFormat('en-GB', {timeZone: timezone, hour: '2-digit', minute: '2-digit', ...(seconds ? {second: '2-digit'} : {})}).format(stamp * 1000) : 'Time unknown';
}
export function dayLabel(day: string) {
  return day ? new Intl.DateTimeFormat('en-GB', {day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(`${day}T12:00:00Z`)) : 'All dates';
}
