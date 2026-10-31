(function(){var b=document.getElementById("compartilhar"),a=document.getElementById("aviso"),t;
function aviso(m){a.textContent=m;a.classList.add("on");clearTimeout(t);t=setTimeout(function(){a.classList.remove("on")},2200)}
b.addEventListener("click",function(){var u=b.dataset.url;
if(navigator.share){navigator.share({title:document.title,url:u}).catch(function(){});return}
if(navigator.clipboard){navigator.clipboard.writeText(u).then(function(){aviso("Link copiado!")},function(){aviso(u)})}else{aviso(u)}})})();
