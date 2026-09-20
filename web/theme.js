'use strict';
/* 主题引导：在首次绘制前同步落地 data-theme，避免暗色/亮色闪烁。
   仅同源外部脚本——页面 CSP 为 default-src 'self'，不允许内联脚本。 */
(function(){
  var KEY='lab-theme';
  var stored=null;
  try{stored=localStorage.getItem(KEY);}catch(e){/* 隐私模式下 localStorage 不可用：降级为跟随系统 */}
  function systemTheme(){
    try{return window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}
    catch(e){return 'light';}
  }
  var theme=(stored==='dark'||stored==='light')?stored:systemTheme();
  document.documentElement.setAttribute('data-theme',theme);
  window.labTheme={
    current:function(){return theme;},
    isStored:function(){return stored==='dark'||stored==='light';},
    set:function(next){
      if(next==='auto'){stored=null;try{localStorage.removeItem(KEY);}catch(e){}theme=systemTheme();}
      else{theme=next==='dark'?'dark':'light';stored=theme;try{localStorage.setItem(KEY,theme);}catch(e){}}
      document.documentElement.setAttribute('data-theme',theme);
      return theme;
    },
    toggle:function(){return this.set(theme==='dark'?'light':'dark');}
  };
  try{
    var mq=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)');
    if(mq){
      var follow=function(e){if(!window.labTheme.isStored()){theme=e.matches?'dark':'light';document.documentElement.setAttribute('data-theme',theme);}};
      if(mq.addEventListener)mq.addEventListener('change',follow);else if(mq.addListener)mq.addListener(follow);
    }
  }catch(e){/* 不支持动态监听的内核：首次落地已生效 */}
})();
