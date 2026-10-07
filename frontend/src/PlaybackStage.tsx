import type { ReactNode, Ref } from 'react';
import { Play } from 'lucide-react';

type PlaybackStageProps = {
  playing: boolean;
  src: string;
  poster?: string;
  posterAlt: string;
  onPlay: () => void;
  onError: () => void;
  videoRef?: Ref<HTMLVideoElement>;
  available?: boolean;
  placeholder: ReactNode;
  unavailable?: ReactNode;
  caption?: ReactNode;
  children?: ReactNode;
};

export function PlaybackStage({playing, src, poster, posterAlt, onPlay, onError, videoRef, available = true, placeholder, unavailable, caption, children}: PlaybackStageProps) {
  return <div className="player-stage">
    {!available ? <div className="player-placeholder">{unavailable}</div> : playing ?
      <video ref={videoRef} src={src} poster={poster} controls autoPlay playsInline preload="metadata" onError={onError}/> : <>
        {poster ? <img className="player-poster" src={poster} alt={posterAlt}/> : <div className="player-placeholder">{placeholder}</div>}
        <div className="player-shade"/>
        <button className="large-play" aria-label="Play selected recording" onClick={onPlay}><Play size={28} fill="currentColor"/></button>
        {caption && <div className="stage-caption"><span>{caption}</span></div>}
      </>}
    {children}
  </div>;
}
