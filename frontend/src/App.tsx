import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity, ArrowDownToLine, ArrowRight, Bookmark, CalendarDays, Camera as CameraIcon,
  Check, ChevronDown, ChevronLeft, ChevronRight, CircleHelp, Clock3, Database, Film, FolderOpen,
  HardDrive, Info, LayoutGrid, LoaderCircle, LockKeyhole, Maximize2, Menu, Play, RefreshCw,
  ScanLine, Search, ShieldCheck, SlidersHorizontal, Sparkles, Video, X,
  PersonStanding, Car, PawPrint, Bell,
} from 'lucide-react';
import { api, bytes, clock, dayLabel, duration, media } from './api';
import type { Camera, Page, Recording, Status, Timeline } from './api';
const LiveView = lazy(() => import('./LiveView').then(module => ({default: module.LiveView})));

const labels: Record<string,string> = {person:'Person',vehicle:'Vehicle',animal:'Animal',motion:'Motion',timer:'Continuous',doorbell:'Doorbell',package:'Package',unknown:'Unknown',face:'Face',io:'I/O',crying:'Crying',crossline:'Line crossing',intrusion:'Intrusion',linger:'Lingering',forgotten_item:'Forgotten item',taken_item:'Taken item'};
const eventIcon = (kind: string, size = 13) => kind === 'person' ? <PersonStanding size={size}/> : kind === 'vehicle' ? <Car size={size}/> : kind === 'animal' ? <PawPrint size={size}/> : kind === 'doorbell' ? <Bell size={size}/> : <Activity size={size}/>;

export default function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [connectionError, setConnectionError] = useState('');
  useEffect(() => { api<{authenticated:boolean}>('/session').then(s => setAuthenticated(s.authenticated)).catch(e => setConnectionError(e.message)); }, []);
  if (connectionError) return <div className="gate"><Brand/><h1>Unable to reach the archive</h1><p>{connectionError}</p><button className="primary" onClick={() => location.reload()}>Try again</button></div>;
  if (authenticated === null) return <div className="gate"><Brand/><LoaderCircle className="spin"/><p>Opening your archive…</p></div>;
  if (!authenticated) return <Login onLogin={() => setAuthenticated(true)}/>;
  return <Archive/>;
}

function Brand() { return <div className="brand"><span className="brand-mark"><ScanLine size={24}/></span><span>reo<span className="brand-ui">ui</span><small>YOUR RECORDING ARCHIVE</small></span></div>; }

function Login({onLogin}:{onLogin:()=>void}) {
  const [token,setToken] = useState(''); const [error,setError] = useState('');
  return <main className="gate"><Brand/><div className="gate-card"><LockKeyhole size={28}/><h1>Your archive awaits.</h1><p>Enter your archive access token to continue.</p><form onSubmit={e => {e.preventDefault(); api('/login',{method:'POST',body:JSON.stringify({token})}).then(onLogin).catch(e=>setError(e.message));}}>
    <input aria-label="Access token" autoComplete="current-password" type="password" value={token} onChange={e=>setToken(e.target.value)} autoFocus/>
    {error && <p role="alert" className="error-text">{error}</p>}<button className="primary" type="submit">Open archive <ArrowRight size={16}/></button></form></div></main>;
}

