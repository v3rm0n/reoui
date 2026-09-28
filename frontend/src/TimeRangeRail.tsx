import { useRef, useState } from 'react';
import type { PointerEvent, KeyboardEvent } from 'react';
import { clock } from './api';
import type { Timeline } from './api';

export type TimeSlice = { start: number; end: number };
type DragMode = 'new' | 'move' | 'start' | 'end';
type Drag = { pointerId: number; mode: DragMode; anchor: number; initial: TimeSlice | null; rect: DOMRect };

const BINS = 96;
const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));

export function formatTimeSlice(slice: TimeSlice, timeline: Timeline, timezone: string) {
  const step = (timeline.end - timeline.start) / BINS;
  return `${clock(timeline.start + slice.start * step, timezone)}–${clock(timeline.start + slice.end * step, timezone)}`;
}

function movedRange(drag: Drag, bin: number): TimeSlice {
  const initial = drag.initial;
  if (drag.mode === 'new' || !initial) {
    return bin === drag.anchor
      ? {start: Math.min(bin, BINS - 1), end: Math.min(bin + 1, BINS)}
      : {start: Math.min(drag.anchor, bin), end: Math.max(drag.anchor, bin)};
  }
  if (drag.mode === 'start') return {start: clamp(bin, 0, initial.end - 1), end: initial.end};
  if (drag.mode === 'end') return {start: initial.start, end: clamp(bin, initial.start + 1, BINS)};
  const width = initial.end - initial.start;
  const start = clamp(initial.start + bin - drag.anchor, 0, BINS - width);
  return {start, end: start + width};
}

export function TimeRangeRail({timeline, timezone, value, onChange}: {
  timeline: Timeline; timezone: string; value: TimeSlice | null; onChange: (slice: TimeSlice | null) => void;
}) {
  const [draft, setDraft] = useState<TimeSlice | null>(null);
  const drag = useRef<Drag | null>(null);
  const active = draft ?? value;
  const label = active ? formatTimeSlice(active, timeline, timezone) : '';
  const binAt = (clientX: number, rect: DOMRect) => clamp(Math.round((clientX - rect.left) / rect.width * BINS), 0, BINS);

  function pointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    const mode = (event.target as Element).closest<HTMLElement>('[data-range-action]')?.dataset.rangeAction as DragMode | undefined;
    const rect = event.currentTarget.getBoundingClientRect();
    const anchor = binAt(event.clientX, rect);
    drag.current = {pointerId: event.pointerId, mode: mode ?? 'new', anchor, initial: value, rect};
    event.currentTarget.setPointerCapture(event.pointerId);
    setDraft(movedRange(drag.current, anchor));
    event.preventDefault();
  }

  function pointerMove(event: PointerEvent<HTMLDivElement>) {
    if (drag.current?.pointerId !== event.pointerId) return;
    setDraft(movedRange(drag.current, binAt(event.clientX, drag.current.rect)));
  }

  function pointerUp(event: PointerEvent<HTMLDivElement>) {
    if (drag.current?.pointerId !== event.pointerId) return;
    onChange(movedRange(drag.current, binAt(event.clientX, drag.current.rect)));
    drag.current = null;
    setDraft(null);
    event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function pointerCancel() {
    drag.current = null;
    setDraft(null);
  }

  function keyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!value) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        onChange({start: 48, end: 52});
      }
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      onChange(null);
      return;
    }
    const amount = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1
      : event.key === 'PageDown' ? -4 : event.key === 'PageUp' ? 4 : 0;
    if (!amount) return;
    const mode = (event.target as HTMLElement).dataset.rangeAction as DragMode | undefined;
    if (!mode) return;
    event.preventDefault();
    if (mode === 'start') onChange({start: clamp(value.start + amount, 0, value.end - 1), end: value.end});
    if (mode === 'end') onChange({start: value.start, end: clamp(value.end + amount, value.start + 1, BINS)});
    if (mode === 'move') {
      const start = clamp(value.start + amount, 0, BINS - (value.end - value.start));
      onChange({start, end: start + value.end - value.start});
    }
  }

  return <div className="timeline-range-rail" role={active ? 'group' : 'button'}
    tabIndex={active ? -1 : 0} aria-label={active ? `Selected time range ${label}` : 'Select a time range'}
    onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp}
    onPointerCancel={pointerCancel} onKeyDown={keyDown}>
    {!active && <span className="timeline-range-hint">Drag to filter</span>}
    {active && <div className="timeline-range-window" style={{left:`${active.start / BINS * 100}%`,width:`${(active.end - active.start) / BINS * 100}%`}}>
      <button type="button" className="timeline-range-handle start" data-range-action="start" aria-label={`Resize range start, ${label}`}/>
      <button type="button" className="timeline-range-move" data-range-action="move" aria-label={`Move selected time range, ${label}`}/>
      <button type="button" className="timeline-range-handle end" data-range-action="end" aria-label={`Resize range end, ${label}`}/>
    </div>}
  </div>;
}
