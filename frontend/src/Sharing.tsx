import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, Check, Copy, LoaderCircle, Share2, Video, X } from 'lucide-react';
import { api, clock, duration } from './api';
import { PlaybackStage } from './PlaybackStage';

type ShareLink = {id: string; created_at: number; expires_at: number};
type CreatedShare = {id: string; url: string; expires_at: number};

export function ShareControls({recordingId}: {recordingId: string}) {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLElement>(null);
  const [links, setLinks] = useState<ShareLink[]>([]);
  const [days, setDays] = useState('7');
  const [created, setCreated] = useState<CreatedShare | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const refresh = () => api<ShareLink[]>(`/recordings/${recordingId}/shares`).then(setLinks);
  useEffect(() => { if (open) refresh().catch(e => setError(e.message)); }, [open, recordingId]);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const node = dialog.current;
    node?.querySelector<HTMLElement>('button')?.focus();
    function trap(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); setOpen(false); }
      if (event.key !== 'Tab' || !node) return;
      const items = Array.from(node.querySelectorAll<HTMLElement>('button:not(:disabled), input, select, a[href]'));
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
    document.addEventListener('keydown', trap);
    return () => {document.removeEventListener('keydown', trap); previous?.focus();};
  }, [open]);
  async function create() {
    setBusy(true); setError(''); setCopied(false);
    try {
      const link = await api<CreatedShare>(`/recordings/${recordingId}/shares`, {method: 'POST', body: JSON.stringify({expires_in: Number(days) * 86400})});
      setCreated(link); await refresh();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function revoke(id: string) {
    setBusy(true); setError('');
    try {
      await api(`/recordings/${recordingId}/shares/${id}`, {method: 'DELETE'});
      if (created?.id === id) setCreated(null);
      await refresh();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <>
    <button className="icon-button" aria-label="Share recording" onClick={() => setOpen(true)}><Share2 size={17}/></button>
    {open && <div className="share-backdrop" onClick={() => setOpen(false)}><section ref={dialog} className="share-dialog" role="dialog" aria-modal="true" aria-labelledby="share-title" onClick={e => e.stopPropagation()} onKeyDown={e => {if(e.key === 'Escape') setOpen(false);}}>
      <button className="icon-button share-close" aria-label="Close sharing" onClick={() => setOpen(false)}><X size={18}/></button>
      <h2 id="share-title">Share recording</h2><p>Anyone with the link can watch and download this recording without signing in. Keep the link private.</p>
      <label>Link expires after <select aria-label="Share link expiry" value={days} onChange={e => setDays(e.target.value)}><option value="1">1 day</option><option value="7">7 days</option><option value="30">30 days</option></select></label>
      <button className="primary" disabled={busy} onClick={create}>{busy ? <LoaderCircle className="spin" size={16}/> : <Share2 size={16}/>} Create link</button>
      {created && <div className="created-share"><label>New share link<input aria-label="New share link" readOnly value={created.url} onFocus={e => e.target.select()}/></label><button className="text-button" onClick={() => navigator.clipboard.writeText(created.url).then(() => setCopied(true)).catch(() => setError('Select and copy the link above.'))}>{copied ? <Check size={14}/> : <Copy size={14}/>} {copied ? 'Copied' : 'Copy link'}</button><small>Expires {new Date(created.expires_at * 1000).toLocaleString()}. Copy it now; the secret is shown only once.</small></div>}
      {error && <p role="alert" className="error-text">{error}</p>}
      <h3>Active links</h3>{links.length ? <ul className="share-links">{links.map(link => <li key={link.id}><span>Created {new Date(link.created_at * 1000).toLocaleString()}<small>Expires {new Date(link.expires_at * 1000).toLocaleString()}</small></span><button className="text-button" disabled={busy} onClick={() => revoke(link.id)}>Revoke</button></li>)}</ul> : <p>No active links.</p>}
    </section></div>}
  </>;
}

type SharedRecording = {camera_name: string; start: number | null; duration: number | null; poster: boolean; proxy: boolean; timezone: string; expires_at: number};

export function SharedPlayer({token}: {token: string}) {
  const [recording, setRecording] = useState<SharedRecording | null>(null);
  const [error, setError] = useState('');
  const [failed, setFailed] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [original, setOriginal] = useState(false);
  const base = `/shared/${encodeURIComponent(token)}`;
  const mediaUrl = (kind: string) => `/api${base}/media/${kind}`;
  useEffect(() => { api<SharedRecording>(base).then(setRecording).catch(e => setError(e.message)); }, [base]);
  return <main className="shared-page"><a className="shared-brand" href="/">reo<span>ui</span></a>
    {error ? <div className="gate-card"><h1>Recording unavailable</h1><p role="alert">{error}</p></div> : !recording ? <LoaderCircle className="spin"/> : <>
      <h1>{recording.camera_name}</h1><p>{recording.start ? new Intl.DateTimeFormat('en-GB', {timeZone: recording.timezone, dateStyle: 'long'}).format(recording.start * 1000) : 'Date unknown'} · {clock(recording.start, recording.timezone, true)} · {duration(recording.duration)}</p>
      <section className="player-panel"><PlaybackStage playing={playing} src={mediaUrl(recording.proxy && !original ? 'proxy' : 'original')}
        poster={recording.poster ? mediaUrl('poster') : undefined} posterAlt={`${recording.camera_name} selected recording`}
        onPlay={() => setPlaying(true)} onError={() => setFailed(true)}
        placeholder={<><Video size={44}/><span>Preview unavailable</span></>}
        caption={clock(recording.start, recording.timezone, true)}/>
      <div className="player-toolbar"><span>Shared recording</span><a className="text-button" href={`${mediaUrl('original')}?download=true`}><ArrowDownToLine size={16}/> Download original</a></div></section>
      {failed && <p role="alert" className="error-text">Playback is unavailable. Try downloading the original or ask the owner to prepare a compatible copy.</p>}
      {recording.proxy && <button className="text-button" onClick={() => {setOriginal(!original); setFailed(false);}}>{original ? 'Use compatible copy' : 'View original'}</button>}
      <p className="shared-expiry">Link expires {new Date(recording.expires_at * 1000).toLocaleString()}</p>
    </>}
  </main>;
}