function Archive() {
  const initial = useRef(new URLSearchParams(location.search));
  const [view,setView] = useState('archive'); const [mobile,setMobile] = useState(false);
  const [status,setStatus] = useState<Status | null>(null); const [cameras,setCameras] = useState<Camera[]>([]);
  const [camera,setCamera] = useState(initial.current.get('camera') || '');
  const [day,setDay] = useState(initial.current.get('day') || '');
  const [dates,setDates] = useState<{day:string;count:number}[]>([]);
  const [event,setEvent] = useState(initial.current.get('event') || '');
  const [search,setSearch] = useState(''); const [query,setQuery] = useState('');
  const [page,setPage] = useState<Page>({items:[],next_cursor:null}); const [loading,setLoading] = useState(true);
  const [selected,setSelected] = useState<Recording | null>(null);
  const [selectedId,setSelectedId] = useState(initial.current.get('clip') || '');
  const [timeline,setTimeline] = useState<Timeline | null>(null);
  const [error,setError] = useState(''); const [toast,setToast] = useState('');
  const [dayPinned,setDayPinned] = useState(initial.current.has('day')||initial.current.has('clip'));
  const followingLatestDay = useRef(!initial.current.has('day')&&!initial.current.has('clip'));
  const [refresh,setRefresh] = useState(0);
  const timezone = status?.timezone || 'Europe/Tallinn';
  const tell = (message: string) => setToast(message);

  const loadStatus = useCallback(() => {
    Promise.all([api<Status>('/status'),api<Camera[]>('/cameras')]).then(([s,c])=>{
      setStatus(s); setCameras(c); setError('');
    }).catch(e=>setError(e.message));
  },[]);
  useEffect(()=>{loadStatus();const timer=setInterval(loadStatus,5000);return()=>clearInterval(timer);},[loadStatus]);
  useEffect(()=>{const timer=setTimeout(()=>setQuery(search),200);return()=>clearTimeout(timer);},[search]);
  useEffect(()=>{if (!toast) return; const timer=setTimeout(()=>setToast(''),4500);return()=>clearTimeout(timer);},[toast]);
  useEffect(()=>{
    let canceled=false;
    api<{day:string;count:number}[]>(`/dates?${new URLSearchParams(camera?{camera}:{})}`).then(result=>{
      if(canceled)return;
      setDates(result);
      if(followingLatestDay.current && result[0]?.day && result[0].day!==day){
        setDay(result[0].day);setSelectedId('');setSelected(null);
      }
    }).catch(e=>{if(!canceled)setError(e.message);});
    return()=>{canceled=true;};
  },[camera,status?.counts.recordings]);

  const params = useCallback(()=>{
    const p = new URLSearchParams({limit:'36'});
    if (camera) p.set('camera',camera); if(day) p.set('day',day); if(event) p.set('event',event);
    if(query) p.set('q',query); if(view==='bookmarks') p.set('bookmarked','true');
    return p;
  },[camera,day,event,query,view]);

  useEffect(()=>{
    let canceled=false; setLoading(true);
    api<Page>(`/recordings?${params()}`).then(p=>{if (!canceled) {setPage(p);setLoading(false);}}).catch(e=>{if(!canceled){setError(e.message);setLoading(false);}});
    return()=>{canceled=true;};
  },[params,refresh,status?.counts.recordings,status?.counts.previews,status?.counts.event_tagged]);

  useEffect(()=>{
    const id=selectedId||page.items[0]?.id;
    if(!id) {setSelected(null);return;}
    let canceled=false;
    const load=()=>api<Recording>(`/recordings/${id}`).then(r=>{if(!canceled)setSelected(r);}).catch(e=>{if(!canceled)setError(e.message);});
    load();const timer=setInterval(load,5000);return()=>{canceled=true;clearInterval(timer);};
  },[selectedId,page.items[0]?.id,refresh]);

  useEffect(()=>{
    if(!day) {setTimeline(null);return;}
    let canceled=false; const p=new URLSearchParams({day}); if(camera)p.set('camera',camera);
    api<Timeline>(`/timeline?${p}`).then(t=>{if(!canceled)setTimeline(t);}).catch(e=>setError(e.message));
    return()=>{canceled=true;};
  },[day,camera,status?.counts.recordings,status?.counts.previews]);

  useEffect(()=>{
    const p=new URLSearchParams();if(camera)p.set('camera',camera);if(day&&dayPinned)p.set('day',day);if(event)p.set('event',event);if(selectedId)p.set('clip',selectedId);
    history.replaceState(null,'',`${location.pathname}${p.size?'?'+p:''}`);
  },[camera,day,dayPinned,event,selectedId]);

  function changeCamera(id:string) {setCamera(id);setSelectedId('');setSelected(null);setMobile(false);}
  function choose(recording:Recording) {followingLatestDay.current=false;setDayPinned(true);setSelected(recording);setSelectedId(recording.id);}
  function pinDay(value:string) {followingLatestDay.current=false;setDayPinned(true);setDay(value);setSelectedId('');setSelected(null);}
  async function bookmark(recording:Recording) {
    try {await api(`/recordings/${recording.id}`,{method:'PATCH',body:JSON.stringify({bookmarked:!recording.bookmarked})});
      setRefresh(n=>n+1);tell(recording.bookmarked?'Bookmark removed':'Recording bookmarked');
    }catch(e){setError((e as Error).message);}
  }
  function moveDay(direction:number) {
    const index=dates.findIndex(d=>d.day===day);const next=dates[index+direction];if(next)pinDay(next.day);
  }
  const current = selected || page.items[0] || null;
  const currentCamera=cameras.find(c=>c.id===current?.camera_id);
  const activeCamera=cameras.find(c=>c.id===camera);
  const allTypes=Array.from(new Set(page.items.flatMap(r=>r.triggers)));

  return <div className="app-shell">
    {mobile&&<button className="sidebar-scrim" aria-label="Close navigation" onClick={()=>setMobile(false)}/>}
    <aside className={`sidebar ${mobile?'mobile-open':''}`}><Brand/>
      <div className="workspace"><span className="workspace-avatar">S</span><span>Suvila<small>Personal workspace</small></span><ChevronDown size={15}/></div>
      <div className="nav-label">LIBRARY</div>
      <nav aria-label="Main navigation">
        <button className={`nav-item ${view==='archive'&&!camera?'active':''}`} onClick={()=>{setView('archive');changeCamera('');}}><Film size={18}/><span>Recordings</span><span className="nav-count">{status?.counts.recordings.toLocaleString()||'0'}</span></button>
        <div className="camera-list nested-cameras" aria-label="Recording cameras">{cameras.map(c=><button key={c.id} className={`camera-item ${view==='archive'&&camera===c.id?'chosen':''}`} onClick={()=>{changeCamera(c.id);setView('archive');}}><span className="camera-dot" style={{background:c.color}}/><span className="camera-name">{c.name}</span>{view==='archive'&&camera===c.id&&<Check size={14}/>}</button>)}</div>
        {[['live','Live view',Video],['bookmarks','Bookmarks',Bookmark],['status','Indexing & storage',Database]].map(([key,label,Icon])=>{
          const NavIcon=Icon as typeof Film;return <button key={key as string} className={`nav-item ${view===key?'active':''}`} onClick={()=>{setView(key as string);setSelectedId('');setSelected(null);setMobile(false);}}><NavIcon size={18}/><span>{label as string}</span></button>;
        })}
      </nav>
      <div className="sidebar-bottom"><div className="storage-card"><div><HardDrive size={16}/><span>Archive storage</span></div><strong>{bytes(status?.counts.bytes||0)} <span>indexed</span></strong><div className="storage-meter"><span style={{width:`${Math.min(100,(status?.counts.bytes||0)/4e12*100)}%`}}/></div><small>Designed for a 4 TB archive</small></div>
        <div className="local-status"><span className={`status-dot ${status?.worker.online?'online':''}`}/><span>{status?.worker.online?'Background worker active':'Background worker offline'}</span></div>
        <div className="sidebar-foot"><span>ReoUI</span><span>LOCAL · v0.1</span></div>
      </div>
    </aside>
    <main className="main-content"><header className="topbar"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={()=>setMobile(true)}><Menu size={20}/></button><span className="breadcrumb">Suvila <ChevronRight size={13}/> <strong>{view==='live'?'Live view':view==='status'?'Library status':view==='bookmarks'?'Bookmarks':'Recording archive'}</strong></span><div className="topbar-right"><span className="protected"><ShieldCheck size={14}/> Read-only source</span><span className="profile">M</span></div></header>
      <div className="page-content">
      {error&&<div className="notice error-notice" role="alert"><Info size={17}/><span>{error}</span><button className="icon-button" aria-label="Dismiss error" onClick={()=>setError('')}><X size={15}/></button></div>}
      {view==='live'?<Suspense fallback={<div className="loading-panel"><LoaderCircle className="spin"/><p>Opening live view…</p></div>}><LiveView cameras={cameras} onRecordings={id=>{changeCamera(id);setView('archive');}}/></Suspense>:view==='status'?<><StatusView status={status} onScan={()=>api('/index',{method:'POST'}).then(()=>{tell('Archive scan queued');loadStatus();}).catch(e=>setError(e.message))}/><details className="connection-details"><summary>Camera connections & metadata</summary><CamerasView cameras={cameras} onUpdate={()=>{loadStatus();setRefresh(n=>n+1);}} onError={setError}/></details></>:<>
        <div className="page-heading"><div className="eyebrow"><span/> YOUR MOMENTS, ORGANIZED</div><div className="heading-row"><div><h1>{view==='bookmarks'?'Bookmarked moments':'Recording archive'}</h1><p>{view==='bookmarks'?'The recordings you want to come back to.':'A clear view of everything that happened.'}</p></div><button className="secondary scan-button" onClick={()=>api('/index',{method:'POST'}).then(()=>tell('Archive scan queued')).catch(e=>setError(e.message))}><RefreshCw size={15} className={status?.scan.status==='running'?'spin':''}/>{status?.scan.status==='running'?'Indexing…':'Scan archive'}</button></div></div>
        <div className="filters"><div className="date-controls"><button className="icon-button" aria-label="Previous recording day" disabled={!dates.length||dates.findIndex(d=>d.day===day)>=dates.length-1} onClick={()=>moveDay(1)}><ChevronLeft size={17}/></button><label className="date-picker"><CalendarDays size={16}/><span>{dayLabel(day)}</span><input type="date" aria-label="Recording date" value={day} onChange={e=>pinDay(e.target.value)}/><ChevronDown size={13}/></label><button className="icon-button" aria-label="Next recording day" disabled={dates.findIndex(d=>d.day===day)<=0} onClick={()=>moveDay(-1)}><ChevronRight size={17}/></button>{day&&<button className="text-button all-dates" onClick={()=>pinDay('')}>All dates</button>}</div><div className="filter-right"><span className="camera-filter-label"><CameraIcon size={15}/>{activeCamera?.name||'All cameras'}</span><div className="search-box"><Search size={16}/><input placeholder="Search recordings…" aria-label="Search recordings" value={search} onChange={e=>{setSearch(e.target.value);setSelectedId('');setSelected(null);}}/>{search&&<button className="icon-button" aria-label="Clear search" onClick={()=>setSearch('')}><X size={13}/></button>}</div></div></div>
        <div className="event-filters"><span className="filter-caption"><SlidersHorizontal size={14}/> Show</span>{['','person','vehicle','animal','motion'].map(type=><button className={`event-filter ${event===type?'selected':''}`} key={type} onClick={()=>{setEvent(type);setSelectedId('');}}>{type?eventIcon(type,14):<LayoutGrid size={14}/>} {type?labels[type]:'All recordings'}</button>)}<select aria-label="More event filters" value={['','person','vehicle','animal','motion'].includes(event)?'':event} onChange={e=>{setEvent(e.target.value);setSelectedId('');}}><option value="">More events</option>{Array.from(new Set(['doorbell','package','unknown',...allTypes])).filter(k=>!['person','vehicle','animal','motion'].includes(k)).map(k=><option key={k} value={k}>{labels[k]||k}</option>)}</select><span className="event-filters-end">{status?.counts.previews.toLocaleString()||0} previews ready</span></div>
        {!current&&loading?<div className="loading-panel"><LoaderCircle className="spin"/><p>Finding your recordings…</p></div>:!current?<EmptyArchive hasRecordings={!!status?.counts.recordings} source={status?.source.available} pending={status?.scan.status==='running'} clear={()=>{setEvent('');pinDay('');setSearch('');setCamera('');}}/>:
        <><div className="viewer-layout"><Player key={`player-${current.id}`} recording={current} timezone={timezone} onBookmark={()=>bookmark(current)} onPrepared={()=>{setSelectedId(current.id);setRefresh(n=>n+1);}} onError={setError}/><Details key={`details-${current.id}`} recording={current} camera={currentCamera} timezone={timezone} onSave={()=>{setRefresh(n=>n+1);tell('Note saved');}} onError={setError}/></div>
        <div className="timeline-panel"><div className="section-heading"><div><Clock3 size={16}/><h2>Day at a glance</h2><span className="subtle">{day?dayLabel(day):'Choose a date to see recording coverage'}</span></div><div className="timeline-legend"><span><i/>Recording</span><span><i className="event"/>Event tagged</span></div></div>
          {timeline&&timeline.lanes.length>0?<div className="timeline-content"><div className="timeline-hours"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>{timeline.lanes.map(lane=><div className="timeline-lane" key={lane.camera}><span title={cameras.find(c=>c.id===lane.camera)?.name}>{cameras.find(c=>c.id===lane.camera)?.name}</span><div className="timeline-track">{lane.bins.map((count,i)=><button aria-label={`${cameras.find(c=>c.id===lane.camera)?.name}, ${clock(timeline.start+i*timeline.bin_seconds,timezone)}, ${count} recordings`} title={`${clock(timeline.start+i*timeline.bin_seconds,timezone)} · ${count} recordings`} key={i} className={count?'filled':''} style={count?{background:lane.events[i]?'#d8bd85':lane.color,opacity:0.45+Math.min(count,5)/10}:undefined} disabled={!count} onClick={()=>{if(lane.first[i])setSelectedId(lane.first[i]!);}}/>)}</div></div>)}</div>:<div className="timeline-empty">{day?'No timed recordings for this selection.':'Select a recording date above to explore the timeline.'}</div>}
        </div></>}
        {page.items.length>0&&<section className="recording-section"><div className="section-heading"><div><h2>{view==='bookmarks'?'Saved recordings':'Browse recordings'}</h2><span className="count-pill">{page.items.length}{page.next_cursor?'+':''}</span></div><span className="subtle">Newest first <ChevronDown size={13}/></span></div><div className="recording-grid">{page.items.map(r=><RecordingCard key={r.id} recording={r} active={current?.id===r.id} timezone={timezone} onChoose={()=>choose(r)} onBookmark={()=>bookmark(r)}/>)}</div>{page.next_cursor&&<div className="load-more"><button className="secondary" onClick={()=>{const p=params();p.set('cursor',page.next_cursor!);api<Page>(`/recordings?${p}`).then(next=>setPage(old=>({items:[...old.items,...next.items],next_cursor:next.next_cursor}))).catch(e=>setError(e.message));}}>Load more recordings <ChevronDown size={15}/></button></div>}</section>}
      </>}
      <footer className="page-footer"><span><ShieldCheck size={13}/> Your recordings stay on your storage.</span><span>{timezone} <span className="footer-dot">·</span> {status?.counts.recordings.toLocaleString()||0} recordings indexed</span></footer>
      </div>
    </main>
    {toast&&<div className="toast" role="status"><Check size={16}/>{toast}</div>}
  </div>;
}

