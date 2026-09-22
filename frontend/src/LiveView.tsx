import { useEffect, useRef, useState } from 'react';
import Hls from 'hls.js';
import { Camera as CameraIcon, Film, LoaderCircle, Play, RefreshCw, Square, Volume2, VolumeX } from 'lucide-react';
import { api } from './api';
import type { Camera } from './api';

type LiveStatus = {status:string; error:string|null; playlist:string|null};

export function LiveView({cameras,onRecordings}:{cameras:Camera[];onRecordings:(id:string)=>void}) {
  const connected=cameras.filter(c=>c.device_host);
  return <><div className="page-heading"><div className="eyebrow"><span/> HAPPENING NOW</div><h1>Live view</h1><p>Fresh snapshots appear when you open this page. Choose a camera to watch live.</p></div>
    <div className="live-grid">{connected.map(c=><LiveCamera key={c.id} camera={c} onRecordings={()=>onRecordings(c.id)}/>)}</div>
    {!connected.length&&<div className="empty-archive"><CameraIcon size={36}/><h2>No live cameras connected</h2><p>Archived recordings are still available under Recordings.</p></div>}</>;
}

function LiveCamera({camera,onRecordings}:{camera:Camera;onRecordings:()=>void}) {
  const [watching,setWatching]=useState(false),[error,setError]=useState(''),[playlist,setPlaylist]=useState(''),[playing,setPlaying]=useState(false),[muted,setMuted]=useState(true);
  const video=useRef<HTMLVideoElement>(null);
  const [snapshot,setSnapshot]=useState<{url:string|null;captured_at:number|null}>({url:null,captured_at:null});
  const [snapshotLoading,setSnapshotLoading]=useState(false),[snapshotError,setSnapshotError]=useState(''),[snapshotRefresh,setSnapshotRefresh]=useState(0);
  useEffect(()=>{
    if(watching)return;
    let canceled=false,timer:ReturnType<typeof setTimeout>|undefined;
    const deadline=Date.now()+22000;
    setSnapshotLoading(true);setSnapshotError('');
    const poll=async()=>{
      try {
        const result=await api<{status:string;url:string|null;captured_at:number|null;error:string|null}>(`/snapshots/${camera.id}`);
        if(canceled)return;
        if(result.url)setSnapshot({url:result.url,captured_at:result.captured_at});
        if(result.status==='ready'){setSnapshotLoading(false);return;}
        if(result.status==='error'||Date.now()>deadline)throw new Error(result.error||'Snapshot timed out. Try refreshing.');
        timer=setTimeout(poll,700);
      }catch(e){if(!canceled){setSnapshotLoading(false);setSnapshotError((e as Error).message);}}
    };
    api(`/snapshots/${camera.id}`,{method:'POST'}).then(()=>{if(!canceled)poll();}).catch(e=>{if(!canceled){setSnapshotLoading(false);setSnapshotError(e.message);}});
    return()=>{canceled=true;if(timer)clearTimeout(timer);};
  },[camera.id,watching,snapshotRefresh]);
  useEffect(()=>{
    if(!watching)return;
    let canceled=false,viewer='',busy=false;
    setError('');setPlaylist('');setPlaying(false);
    const release=()=>{if(viewer)fetch(`/api/live/${camera.id}/viewers/${viewer}`,{method:'DELETE',keepalive:true}).catch(()=>{});};
    const fail=(message:string)=>{if(!canceled){setError(message);setWatching(false);}};
    api<{viewer:string}>(`/live/${camera.id}`,{method:'POST'}).then(r=>{viewer=r.viewer;if(canceled)release();}).catch(e=>fail(e.message));
    const poll=setInterval(async()=>{
      if(canceled||!viewer||busy)return;
      busy=true;
      try {
        const s=await api<LiveStatus>(`/live/${camera.id}`);
        if(canceled)return;
        if(s.status==='error'||s.status==='stopped')fail(s.error||'Live stream ended. Connect again to resume.');
        else if(s.playlist)setPlaylist(s.playlist);
      }catch(e){fail((e as Error).message);}finally{busy=false;}
    },1000);
    const heartbeat=setInterval(()=>{if(viewer)api(`/live/${camera.id}/viewers/${viewer}`,{method:'POST'}).catch(e=>fail(e.message));},15000);
    // Hidden tabs relinquish their streams rather than continuing camera reads.
    const hidden=()=>{if(document.hidden)setWatching(false);};
    document.addEventListener('visibilitychange',hidden);
    return()=>{canceled=true;clearInterval(poll);clearInterval(heartbeat);document.removeEventListener('visibilitychange',hidden);release();};
  },[watching,camera.id]);
  useEffect(()=>{
    const element=video.current;
    if(!element||!watching||!playlist)return;
    let hls:Hls|undefined;
    if(Hls.isSupported()){
      hls=new Hls({liveSyncDurationCount:2,maxBufferLength:12,backBufferLength:0});
      hls.loadSource(playlist);hls.attachMedia(element);
      hls.on(Hls.Events.MANIFEST_PARSED,()=>{element.play().catch(()=>{});});
      hls.on(Hls.Events.ERROR,(_,data)=>{if(data.fatal){setError('Live playback interrupted. Try connecting again.');setWatching(false);}});
    }else if(element.canPlayType('application/vnd.apple.mpegurl')){element.src=playlist;element.play().catch(()=>{});}
    else {setError('This browser does not support live playback.');setWatching(false);}
    return()=>{hls?.destroy();element.pause();element.removeAttribute('src');element.load();};
  },[playlist,watching]);
  return <section className="live-card"><div className="live-card-heading"><span className="camera-dot" style={{background:camera.color}}/><h2>{camera.name}</h2>{watching&&<span className="live-badge">{playing?'LIVE':'CONNECTING'}</span>}</div>
    <div className="live-screen">{watching?<><video ref={video} muted={muted} playsInline autoPlay controls onPlaying={()=>setPlaying(true)} aria-label={`Live video from ${camera.name}`}/>{!playing&&<div className="live-overlay"><LoaderCircle className="spin"/><span>Connecting to camera…</span></div>}</>:<button className="live-placeholder" onClick={()=>setWatching(true)} aria-label={`Watch ${camera.name} live`}>{snapshot.url?<img className="snapshot-image" src={snapshot.url} alt={`Snapshot from ${camera.name}`} onError={()=>{setSnapshot({url:null,captured_at:null});setSnapshotError('Snapshot could not load. Try refreshing.');}}/>:<CameraIcon size={34}/>}
      <span className="snapshot-play"><Play size={16}/> Watch live</span>
      <span className="snapshot-caption">{snapshotLoading?<><LoaderCircle className="spin" size={13}/> Capturing snapshot…</>:snapshot.captured_at?`Snapshot · ${new Date(snapshot.captured_at*1000).toLocaleTimeString()}`:'Snapshot unavailable'}</span></button>}</div>
    {!watching&&snapshotError&&<p className="error-text live-error" role="status">{snapshotError}</p>}
    {error&&<p className="error-text live-error" role="alert">{error}</p>}
    <div className="live-actions"><button className="secondary" onClick={()=>setWatching(w=>!w)}>{watching?<Square size={14}/>:<Play size={14}/>} {watching?'Stop':'Connect'}</button>{!watching&&<button className="icon-button" disabled={snapshotLoading} aria-label={`Refresh ${camera.name} snapshot`} onClick={()=>setSnapshotRefresh(n=>n+1)}><RefreshCw size={17} className={snapshotLoading?'spin':''}/></button>}{watching&&<button className="icon-button" aria-label={muted?`Unmute ${camera.name}`:`Mute ${camera.name}`} onClick={()=>setMuted(m=>!m)}>{muted?<VolumeX size={17}/>:<Volume2 size={17}/>}</button>}<button className="text-button" onClick={onRecordings}><Film size={15}/> Recordings</button></div>
  </section>;
}
