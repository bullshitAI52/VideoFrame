let playbackSheet=null, playbackFrame=null, playbackTarget=0, playbackGeneration=0;
const playerElement=id=>document.getElementById(id);
function frameAt(x,y,width,height,count){
 if(width<=0||height<=0||x<0||y<0||x>=width||y>=height)return null;
 const index=Math.floor(y/height*4)*4+Math.floor(x/width*4);
 return index<count?index:null;
}
function playbackStart(time){return Math.max(0,Number(time)-5)}
function videoTime(time){const n=Math.max(0,Math.floor(time));return `${String(Math.floor(n/3600)).padStart(2,'0')}:${String(Math.floor(n/60)%60).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`}
function stopPlayback(){
 playbackGeneration++;
 const video=playerElement('video-player');video.pause();video.onloadedmetadata=null;video.onerror=null;video.removeAttribute('src');video.load();
 playerElement('playback').hidden=true;
}
function openSheet(sheet){
 stopPlayback();playbackSheet=sheet;playbackFrame=null;
 playerElement('large').src='/media?id='+encodeURIComponent(sheet.id);
 const details=playerElement('details');details.replaceChildren();
 const name=document.createElement('strong');name.textContent=sheet.name;
 const reason=document.createElement('p');reason.textContent=sheet.analysis?.reason||'尚未分析';details.append(name,reason);
 const grid=playerElement('frame-grid');grid.replaceChildren();
 for(let i=0;i<16;i++){
  const button=document.createElement('button');button.type='button';button.disabled=i>=sheet.count;button.className='frame-cell';
  if(sheet.analysis?.frames?.includes(i+1))button.classList.add('frame-hit');
  if(!button.disabled){button.textContent=`${i+1} · ${videoTime(sheet.times[i])}`;button.setAttribute('aria-label',`播放第 ${i+1} 帧，时间 ${videoTime(sheet.times[i])}`);button.addEventListener('click',()=>playFrame(i))}
  grid.append(button);
 }
 playerElement('playback-error').textContent='';
 if(!playerElement('viewer').open)playerElement('viewer').showModal();
}
function playFrame(index){
 if(index===null||!playbackSheet||index<0||index>=playbackSheet.count)return;
 const time=Number(playbackSheet.times[index]);if(!Number.isFinite(time))return;
 playbackFrame=index;playbackTarget=playbackStart(time);
 const video=playerElement('video-player');const generation=++playbackGeneration;
 playerElement('playback').hidden=false;
 playerElement('playback-title').textContent=playbackSheet.name;
 playerElement('playback-position').textContent=`第 ${index+1} 帧 · 视频内时间 ${videoTime(time)} · 从 ${videoTime(playbackTarget)} 开始回看`;
 playerElement('playback-error').textContent='';
 const source='/video?id='+encodeURIComponent(playbackSheet.video_id);
 function start(){
  if(generation!==playbackGeneration)return;
  video.currentTime=Number.isFinite(video.duration)?Math.min(playbackTarget,Math.max(0,video.duration-.05)):playbackTarget;
  video.playbackRate=Number(playerElement('playback-rate').value);
  const result=video.play();if(result)result.catch(()=>{if(generation===playbackGeneration&&!video.error)playerElement('playback-error').textContent='请点击播放器的播放按钮开始回看。'});
 }
 video.onerror=()=>{if(generation===playbackGeneration)playerElement('playback-error').textContent='原视频无法播放：文件可能已移动，或浏览器不支持该编码。请在 Finder 中打开原视频，并按上方时间定位。'};
 if(video.getAttribute('src')!==source){video.pause();video.onloadedmetadata=start;video.src=source;video.load()}
 else if(video.readyState>=1){video.onloadedmetadata=null;start()}
 else video.onloadedmetadata=start;
 playerElement('playback').scrollIntoView({block:'nearest'});
}
if(typeof document!=='undefined'){
 document.addEventListener('DOMContentLoaded',()=>{
  playerElement('viewer').addEventListener('close',stopPlayback);
  playerElement('playback-rate').addEventListener('change',()=>{playerElement('video-player').playbackRate=Number(playerElement('playback-rate').value)});
  playerElement('replay-frame').addEventListener('click',()=>playFrame(playbackFrame));
 });
}