function EventChips({recording}:{recording:Recording}) {
  return <div className="event-chips">{recording.triggers.length?recording.triggers.map(kind=><span className={`event-chip ${kind}`} key={kind}>{eventIcon(kind)}{labels[kind]||kind}</span>):<span className="event-chip unknown"><CircleHelp size={12}/>Event unknown</span>}</div>;
}

function RecordingCard({recording:r,active,timezone,onChoose,onBookmark}:{recording:Recording;active:boolean;timezone:string;onChoose:()=>void;onBookmark:()=>void}) {
  const [preview,setPreview]=useState(-1);const [imageFailed,setImageFailed]=useState(false);
  return <article className={`recording-card ${active?'is-active':''}`}><button className="card-image" aria-label={`Play ${r.camera_name} at ${clock(r.start,timezone)}`} onClick={onChoose}
    onMouseMove={e=>{if(r.sprite){const box=e.currentTarget.getBoundingClientRect();setPreview(Math.min(r.sprite.timestamps.length-1,Math.floor((e.clientX-box.left)/box.width*r.sprite.timestamps.length)));}}} onMouseLeave={()=>setPreview(-1)}>
    {r.poster&&!imageFailed?<img loading="lazy" src={media(r,'poster')} alt={`${r.camera_name} recording preview`} onError={()=>setImageFailed(true)}/>:<div className="thumbnail-placeholder"><Video size={24}/><span>{r.status==='error'?'Preview unavailable':'Preparing preview'}</span></div>}
    {preview>=0&&r.sprite&&<div className="sprite-preview" style={{backgroundImage:`url(${media(r,'sprite')})`,backgroundSize:`${r.sprite.columns*100}% ${r.sprite.rows*100}%`,backgroundPosition:`${preview%r.sprite.columns/(r.sprite.columns-1)*100}% ${Math.floor(preview/r.sprite.columns)/(r.sprite.rows-1)*100}%`}}/>}
    <span className="card-camera"><span style={{background:r.camera_color}}/>{r.camera_name}</span><span className="card-duration">{preview>=0&&r.sprite?duration(r.sprite.timestamps[preview]):duration(r.duration)}</span><span className="card-play"><Play size={20} fill="currentColor"/></span>{active&&<span className="viewing-badge">Viewing</span>}
  </button><div className="card-info"><div><button className="card-time" onClick={onChoose}>{clock(r.start,timezone,true)}</button><button className={`icon-button bookmark-button ${r.bookmarked?'saved':''}`} aria-label={r.bookmarked?'Remove bookmark':'Bookmark recording'} aria-pressed={r.bookmarked} onClick={onBookmark}><Bookmark size={15} fill={r.bookmarked?'currentColor':'none'}/></button></div><EventChips recording={r}/></div></article>;
}

