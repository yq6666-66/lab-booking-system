'use strict';
/* 主题引导：在首次绘制前同步落地 data-theme，避免暗色/亮色闪烁。
   仅同源外部脚本——页面 CSP 为 default-src 'self'，不允许内联脚本。
   切换主题时经 View Transitions API 播放「从切换按钮圆形扩散」的过渡
   （关键帧 vt-reveal 在 style.css，圆心坐标经 --vt-x/--vt-y 自定义属性传入）；
   API 不存在或用户偏好减弱动效时直接落属性，行为与旧版完全一致。 */
(function(){
  var KEY='lab-theme';
  var stored=null;
  try{stored=localStorage.getItem(KEY);}catch(e){/* 隐私模式下 localStorage 不可用：降级为跟随系统 */}
  function systemTheme(){
    try{return window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}
    catch(e){return 'light';}
  }
  function reduceMotion(){
    try{return window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;}
    catch(e){return false;}
  }
  var theme=(stored==='dark'||stored==='light')?stored:systemTheme();
  document.documentElement.setAttribute('data-theme',theme);
  function applyTheme(){document.documentElement.setAttribute('data-theme',theme);}
  function withTransition(){
    if(typeof document.startViewTransition!=='function'||reduceMotion()){applyTheme();return;}
    var btn=document.getElementById('theme-toggle');
    if(btn&&btn.getBoundingClientRect){
      var r=btn.getBoundingClientRect();
      if(r.width||r.height){
        document.documentElement.style.setProperty('--vt-x',Math.round(r.left+r.width/2)+'px');
        document.documentElement.style.setProperty('--vt-y',Math.round(r.top+r.height/2)+'px');
      }
    }
    document.startViewTransition(applyTheme);
  }
  window.labTheme={
    current:function(){return theme;},
    isStored:function(){return stored==='dark'||stored==='light';},
    set:function(next){
      if(next==='auto'){stored=null;try{localStorage.removeItem(KEY);}catch(e){}theme=systemTheme();}
      else{theme=next==='dark'?'dark':'light';stored=theme;try{localStorage.setItem(KEY,theme);}catch(e){}}
      withTransition();
      return theme;
    },
    toggle:function(){return this.set(theme==='dark'?'light':'dark');}
  };
  try{
    var mq=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)');
    if(mq){
      var follow=function(e){if(!window.labTheme.isStored()){theme=e.matches?'dark':'light';withTransition();}};
      if(mq.addEventListener)mq.addEventListener('change',follow);else if(mq.addListener)mq.addListener(follow);
    }
  }catch(e){/* 不支持动态监听的内核：首次落地已生效 */}
})();