function Player({recording:r,timezone,onBookmark,onPrepared,onError}:{recording:Recording;timezone:string;onBookmark:()=>void;onPrepared:()=>void;onError:(e:string)=>void}) {
  const video=useRef<HTMLVideoElement>(null);const [playing,setPlaying]=useState(false);const [failed,setFailed]=useState(false);const [original,setOriginal]=useState(false);const [queued,setQueued]=useState(false);
  const proxyJob=r.jobs?.find(j=>j.kind==='proxy');
  const preparationFailed=proxyJob?.status==='error';
  const preparing=!r.proxy&&(queued||proxyJob?.status==='queued'||proxyJob?.status==='running')&&!preparationFailed;
  useEffect(()=>{if(r.proxy){setQueued(false);setFailed(false);setOriginal(false);}},[r.proxy]);
  const prepare=async()=>{try{await api(`/recordings/${r.id}/prepare`,{method:'POST'});setQueued(true);onPrepared();}catch(e){onError((e as Error).message);}};
  function fullscreen(){video.current?.requestFullscreen().catch(()=>{});}
  return <section className="player-panel"><div className="player-stage">{playing?<video ref={video} src={media(r,r.proxy&&!original?'proxy':'original')} poster={r.poster?media(r,'poster'):undefined} controls autoPlay playsInline preload="metadata" onError={()=>setFailed(true)}/>:<>
    {r.poster?<img className="player-poster" src={media(r,'poster')} alt={`${r.camera_name} selected recording`}/>:<div className="player-placeholder"><Video size={44}/><span>{r.status==='error'?'Preview unavailable':'Preview is being prepared'}</span></div>}
    <div className="player-shade"/><button className="large-play" aria-label="Play selected recording" onClick={()=>setPlaying(true)}><Play size={28} fill="currentColor"/></button><div className="stage-caption"><span className="recorded-badge"><span/>RECORDED FOOTAGE</span><strong>{r.camera_name}</strong><span>{clock(r.start,timezone,true)}{r.end?` — ${clock(r.end,timezone,true)}`:''}</span></div></>}
    {!playing&&<span className="quality-badge">{r.height?`${r.height}p`:'Original'} {r.video_codec?.toUpperCase()}</span>}
    {failed&&<div className="playback-error"><Film size={28}/><h3>Prepare this recording for your browser</h3><p>Your browser could not play the original format. A compatible copy keeps the original untouched.</p><button className="primary" disabled={preparing} onClick={prepare}>{preparing?<LoaderCircle className="spin" size={16}/>:<Sparkles size={16}/>} {preparing?'Preparing playback…':'Prepare compatible playback'}</button>{preparationFailed&&<p className="error-text">{proxyJob.error||'Preparation failed. Try again.'}</p>}</div>}
  </div><div className="player-toolbar"><div className="player-title"><span className="camera-dot" style={{background:r.camera_color}}/><strong>{r.camera_name}</strong><span>{duration(r.duration)}</span></div><div className="player-actions"><button className={`icon-button ${r.bookmarked?'saved':''}`} aria-label={r.bookmarked?'Remove selected bookmark':'Bookmark selected recording'} onClick={onBookmark}><Bookmark size={17} fill={r.bookmarked?'currentColor':'none'}/></button><a className="icon-button" aria-label="Download original recording" href={`${media(r,'original')}&download=true`}><ArrowDownToLine size={17}/></a><button className="icon-button" aria-label="Full screen" onClick={fullscreen} disabled={!playing}><Maximize2 size={17}/></button></div></div>
    <div className="playback-options"><span><ShieldCheck size={13}/>{r.proxy&&!original?'Compatible playback copy':'Original recording'}</span>{r.proxy?<button className="text-button" onClick={()=>{setOriginal(!original);setFailed(false);}}>{original?'Use compatible copy':'View original'}</button>:<button className="text-button" disabled={preparing} onClick={prepare}>{preparing?'Preparing compatible copy…':'Prepare compatible copy'}</button>}</div>
  </section>;
}

function Details({recording:r,camera,timezone,onSave,onError}:{recording:Recording;camera:Camera|undefined;timezone:string;onSave:()=>void;onError:(s:string)=>void}) {
  const [tab,setTab]=useState('details'); const [note,setNote]=useState(r.note);const [saving,setSaving]=useState(false);
  const save=async()=>{setSaving(true);try{await api(`/recordings/${r.id}`,{method:'PATCH',body:JSON.stringify({note})});onSave();}catch(e){onError((e as Error).message);}finally{setSaving(false);}};
  const fields=[[r.time_source==='filename'?'Filename time':'Recorded',clock(r.start,timezone,true)],['Duration',duration(r.duration)],['Resolution',r.width&&r.height?`${r.width} × ${r.height}`:'Preparing'],['Video',r.video_codec?.toUpperCase()||'Preparing'],['Audio',r.audio_codec?.toUpperCase()||(r.status==='ready'?'No audio':'Preparing')],['File size',bytes(r.size)]];
  return <aside className="details-panel"><div className="detail-tabs"><button className={tab==='details'?'active':''} onClick={()=>setTab('details')}><Info size={14}/> Recording details</button><button className={tab==='device'?'active':''} onClick={()=>setTab('device')}><CameraIcon size={14}/> Device</button></div>
    <div className="details-body">{tab==='details'?<><div className="detail-label">DETECTED EVENTS</div><EventChips recording={r}/><p className="detail-help">{r.event_match?'Camera-reported events from a recording matched by camera and start time. FTP and camera recordings can have different boundaries; these are not exact event timestamps.':r.triggers_known?'Labels apply to this recording. Exact event timing may be unavailable.':r.event_recovery_status==='camera_history_unavailable'?'The camera returned no recordings for this day. The backup still exists, but its filename has no event flags. The camera copy may have been overwritten or never recorded there.':r.event_recovery_status==='no_reliable_match'?'Camera history was searched, but no unambiguous event match was found for this backup.':r.event_recovery_status==='search_failed'?'The camera history search failed and will be retried. The backup remains available.':'Event recovery is pending. The backup filename does not contain event flags.'}</p><dl className="metadata-list">{fields.map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>{r.time_warning&&<p className="notice">{r.time_warning}</p>}<div className="note-heading"><label htmlFor="recording-note">YOUR NOTE</label><span>Private to this archive</span></div><textarea id="recording-note" placeholder="Add a note about this moment…" value={note} maxLength={5000} onChange={e=>setNote(e.target.value)}/>{note!==r.note&&<button className="secondary note-save" disabled={saving} onClick={save}>{saving?'Saving…':'Save note'}</button>}<details className="raw-details"><summary>Source metadata <ChevronDown size={13}/></summary><p className="file-path">{r.path}</p><p>Timestamp source: {r.time_source}</p>{r.event_match&&<><p>Event source: camera recording matched by time ({r.event_match.offset_seconds.toFixed(1)}s filename offset)</p><p className="file-path">{r.event_match.device_filename}</p><p>Camera recording: {clock(r.event_match.start,timezone,true)} – {clock(r.event_match.end,timezone,true)}</p></>}{r.error&&<p className="error-text">{r.error}</p>}<pre>{JSON.stringify(r.probe||{status:'Media inspection pending'},null,2)}</pre></details></>:<><div className="detail-label">LATEST DEVICE OBSERVATION</div><h3>{camera?.name||r.camera_name}</h3><p className="detail-help">Device settings describe when they were collected, not when this recording was made.</p>{camera?.last_seen&&<p className="observed-at">Collected {new Date(camera.last_seen*1000).toLocaleString('en-GB',{timeZone:timezone})}</p>}{camera?.metadata.host?<><dl className="metadata-list"><div><dt>Model</dt><dd>{camera.metadata.host.GetDevInfo?.DevInfo?.model||'Unknown'}</dd></div><div><dt>Address</dt><dd>{camera.device_host}</dd></div><div><dt>Connection</dt><dd>{camera.status}</dd></div></dl><details className="raw-details"><summary>All collected metadata <ChevronDown size={13}/></summary><pre>{JSON.stringify(camera.metadata,null,2)}</pre></details></>:<div className="device-empty"><CameraIcon size={28}/><p>Connect this archive folder to a camera in the Cameras view to see device metadata.</p></div>}</>}</div>
  </aside>;
}

function EmptyArchive({hasRecordings,source,pending,clear}:{hasRecordings:boolean;source:boolean|null|undefined;pending:boolean;clear:()=>void}) {
  return <div className="empty-archive"><div className="empty-art"><div/><div/><div/><FolderOpen size={39}/></div><span className="eyebrow">{hasRecordings?'A LITTLE TOO SPECIFIC?':'A HOME FOR EVERY MOMENT'}</span><h2>{hasRecordings?'No recordings match these filters':source===false?'Your archive is temporarily unavailable':pending?'Getting your archive ready':'Your recordings will appear here'}</h2><p>{hasRecordings?'Try a different date, camera, or event type.':source===false?'The backup folder is not responding. Existing metadata stays safe, and browsing will be ready when the source reconnects.':'The background worker discovers your recordings and prepares visual previews. Your original files stay untouched.'}</p>{hasRecordings?<button className="primary" onClick={clear}>Clear filters <ArrowRight size={15}/></button>:<div className="empty-steps"><span><span>1</span>Discover files</span><ChevronRight size={14}/><span><span>2</span>Prepare previews</span><ChevronRight size={14}/><span><span>3</span>Explore your archive</span></div>}</div>;
}

function CamerasView({cameras,onUpdate,onError}:{cameras:Camera[];onUpdate:()=>void;onError:(s:string)=>void}) {
  const folders=cameras.filter(c=>!c.device_host);
  return <><div className="page-heading"><div className="eyebrow"><span/> YOUR POINTS OF VIEW</div><h1>Cameras</h1><p>Recording sources and the context behind every moment.</p></div><div className="notice"><ShieldCheck size={18}/><span>Connections collect metadata only. Camera settings and recordings are never changed.</span></div><div className="device-grid">{cameras.map(camera=><section className="device-card" key={camera.id}><div className="device-card-top"><span className="device-icon" style={{color:camera.color}}><CameraIcon size={27}/></span><span className={`connection-pill ${camera.status==='online'?'connected':''}`}><span/>{camera.status==='online'?'Connected':camera.status==='archive'?'Archive folder':camera.status==='pending'?'Awaiting connection':'Unavailable'}</span></div><h2>{camera.name}</h2><p>{camera.metadata.host?.GetDevInfo?.DevInfo?.model||camera.device_host||'Local recording source'}</p><dl className="metadata-list"><div><dt>Recordings</dt><dd>{camera.recordings.toLocaleString()}</dd></div><div><dt>Archive size</dt><dd>{bytes(camera.bytes)}</dd></div><div><dt>Folder</dt><dd>{camera.folder||'Not mapped'}</dd></div><div><dt>Metadata reads</dt><dd>{Object.values(camera.metadata.coverage||{}).filter(v=>v==='collected').length} collected</dd></div></dl>{camera.device_host&&!camera.folder&&folders.length>0&&<label className="mapping-select">Connect archive folder<select aria-label={`Archive folder for ${camera.name}`} defaultValue="" onChange={e=>{if(e.target.value)api(`/cameras/${camera.id}/mapping`,{method:'POST',body:JSON.stringify({archive_camera_id:e.target.value})}).then(onUpdate).catch(e=>onError(e.message));}}><option value="" disabled>Choose a folder…</option>{folders.map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label>}<details className="raw-details"><summary>Metadata and coverage <ChevronDown size={13}/></summary><pre>{JSON.stringify(camera.metadata,null,2)}</pre></details></section>)}</div>{!cameras.length&&<div className="empty-archive"><CameraIcon size={40}/><h2>No cameras discovered yet</h2><p>Start the worker with your local camera configuration, or index an archive folder.</p></div>}</>;
}

function StatusView({status,onScan}:{status:Status|null;onScan:()=>void}) {
  const stats=[['Indexed recordings',status?.counts.recordings.toLocaleString()||'0',Film],['Original recordings',bytes(status?.counts.bytes||0),HardDrive],['Previews ready',status?.counts.previews.toLocaleString()||'0',LayoutGrid],['Awaiting processing',status?.counts.pending.toLocaleString()||'0',Clock3]];
  return <><div className="page-heading"><div className="eyebrow"><span/> WORKING IN THE BACKGROUND</div><div className="heading-row"><div><h1>Indexing & storage</h1><p>A healthy archive, without the maintenance.</p></div><button className="secondary" onClick={onScan}><RefreshCw size={15}/>Scan archive</button></div></div><div className="stats-grid">{stats.map(([label,value,Icon])=>{const StatIcon=Icon as typeof Film;return <div className="stat-card" key={label as string}><StatIcon size={19}/><strong>{value as string}</strong><span>{label as string}</span></div>;})}</div><div className="notice"><Info size={18}/><span><strong>{status?.counts.event_tagged?.toLocaleString()||0} recordings with event labels.</strong> Camera history searched for {status?.event_recovery?.searched_days||0} of {status?.event_recovery?.total_days||0} camera-days; {status?.event_recovery?.failed_days||0} failed searches. Older events may be unavailable after camera storage overwrites them. Uncertain matches stay unknown.</span></div><div className="status-grid"><section className="status-card"><h2><FolderOpen size={18}/> Recording source</h2><p className="file-path">{status?.archive||'Not configured'}</p><dl className="metadata-list"><div><dt>Source</dt><dd>{status?.source.available===true?'Available':status?.source.available===false?'Unavailable':'Not checked yet'}</dd></div><div><dt>Access</dt><dd>Read only</dd></div><div><dt>Latest scan</dt><dd>{status?.scan.status||'Idle'}</dd></div><div><dt>Files seen in scan</dt><dd>{status?.scan.seen?.toLocaleString()||'0'}</dd></div></dl>{status?.scan.error&&<p className="error-text">{status.scan.error}</p>}</section><section className="status-card"><h2><Database size={18}/> Preview & playback cache</h2><strong className="cache-number">{bytes(status?.cache.bytes||0)} <span>of {bytes(status?.cache.budget||0)}</span></strong><div className="storage-meter"><span style={{width:`${Math.min(100,(status?.cache.bytes||0)/(status?.cache.budget||1)*100)}%`}}/></div><p>Thumbnails and compatible playback copies are stored separately. Older playback copies can be rebuilt from the originals.</p><dl className="metadata-list"><div><dt>Worker</dt><dd>{status?.worker.online?'Running':'Offline'}</dd></div><div><dt>Processing errors</dt><dd>{status?.counts.errors||0}</dd></div></dl></section></div><div className="notice"><Info size={18}/><span>Camera metadata is preserved in the catalog. Clearing generated previews does not remove notes, bookmarks, or captured metadata.</span></div></>;
}
